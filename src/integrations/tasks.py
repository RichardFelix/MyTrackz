import requests
from celery import shared_task
from django.contrib.auth import get_user_model

import events
from app.mixins import disable_fetch_releases
from app.models import MediaTypes
from app.templatetags import app_tags
from integrations import helpers
from integrations.imports import anilist, hltb, kitsu, mal, simkl, trakt, yamtrack

ERROR_TITLE = "\n\n\n Couldn't import the following media: \n\n"


def format_media_type_display(count, media_type):
    """Format media type display with proper pluralization."""
    if count == 0:
        return None
    if count == 1:
        return f"{count} {dict(MediaTypes.choices).get(media_type, media_type)}"
    return f"{count} {app_tags.media_type_readable_plural(media_type)}"


@shared_task(name="Import from Trakt")
def import_trakt(username, user_id, mode):
    """Celery task for importing anime and manga data from Trakt."""
    user = get_user_model().objects.get(id=user_id)

    with disable_fetch_releases():
        (
            num_tv_imported,
            num_movie_imported,
            num_anime_imported,
            warning_message,
        ) = trakt.importer(username, user, mode)
    events.tasks.reload_calendar.delay()

    parts = [
        format_media_type_display(num_tv_imported, MediaTypes.TV.value),
        format_media_type_display(num_movie_imported, MediaTypes.MOVIE.value),
        format_media_type_display(num_anime_imported, MediaTypes.ANIME.value),
    ]
    parts = [p for p in parts if p is not None]

    if not parts:
        info_message = "No media was imported."
    else:
        info_message = f"Imported {helpers.join_with_commas_and(parts)}."

    return (
        f"{info_message} {ERROR_TITLE} {warning_message}"
        if warning_message
        else info_message
    )


@shared_task(name="Import from SIMKL")
def import_simkl(username, user_id, mode):
    """Celery task for importing anime and manga data from SIMKL."""
    user = get_user_model().objects.get(id=user_id)
    with disable_fetch_releases():
        num_tv_imported, num_movie_imported, num_anime_imported, warning_message = (
            simkl.importer(username, user, mode)
        )
    events.tasks.reload_calendar.delay()

    parts = [
        format_media_type_display(num_tv_imported, MediaTypes.TV.value),
        format_media_type_display(num_movie_imported, MediaTypes.MOVIE.value),
        format_media_type_display(num_anime_imported, MediaTypes.ANIME.value),
    ]
    parts = [p for p in parts if p is not None]

    if not parts:
        info_message = "No media was imported."
    else:
        info_message = f"Imported {helpers.join_with_commas_and(parts)}."

    return (
        f"{info_message} {ERROR_TITLE} {warning_message}"
        if warning_message
        else info_message
    )


@shared_task(name="Import from MyAnimeList")
def import_mal(username, user_id, mode):
    """Celery task for importing anime and manga data from MyAnimeList."""
    try:
        user = get_user_model().objects.get(id=user_id)
        with disable_fetch_releases():
            num_anime_imported, num_manga_imported = mal.importer(username, user, mode)
        events.tasks.reload_calendar.delay()
    except requests.exceptions.HTTPError as error:
        if error.response.status_code == requests.codes.not_found:
            msg = f"User {username} not found."
            raise ValueError(msg) from error
        raise
    else:
        parts = [
            format_media_type_display(num_anime_imported, MediaTypes.ANIME.value),
            format_media_type_display(num_manga_imported, MediaTypes.MANGA.value),
        ]
        parts = [p for p in parts if p is not None]

        if not parts:
            return "No media was imported."
        return f"Imported {helpers.join_with_commas_and(parts)}."


@shared_task(name="Import from AniList")
def import_anilist(username, user_id, mode):
    """Celery task for importing anime and manga data from AniList."""
    user = get_user_model().objects.get(id=user_id)
    try:
        with disable_fetch_releases():
            num_anime_imported, num_manga_imported, warning_message = anilist.importer(
                username,
                user,
                mode,
            )
        events.tasks.reload_calendar.delay()
    except requests.exceptions.HTTPError as error:
        error_message = error.response.json()["errors"][0].get("message")
        if error_message == "User not found":
            msg = f"User {username} not found."
            raise ValueError(msg) from error
        if error_message == "Private User":
            msg = f"User {username} is private."
            raise ValueError(msg) from error
        raise
    else:
        parts = [
            format_media_type_display(num_anime_imported, MediaTypes.ANIME.value),
            format_media_type_display(num_manga_imported, MediaTypes.MANGA.value),
        ]
        parts = [p for p in parts if p is not None]

        if not parts:
            info_message = "No media was imported."
        else:
            info_message = f"Imported {helpers.join_with_commas_and(parts)}."

        return (
            f"{info_message} {ERROR_TITLE} {warning_message}"
            if warning_message
            else info_message
        )


@shared_task(name="Import from Kitsu")
def import_kitsu(username, user_id, mode):
    """Celery task for importing anime and manga data from Kitsu."""
    user = get_user_model().objects.get(id=user_id)
    with disable_fetch_releases():
        num_anime_imported, num_manga_imported, warning_message = kitsu.importer(
            username,
            user,
            mode,
        )
    events.tasks.reload_calendar.delay()

    parts = [
        format_media_type_display(num_anime_imported, MediaTypes.ANIME.value),
        format_media_type_display(num_manga_imported, MediaTypes.MANGA.value),
    ]
    parts = [p for p in parts if p is not None]

    if not parts:
        info_message = "No media was imported."
    else:
        info_message = f"Imported {helpers.join_with_commas_and(parts)}."

    return (
        f"{info_message} {ERROR_TITLE} {warning_message}"
        if warning_message
        else info_message
    )


@shared_task(name="Import from Yamtrack")
def import_yamtrack(file, user_id, mode):
    """Celery task for importing media data from Yamtrack."""
    try:
        user = get_user_model().objects.get(id=user_id)
        with disable_fetch_releases():
            imported_counts, warning_message = yamtrack.importer(file, user, mode)
        events.tasks.reload_calendar.delay()
    except UnicodeDecodeError as error:
        msg = "Invalid file format. Please upload a CSV file."
        raise ValueError(msg) from error
    except KeyError as error:
        msg = "Error parsing Yamtrack CSV file."
        raise ValueError(msg) from error
    else:
        parts = [
            format_media_type_display(count, media_type)
            for media_type, count in imported_counts.items()
        ]
        parts = [p for p in parts if p is not None]

        if not parts:
            info_message = "No media was imported."
        elif len(parts) > 1:
            info_message = f"Imported {helpers.join_with_commas_and(parts)}."
        else:
            info_message = f"Imported {parts[0]}."

        return (
            f"{info_message} {ERROR_TITLE} {warning_message}"
            if warning_message
            else info_message
        )


@shared_task(name="Import from HowLongToBeat")
def import_hltb(file, user_id, mode):
    """Celery task for importing media data from HowLongToBeat."""
    try:
        user = get_user_model().objects.get(id=user_id)
        with disable_fetch_releases():
            imported_counts, warning_message = hltb.importer(file, user, mode)
        events.tasks.reload_calendar.delay()
    except UnicodeDecodeError as error:
        msg = "Invalid file format. Please upload a CSV file."
        raise ValueError(msg) from error
    except KeyError as error:
        msg = "Error parsing HowLongToBeat CSV file."
        raise ValueError(msg) from error
    else:
        parts = [
            format_media_type_display(count, media_type)
            for media_type, count in imported_counts.items()
        ]
        parts = [p for p in parts if p is not None]

        if not parts:
            info_message = "No media was imported."
        else:
            info_message = f"Imported {helpers.join_with_commas_and(parts)}."

        return (
            f"{info_message} {ERROR_TITLE} {warning_message}"
            if warning_message
            else info_message
        )
