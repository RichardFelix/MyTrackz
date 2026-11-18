import logging
import re

import requests
from django.conf import settings
from django.core.cache import cache

from app import helpers
from app.models import MediaTypes, Sources
from app.providers import services

logger = logging.getLogger(__name__)

base_url = "https://api.mangaupdates.com/v1"


def handle_error(error):
    """Handle MangaUpdates API errors."""
    error_resp = error.response
    status_code = error_resp.status_code

    try:
        error_json = error_resp.json()
    except requests.exceptions.JSONDecodeError as json_error:
        logger.exception("Failed to decode JSON response")
        raise services.ProviderAPIError(
            Sources.MANGAUPDATES.value,
            error,
        ) from json_error

    if status_code == requests.codes.bad_request:
        search_error = error_json["context"].get("search")
        if search_error:
            message = search_error[0]["errors"][0]
            if message == '"" must have a length between 1 and 400':
                return {"results": [], "total_hits": 0}

    raise services.ProviderAPIError(
        Sources.MANGAUPDATES.value,
        error,
    )


def search(query, page):
    """Search for media on MangaUpdates."""
    cache_key = (
        f"search_{Sources.MANGAUPDATES.value}_{MediaTypes.MANGA.value}_{query}_{page}"
    )
    data = cache.get(cache_key)

    if data is None:
        url = f"{base_url}/series/search"
        per_page = 30
        params = {
            "search": query,
            "stype": "title",
            "perpage": per_page,
            "page": page,
        }

        if not settings.MAL_NSFW:
            params["exclude_genre"] = [
                "Adult",
                "Hentai",
                "Doujinshi",
            ]

        try:
            response = services.api_request(
                Sources.MANGAUPDATES.value,
                "POST",
                url,
                params=params,
            )
        except requests.exceptions.HTTPError as error:
            response = handle_error(error)

        results = [
            {
                "media_id": media["record"]["series_id"],
                "source": Sources.MANGAUPDATES.value,
                "media_type": MediaTypes.MANGA.value,
                "title": media["record"]["title"],
                "image": get_image_url(media["record"]),
            }
            for media in response["results"]
        ]

        total_results = response["total_hits"]
        data = helpers.format_search_response(
            page,
            per_page,
            total_results,
            results,
        )

        cache.set(cache_key, data)

    return data


def manga(media_id):
    """Get metadata for a manga from MangaUpdates."""
    cache_key = f"{Sources.MANGAUPDATES.value}_{MediaTypes.MANGA.value}_{media_id}"
    data = cache.get(cache_key)

    if data is None:
        url = f"{base_url}/series/{media_id}"

        try:
            response = services.api_request(Sources.MANGAUPDATES.value, "GET", url)
        except requests.exceptions.HTTPError as error:
            handle_error(error)

        related_manga, recommendations = get_related_and_recommendations(
            response["related_series"],
            response["recommendations"],
        )

        data = {
            "media_id": media_id,
            "source": Sources.MANGAUPDATES.value,
            "source_url": response["url"],
            "media_type": MediaTypes.MANGA.value,
            "title": response["title"],
            "image": get_image_url(response),
            "synopsis": response["description"],
            "max_progress": get_max_progress(response),
            "genres": get_genres(response["genres"]),
            "score": get_score(response["bayesian_rating"]),
            "score_count": response["rating_votes"],
            "details": {
                "format": response["type"],
                "authors": get_authors(response["authors"]),
                "year": response["year"],
                "status_in_country_of_origin": get_status(response["status"]),
                "latest_chapter_translated": response["latest_chapter"],
            },
            "related": {
                "related_manga": related_manga,
                "recommendations": recommendations,
            },
        }

        cache.set(cache_key, data)

    return data


def get_image_url(response):
    """Get the image URL for a media item."""
    # when no image, value from response is null
    url = response["image"]["url"]["original"]
    return url or settings.IMG_NONE


def get_max_progress(response):
    """Get the maximum progress if the media is completed."""
    if response["completed"]:
        return response["latest_chapter"]
    return None


def get_genres(genres):
    """Return the genres for the media."""
    if genres:
        return [item["genre"] for item in genres]
    return None


def get_authors(authors):
    """Get the authors for a media item."""
    if authors:
        return [item["name"] for item in authors]
    return None


def get_status(status):
    """Return the status of the media."""
    # can be null 67117539345
    # e.g berserk 51239621230 needs parsing
    if status:
        pattern = r"(\d+\s+Volumes\s+\([^)]+\))"
        match = re.search(pattern, status)
        if match:
            return match.group(1)
    return status


def get_score(score):
    """Return the score for the media."""
    # can be null 29732162445
    if score:
        return round(score, 1)
    return None


def get_related_and_recommendations(related, recommendations):
    """Return the related and recommended series as media entries.

    Each entry needs its own series lookup for the cover image; all of them
    are fetched in one batch through the shared rate-limited session.
    """
    related = [item for item in related if item["related_series_name"]]
    recommendations = [item for item in recommendations if item["series_name"]]
    entries = [
        (item["related_series_id"], item["related_series_name"]) for item in related
    ] + [(item["series_id"], item["series_name"]) for item in recommendations]

    responses = services.api_request_many(
        Sources.MANGAUPDATES.value,
        [f"{base_url}/series/{series_id}" for series_id, _ in entries],
    )
    series = [
        {
            "source": Sources.MANGAUPDATES.value,
            "media_id": series_id,
            "media_type": MediaTypes.MANGA.value,
            "title": title,
            "image": get_image_url(data),
        }
        if data
        else None
        for (series_id, title), data in zip(entries, responses, strict=True)
    ]
    related_series = series[: len(related)]
    recommended_series = series[len(related) :]
    return (
        [entry for entry in related_series if entry],
        [entry for entry in recommended_series if entry],
    )
