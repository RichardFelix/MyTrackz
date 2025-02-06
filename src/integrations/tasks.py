import requests
from celery import shared_task
from django.contrib.auth import get_user_model

import events
from app.mixins import disable_all_calendar_triggers
from integrations.imports import anilist, kitsu, mal, simkl, trakt, yamtrack

ERROR_TITLE = "\n\n\n Couldn't import the following media: \n\n"


@shared_task(name="Import from Trakt")
def import_trakt(username, user_id, mode):
    """Celery task for importing anime and manga data from Trakt."""
    user = get_user_model().objects.get(id=user_id)

    with disable_all_calendar_triggers():
        (
            num_tv_imported,
            num_movie_imported,
            num_anime_imported,
            warning_message,
        ) = trakt.importer(username, user, mode)
        events.tasks.reload_calendar.delay()

    info_message = (
        f"Imported {num_tv_imported} TV shows, "
        f"{num_movie_imported} movies, "
        f"and {num_anime_imported} anime."
    )
    return (
        f"{info_message} {ERROR_TITLE} {warning_message}"
        if warning_message
        else info_message
    )


@shared_task(name="Import from SIMKL")
def import_simkl(username, user_id, mode):
    """Celery task for importing anime and manga data from SIMKL."""
    user = get_user_model().objects.get(id=user_id)
    with disable_all_calendar_triggers():
        num_tv_imported, num_movie_imported, num_anime_imported, warning_message = (
            simkl.importer(username, user, mode)
        )
        events.tasks.reload_calendar.delay()

    info_message = (
        f"Imported {num_tv_imported} TV shows, "
        f"{num_movie_imported} movies, "
        f"and {num_anime_imported} anime."
    )
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
        with disable_all_calendar_triggers():
            num_anime_imported, num_manga_imported = mal.importer(username, user, mode)
            events.tasks.reload_calendar.delay()
    except requests.exceptions.HTTPError as error:
        if error.response.status_code == requests.codes.not_found:
            msg = f"User {username} not found."
            raise ValueError(msg) from error
        raise
    else:
        return f"Imported {num_anime_imported} anime and {num_manga_imported} manga."


@shared_task(name="Import from AniList")
def import_anilist(username, user_id, mode):
    """Celery task for importing anime and manga data from AniList."""
    user = get_user_model().objects.get(id=user_id)
    try:
        with disable_all_calendar_triggers():
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
        info_message = (
            f"Imported {num_anime_imported} anime and {num_manga_imported} manga."
        )
        return (
            f"{info_message} {ERROR_TITLE} {warning_message}"
            if warning_message
            else info_message
        )


@shared_task(name="Import from Kitsu")
def import_kitsu(username, user_id, mode):
    """Celery task for importing anime and manga data from Kitsu."""
    user = get_user_model().objects.get(id=user_id)
    with disable_all_calendar_triggers():
        num_anime_imported, num_manga_imported, warning_message = kitsu.importer(
            username,
            user,
            mode,
        )
        events.tasks.reload_calendar.delay()

    info_message = (
        f"Imported {num_anime_imported} anime and {num_manga_imported} manga."
    )
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
        with disable_all_calendar_triggers():
            imported_counts = yamtrack.importer(file, user, mode)
            events.tasks.reload_calendar.delay()
    except UnicodeDecodeError as error:
        msg = "Invalid file format. Please upload a CSV file."
        raise ValueError(msg) from error
    except KeyError as error:
        msg = "Error parsing Yamtrack CSV file."
        raise ValueError(msg) from error
    else:
        imported_summary_list = [
            f"{count} TV shows" if media_type == "tv" else f"{count} {media_type}s"
            for media_type, count in imported_counts.items()
        ]
        imported_summary = (
            ", ".join(imported_summary_list[:-1]) + " and " + imported_summary_list[-1]
        )
        return f"Imported {imported_summary}."
