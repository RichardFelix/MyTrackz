import datetime
import logging

import requests
from django.apps import apps
from django.conf import settings

import app
from integrations import helpers

logger = logging.getLogger(__name__)

SIMKL_API_BASE_URL = "https://api.simkl.com"


def get_token(request):
    """View for getting the SIMKL OAuth2 token."""
    domain = request.get_host()
    scheme = request.scheme
    code = request.GET["code"]
    url = f"{SIMKL_API_BASE_URL}/oauth/token"

    headers = {
        "Content-Type": "application/json",
    }

    params = {
        "client_id": settings.SIMKL_ID,
        "client_secret": settings.SIMKL_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": f"{scheme}://{domain}",
    }

    try:
        request = app.providers.services.api_request(
            "SIMKL",
            "POST",
            url,
            headers=headers,
            params=params,
        )
    except requests.exceptions.HTTPError as error:
        if error.response.status_code == requests.codes.unauthorized:
            msg = "Invalid SIMKL secret key."
            raise ValueError(msg) from error
        raise

    return request["access_token"]


def importer(token, user, mode):
    """Import tv shows, movies and anime from SIMKL."""
    logger.info("Starting SIMKL import with mode %s", mode)

    data = get_user_list(token)

    if not data:
        return 0, 0, 0, ""

    bulk_media = {"tv": [], "movie": [], "anime": [], "season": [], "episode": []}
    warnings = []

    # Process all media types
    process_tv_list(data["shows"], user, bulk_media, warnings)
    process_movie_list(data["movies"], user, bulk_media, warnings)
    process_anime_list(data["anime"], user, bulk_media, warnings)

    # Import using bulk operations
    imported_counts = {}
    for media_type, bulk_list in bulk_media.items():
        logger.info("Bulk creating %d %s", len(bulk_list), media_type)

        if bulk_list:
            imported_counts[media_type] = helpers.bulk_chunk_import(
                bulk_list,
                apps.get_model(app_label="app", model_name=media_type),
                user,
                mode,
            )

    return (
        imported_counts.get("tv", 0),
        imported_counts.get("movie", 0),
        imported_counts.get("anime", 0),
        "\n".join(warnings),
    )


def get_user_list(token):
    """Get the user's list from SIMKL."""
    url = f"{SIMKL_API_BASE_URL}/sync/all-items/"
    headers = {
        "Authorization": f"Bearer: {token}",
        "simkl-api-key": settings.SIMKL_ID,
    }
    params = {
        "extended": "full",
        "episode_watched_at": "yes",
    }

    return app.providers.services.api_request(
        "SIMKL",
        "GET",
        url,
        headers=headers,
        params=params,
    )


def process_tv_list(tv_list, user, bulk_media, warnings):
    """Process TV list from SIMKL and prepare for bulk creation."""
    logger.info("Processing tv shows")

    for tv in tv_list:
        title = tv["show"]["title"]
        tmdb_id = tv["show"]["ids"]["tmdb"]
        tv_status = get_status(tv["status"])

        try:
            season_numbers = [season["number"] for season in tv["seasons"]]
        except KeyError:
            warnings.append(f"{title}: It doesn't have data on episodes viewed.")
            continue

        try:
            metadata = app.providers.tmdb.tv_with_seasons(tmdb_id, season_numbers)
        except requests.exceptions.HTTPError as error:
            if error.response.status_code == requests.codes.not_found:
                warnings.append(
                    f"{title}: Couldn't fetch metadata from TMDB ({tmdb_id})",
                )
                continue
            raise

        tv_item, _ = app.models.Item.objects.get_or_create(
            media_id=tmdb_id,
            source="tmdb",
            media_type="tv",
            defaults={
                "title": metadata["title"],
                "image": metadata["image"],
            },
        )

        tv_instance = app.models.TV(
            item=tv_item,
            user=user,
            status=tv_status,
            score=tv["user_rating"],
        )
        bulk_media["tv"].append(tv_instance)

        # Process seasons and episodes
        process_seasons_and_episodes(
            tv,
            tv_instance,
            metadata,
            season_numbers,
            user,
            bulk_media,
        )
    logger.info("Processed %d tv shows", len(tv_list))

    helpers.update_season_references(bulk_media["season"], user)
    helpers.update_episode_references(bulk_media["episode"], user)


def process_seasons_and_episodes(
    tv,
    tv_instance,
    metadata,
    season_numbers,
    user,
    bulk_media,
):
    """Process seasons and episodes for bulk creation."""
    tmdb_id = tv["show"]["ids"]["tmdb"]

    for season in tv["seasons"]:
        season_number = season["number"]
        episodes = season["episodes"]
        season_metadata = metadata[f"season/{season_number}"]

        season_item, _ = app.models.Item.objects.get_or_create(
            media_id=tmdb_id,
            source="tmdb",
            media_type="season",
            season_number=season_number,
            defaults={
                "title": metadata["title"],
                "image": season_metadata["image"],
            },
        )

        # Prepare Season instance for bulk creation
        season_status = (
            app.models.Media.Status.COMPLETED.value
            if season_number != season_numbers[-1]
            else tv_instance.status
        )

        season_instance = app.models.Season(
            item=season_item,
            user=user,
            related_tv=tv_instance,
            status=season_status,
        )
        bulk_media["season"].append(season_instance)

        # Process episodes
        for episode in episodes:
            ep_img = get_episode_image(episode, season_number, metadata)
            episode_item, _ = app.models.Item.objects.get_or_create(
                media_id=tmdb_id,
                source="tmdb",
                media_type="episode",
                season_number=season_number,
                episode_number=episode["number"],
                defaults={
                    "title": metadata["title"],
                    "image": ep_img,
                },
            )

            episode_instance = app.models.Episode(
                item=episode_item,
                related_season=season_instance,
                end_date=get_date(episode["watched_at"]),
            )
            bulk_media["episode"].append(episode_instance)


def get_episode_image(episode, season_number, metadata):
    """Get the image for the episode."""
    for episode_metadata in metadata[f"season/{season_number}"]["episodes"]:
        if episode_metadata["episode_number"] == episode["number"]:
            return f"https://image.tmdb.org/t/p/w500{episode_metadata['still_path']}"
    return settings.IMG_NONE


def process_movie_list(movie_list, user, bulk_media, warnings):
    """Process movie list from SIMKL and prepare for bulk creation."""
    logger.info("Processing movies")

    for movie in movie_list:
        title = movie["movie"]["title"]
        tmdb_id = movie["movie"]["ids"]["tmdb"]
        movie_status = get_status(movie["status"])

        try:
            metadata = app.providers.tmdb.movie(tmdb_id)
        except requests.exceptions.HTTPError as error:
            if error.response.status_code == requests.codes.not_found:
                warnings.append(
                    f"{title}: Couldn't fetch metadata from TMDB ({tmdb_id})",
                )
                continue
            raise

        movie_item, _ = app.models.Item.objects.get_or_create(
            media_id=tmdb_id,
            source="tmdb",
            media_type="movie",
            defaults={
                "title": metadata["title"],
                "image": metadata["image"],
            },
        )

        movie_instance = app.models.Movie(
            item=movie_item,
            user=user,
            status=movie_status,
            score=movie["user_rating"],
            start_date=get_date(movie["last_watched_at"]),
            end_date=get_date(movie["last_watched_at"]),
        )
        bulk_media["movie"].append(movie_instance)

    logger.info("Processed %d movies", len(movie_list))


def process_anime_list(anime_list, user, bulk_media, warnings):
    """Process anime list from SIMKL and prepare for bulk creation."""
    logger.info("Processing anime")

    for anime in anime_list:
        title = anime["show"]["title"]
        mal_id = anime["show"]["ids"]["mal"]
        anime_status = get_status(anime["status"])

        try:
            metadata = app.providers.mal.anime(mal_id)
        except requests.exceptions.HTTPError as error:
            if error.response.status_code == requests.codes.not_found:
                warnings.append(f"{title}: Couldn't fetch metadata from MAL ({mal_id})")
                continue
            raise

        anime_item, _ = app.models.Item.objects.get_or_create(
            media_id=mal_id,
            source="mal",
            media_type="anime",
            defaults={
                "title": metadata["title"],
                "image": metadata["image"],
            },
        )

        # Determine end date based on status
        end_date = (
            get_date(anime["last_watched_at"])
            if anime_status == app.models.Media.Status.COMPLETED.value
            else None
        )

        anime_instance = app.models.Anime(
            item=anime_item,
            user=user,
            status=anime_status,
            score=anime["user_rating"],
            progress=anime["watched_episodes_count"],
            start_date=get_date(anime["last_watched_at"]),
            end_date=end_date,
        )
        bulk_media["anime"].append(anime_instance)

    logger.info("Processed %d anime", len(anime_list))


def get_status(status):
    """Map SIMKL status to internal status."""
    status_mapping = {
        "completed": app.models.Media.Status.COMPLETED.value,
        "watching": app.models.Media.Status.IN_PROGRESS.value,
        "plantowatch": app.models.Media.Status.PLANNING.value,
        "hold": app.models.Media.Status.PAUSED.value,
        "dropped": app.models.Media.Status.DROPPED.value,
    }

    return status_mapping.get(status, app.models.Media.Status.IN_PROGRESS.value)


def get_date(date):
    """Convert the date from Trakt to a date object."""
    if date:
        return (
            datetime.datetime.strptime(date, "%Y-%m-%dT%H:%M:%SZ")
            .replace(tzinfo=datetime.UTC)
            .astimezone(settings.TZ)
            .date()
        )
    return None
