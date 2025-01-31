import datetime
import json
import logging
from pathlib import Path

from django.apps import apps
from django.conf import settings

import app
from app.models import Item, Media
from integrations import helpers

logger = logging.getLogger(__name__)
KITSU_API_BASE_URL = "https://kitsu.io/api/edge"
KITSU_PAGE_LIMIT = 500


def get_kitsu_id(username):
    """Get the user ID from Kitsu."""
    url = f"{KITSU_API_BASE_URL}/users"
    response = app.providers.services.api_request(
        "KITSU",
        "GET",
        url,
        params={"filter[name]": username},
    )

    if not response["data"]:
        msg = f"User {username} not found."
        raise ValueError(msg)
    if len(response["data"]) > 1:
        msg = (
            f"Multiple users found for {username}, please use your user ID. "
            "User IDs can be found in the URL when viewing your Kitsu profile."
        )
        raise ValueError(msg)

    return response["data"][0]["id"]


def importer(kitsu_id, user, mode):
    """Import anime and manga ratings from Kitsu by user ID."""
    # Check if given ID is a username
    if not kitsu_id.isdigit():
        kitsu_id = get_kitsu_id(kitsu_id)

    anime_response = get_media_response(kitsu_id, "anime")
    num_anime_imported, anime_warnings = import_media(
        anime_response,
        "anime",
        user,
        mode,
    )

    manga_response = get_media_response(kitsu_id, "manga")
    num_manga_imported, manga_warning = import_media(
        manga_response,
        "manga",
        user,
        mode,
    )

    warning_messages = anime_warnings + manga_warning
    return num_anime_imported, num_manga_imported, "\n".join(warning_messages)


def get_media_response(kitsu_id, media_type):
    """Get all media entries for a user from Kitsu."""
    logger.info("Fetching %s from Kitsu", media_type)
    url = f"{KITSU_API_BASE_URL}/library-entries"
    params = {
        "filter[user_id]": kitsu_id,
        "filter[kind]": media_type,
        "include": f"{media_type},{media_type}.mappings",
        f"fields[{media_type}]": "canonicalTitle,posterImage,mappings",
        "fields[mappings]": "externalSite,externalId",
        "page[limit]": KITSU_PAGE_LIMIT,
    }

    all_data = {"entries": [], "included": []}

    while url:
        data = app.providers.services.api_request("KITSU", "GET", url, params=params)
        all_data["entries"].extend(data["data"])
        all_data["included"].extend(data.get("included", []))
        url = data["links"].get("next")
        params = {}  # Clear params for subsequent requests
    return all_data


def import_media(response, media_type, user, mode):
    """Import media from Kitsu and return the number of items imported."""
    logger.info("Importing %s from Kitsu", media_type)

    model = apps.get_model(app_label="app", model_name=media_type)
    media_lookup = {
        item["id"]: item for item in response["included"] if item["type"] == media_type
    }
    mapping_lookup = {
        item["id"]: item for item in response["included"] if item["type"] == "mappings"
    }

    bulk_data = []
    warnings = []

    current_file_dir = Path(__file__).resolve().parent
    json_file_path = current_file_dir / "data" / "kitsu-mu-mapping.json"
    with json_file_path.open() as f:
        kitsu_mu_mapping = json.load(f)
        for entry in response["entries"]:
            try:
                instance = process_entry(
                    entry,
                    media_type,
                    media_lookup,
                    mapping_lookup,
                    kitsu_mu_mapping,
                    user,
                )
            except ValueError as e:
                warnings.append(str(e))
            else:
                bulk_data.append(instance)

    num_imported = helpers.bulk_chunk_import(bulk_data, model, user, mode)

    logger.info("Imported %s %s", num_imported, media_type)

    return num_imported, warnings


def process_entry(
    entry,
    media_type,
    media_lookup,
    mapping_lookup,
    kitsu_mu_mapping,
    user,
):
    """Process a single entry and return the model instance."""
    attributes = entry["attributes"]
    kitsu_id = entry["relationships"][media_type]["data"]["id"]
    kitsu_metadata = media_lookup[kitsu_id]

    item = create_or_get_item(
        media_type,
        kitsu_metadata,
        mapping_lookup,
        kitsu_mu_mapping,
    )
    model = apps.get_model(app_label="app", model_name=media_type)

    instance = model(
        item=item,
        user=user,
        score=get_rating(attributes["ratingTwenty"]),
        progress=attributes["progress"],
        status=get_status(attributes["status"]),
        repeats=attributes["reconsumeCount"],
        start_date=get_date(attributes["startedAt"]),
        end_date=get_date(attributes["finishedAt"]),
        notes=attributes["notes"] or "",  # sometimes returns None instead of ""
    )

    if attributes["reconsuming"]:
        instance.status = Media.Status.REPEATING.value

    return instance


def create_or_get_item(media_type, kitsu_metadata, mapping_lookup, kitsu_mu_mapping):
    """Create or get an Item instance."""
    sites = [
        f"myanimelist/{media_type}",
        "mangaupdates",
        "thetvdb/season",
        "thetvdb",
        "thetvdb/series",
    ]

    mappings = {
        mapping["attributes"]["externalSite"]: mapping["attributes"]["externalId"]
        for mapping_ref in kitsu_metadata["relationships"]["mappings"]["data"]
        for mapping in [mapping_lookup[mapping_ref["id"]]]
    }

    media_id = None
    for site in sites:
        if site not in mappings:
            continue

        external_id = mappings[site]
        if site == f"myanimelist/{media_type}":
            media_id = int(external_id)
            season_number = None
            source = "mal"
            break

        if site == "mangaupdates":
            # if its int, its an old MU ID
            if external_id.isdigit():
                # get the base36 encoded ID
                try:
                    external_id = kitsu_mu_mapping[external_id]
                except KeyError:  # ID not found in mapping
                    continue

            # decode the base36 encoded ID
            media_id = int(external_id, 36)
            media_type = "manga"
            season_number = None
            source = "mangaupdates"
            break

    if not media_id:
        media_title = kitsu_metadata["attributes"]["canonicalTitle"]
        msg = f"{media_title}: No valid external ID found."
        raise ValueError(msg)

    image_url = get_image_url(kitsu_metadata)

    return Item.objects.get_or_create(
        media_id=media_id,
        source=source,
        media_type=media_type,
        season_number=season_number,
        defaults={
            "title": kitsu_metadata["attributes"]["canonicalTitle"],
            "image": image_url,
        },
    )[0]


def get_image_url(media):
    """Get the image URL for a media item."""
    try:
        return media["attributes"]["posterImage"]["medium"]
    except KeyError:
        try:
            return media["attributes"]["posterImage"]["original"]
        except KeyError:
            return settings.IMG_NONE


def get_rating(rating):
    """Convert the rating from Kitsu to a 0-10 scale."""
    if rating:
        return rating / 2
    return None


def get_date(date):
    """Convert the date from Kitsu to a date object."""
    if date:
        return (
            datetime.datetime.strptime(date, "%Y-%m-%dT%H:%M:%S.%fZ")
            .replace(tzinfo=datetime.UTC)
            .astimezone(settings.TZ)
            .date()
        )
    return None


def get_status(status):
    """Convert the status from Kitsu to the status used in the app."""
    status_mapping = {
        "completed": Media.Status.COMPLETED.value,
        "current": Media.Status.IN_PROGRESS.value,
        "planned": Media.Status.PLANNING.value,
        "on_hold": Media.Status.PAUSED.value,
        "dropped": Media.Status.DROPPED.value,
    }
    return status_mapping[status]
