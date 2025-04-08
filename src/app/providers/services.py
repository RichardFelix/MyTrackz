import logging
import time
from functools import wraps

import requests
from django.conf import settings
from pyrate_limiter import RedisBucket
from redis import ConnectionPool
from requests_ratelimiter import LimiterAdapter, LimiterSession

from app.providers import comicvine, igdb, mal, mangaupdates, manual, openlibrary, tmdb

logger = logging.getLogger(__name__)


def get_redis_connection():
    """Return a Redis connection pool."""
    if settings.TESTING:
        import fakeredis

        return fakeredis.FakeStrictRedis().connection_pool
    return ConnectionPool.from_url(settings.REDIS_URL)


redis_pool = get_redis_connection()

session = LimiterSession(
    per_second=5,
    bucket_class=RedisBucket,
    bucket_kwargs={"redis_pool": redis_pool, "bucket_name": "api"},
)
session.mount(
    "https://api.myanimelist.net/v2",
    LimiterAdapter(per_minute=30),
)
session.mount(
    "https://graphql.anilist.co",
    LimiterAdapter(per_minute=85),
)
session.mount(
    "https://api.igdb.com/v4",
    LimiterAdapter(per_second=3),
)
session.mount(
    "https://api.tvmaze.com",
    LimiterAdapter(per_second=2),
)
session.mount(
    "https://comicvine.gamespot.com/api",
    LimiterAdapter(per_hour=190),
)
session.mount(
    "https://openlibrary.org",
    LimiterAdapter(per_minute=20),
)


def retry_on_error(delay=1):
    """Retry a function if it raises a RequestException."""

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except requests.exceptions.RequestException:
                msg = f"Request failed. Retrying in {delay} seconds."
                logger.warning(msg)
                time.sleep(delay)

                # Retry the function
                return func(*args, **kwargs)

        return wrapper

    return decorator


@retry_on_error(delay=1)
def api_request(provider, method, url, params=None, data=None, headers=None):
    """Make a request to the API and return the response as a dictionary."""
    try:
        request_kwargs = {
            "url": url,
            "headers": headers,
            "timeout": settings.REQUEST_TIMEOUT,
        }

        if method == "GET":
            request_kwargs["params"] = params
            request_func = session.get
        elif method == "POST":
            request_kwargs["data"] = data
            request_kwargs["json"] = params
            request_func = session.post

        response = request_func(**request_kwargs)
        response.raise_for_status()
        json_response = response.json()

    except requests.exceptions.HTTPError as error:
        json_response = request_error_handling(
            error,
            provider,
            method,
            url,
            params,
            data,
            headers,
        )

    return json_response


def request_error_handling(error, provider, method, url, params, data, headers):
    """Handle errors when making a request to the API."""
    error_resp = error.response
    status_code = error_resp.status_code

    # handle rate limiting
    if status_code == requests.codes.too_many_requests:
        seconds_to_wait = int(error_resp.headers["Retry-After"])
        logger.warning("Rate limited, waiting %s seconds", seconds_to_wait)
        time.sleep(seconds_to_wait + 3)
        logger.info("Retrying request")
        return api_request(
            provider,
            method,
            url,
            params=params,
            data=data,
            headers=headers,
        )

    # Delegate to provider-specific error handlers
    if provider == "IGDB":
        result = igdb.handle_error(error, provider, method, url, params, data, headers)
        if result and result.get("retry"):
            return api_request(**{k: v for k, v in result.items() if k != "retry"})
    elif provider == "TMDB":
        result = tmdb.handle_error(error)
    elif provider == "MAL":
        result = mal.handle_error(error)
    elif provider == "ComicVine":
        result = comicvine.handle_error(error)

    raise error  # re-raise the error if it's not handled


def get_media_metadata(
    media_type,
    media_id,
    source,
    season_numbers=None,
    episode_number=None,
):
    """Return the metadata for the selected media."""
    if source == "manual":
        if media_type == "season":
            return manual.season(media_id, season_numbers[0])
        if media_type == "episode":
            return manual.episode(media_id, season_numbers[0], episode_number)
        if media_type == "tv_with_seasons":
            media_type = "tv"
        return manual.metadata(media_id, media_type)

    metadata_retrievers = {
        "anime": lambda: mal.anime(media_id),
        "manga": lambda: mangaupdates.manga(media_id)
        if source == "mangaupdates"
        else mal.manga(media_id),
        "tv": lambda: tmdb.tv(media_id),
        "tv_with_seasons": lambda: tmdb.tv_with_seasons(media_id, season_numbers),
        "season": lambda: tmdb.tv_with_seasons(media_id, season_numbers)[
            f"season/{season_numbers[0]}"
        ],
        "episode": lambda: tmdb.episode(media_id, season_numbers[0], episode_number),
        "movie": lambda: tmdb.movie(media_id),
        "game": lambda: igdb.game(media_id),
        "book": lambda: openlibrary.book(media_id),
        "comic": lambda: comicvine.comic(media_id),
    }
    return metadata_retrievers[media_type]()


def search(media_type, query, source=None):
    """Search for media based on the query and return the results."""
    if media_type == "manga":
        if source == "mangaupdates":
            query_list = mangaupdates.search(query)
        else:
            query_list = mal.search(media_type, query)
    elif media_type in "anime":
        query_list = mal.search(media_type, query)
    elif media_type in ("tv", "movie"):
        query_list = tmdb.search(media_type, query)
    elif media_type == "game":
        query_list = igdb.search(query)
    elif media_type == "book":
        query_list = openlibrary.search(query)
    elif media_type == "comic":
        query_list = comicvine.search(query)

    return query_list
