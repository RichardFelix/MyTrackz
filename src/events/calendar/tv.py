import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from django.core.cache import cache
from django.db import transaction
from django.db.models import Prefetch, Q
from django.utils import timezone
from simple_history.utils import bulk_create_with_history, bulk_update_with_history

from app.models import TV, Item, MediaTypes, Season, Status
from app.providers import services, tmdb
from events.models import Event

from .helpers import date_parser

logger = logging.getLogger(__name__)


def _media_id_scope(keys, prefix=""):
    """Build a compact source/media ID query for a collection of pairs."""
    media_ids_by_source = {}
    for source, media_id in keys:
        media_ids_by_source.setdefault(source, set()).add(media_id)

    scope = Q()
    for source, media_ids in media_ids_by_source.items():
        scope |= Q(
            **{
                f"{prefix}source": source,
                f"{prefix}media_id__in": media_ids,
            },
        )
    return scope


def process_tv(tv_item, events_bulk):
    """Process TV item and create events for all seasons and episodes."""
    logger.info("Processing TV show: %s", tv_item)

    try:
        seasons_to_process = get_seasons_to_process(tv_item)

        if not seasons_to_process:
            logger.info("%s - No seasons need processing", tv_item)
            return

        process_tv_seasons(
            tv_item,
            seasons_to_process,
            events_bulk,
        )

    except services.ProviderAPIError:
        logger.warning(
            "Failed to fetch metadata for %s",
            tv_item,
        )
    except Exception:
        logger.exception("Error processing %s", tv_item)


def get_seasons_to_process(tv_item):
    """Identify which seasons of a TV show need to be processed."""
    tv_metadata = tmdb.tv(tv_item.media_id)

    if not tv_metadata.get("related", {}).get("seasons"):
        logger.warning("No seasons found for TV show: %s", tv_item)
        return []

    season_numbers = [
        season["season_number"] for season in tv_metadata["related"]["seasons"]
    ]

    if not season_numbers:
        logger.warning("No valid seasons found for TV show: %s", tv_item)
        return []

    next_episode_season = tv_metadata.get("next_episode_season")

    existing_season_events = Event.objects.filter(
        item__media_id=tv_item.media_id,
        item__source=tv_item.source,
        item__media_type=MediaTypes.SEASON.value,
    ).select_related("item")

    seasons_with_events = {event.item.season_number for event in existing_season_events}
    seasons_to_process = [
        season_num
        for season_num in season_numbers
        if season_num not in seasons_with_events
        or (next_episode_season and season_num >= next_episode_season)
    ]

    if not seasons_to_process:
        return []

    logger.info(
        "%s - Processing %d seasons (Next episode season: %s)",
        tv_item,
        len(seasons_to_process),
        next_episode_season,
    )

    return seasons_to_process


def process_tv_seasons(tv_item, seasons_to_process, events_bulk):
    """Process specific seasons of a TV show."""
    process_seasons_data = tmdb.tv_with_seasons(
        tv_item.media_id,
        seasons_to_process,
    )
    for season_number in seasons_to_process:
        season_key = f"season/{season_number}"
        if season_key not in process_seasons_data:
            logger.warning(
                "Season %s data not found for %s",
                season_number,
                tv_item,
            )
            continue

        season_metadata = process_seasons_data[season_key]

        season_item, _ = Item.objects.get_or_create(
            media_id=tv_item.media_id,
            source=tv_item.source,
            media_type=MediaTypes.SEASON.value,
            season_number=season_number,
            defaults={
                "title": tv_item.title,
                "image": season_metadata["image"],
            },
        )

        process_season_episodes(season_item, season_metadata, events_bulk)


def _get_upcoming_season_items(tv_items):
    """Return future regular season items grouped by TV source and media ID."""
    upcoming_season_items = Item.objects.filter(
        media_type=MediaTypes.SEASON.value,
        season_number__gt=0,
        event__datetime__gte=timezone.now(),
    ).distinct()

    if tv_items is not None:
        tv_keys = {
            (item.source, item.media_id)
            for item in tv_items
            if item.media_type == MediaTypes.TV.value
        }
        if not tv_keys:
            return {}

        upcoming_season_items = upcoming_season_items.filter(
            _media_id_scope(tv_keys),
        )

    season_items_by_tv = {}
    for season_item in upcoming_season_items.order_by("season_number"):
        key = (season_item.source, season_item.media_id)
        season_items_by_tv.setdefault(key, {})[season_item.season_number] = season_item

    return season_items_by_tv


def reconcile_completed_tv_seasons(user=None, tv_items=None):
    """Create missing planning seasons from saved future calendar events."""
    season_items_by_tv = _get_upcoming_season_items(tv_items)
    if not season_items_by_tv:
        return 0, 0

    eligible_tvs = TV.objects.filter(
        _media_id_scope(season_items_by_tv, prefix="item__"),
        status__in=(
            Status.COMPLETED.value,
            Status.IN_PROGRESS.value,
        ),
    ).select_related("item", "user")
    if user is not None:
        eligible_tvs = eligible_tvs.filter(user=user)

    repaired_tv_count = 0
    created_season_count = 0

    with transaction.atomic():
        eligible_tvs = list(
            eligible_tvs.select_for_update().prefetch_related(
                Prefetch(
                    "seasons",
                    queryset=Season.objects.select_related("item"),
                ),
            ),
        )

        for tv in eligible_tvs:
            key = (tv.item.source, tv.item.media_id)
            existing_season_numbers = {
                season.item.season_number for season in tv.seasons.all()
            }
            missing_season_items = [
                season_item
                for season_number, season_item in season_items_by_tv[key].items()
                if season_number not in existing_season_numbers
            ]
            if not missing_season_items:
                continue

            planning_seasons = [
                Season(
                    item=season_item,
                    related_tv=tv,
                    user=tv.user,
                    status=Status.PLANNING.value,
                )
                for season_item in missing_season_items
            ]
            bulk_create_with_history(
                planning_seasons,
                Season,
                default_user=tv.user,
            )

            reopened_tv = tv.status == Status.COMPLETED.value
            if reopened_tv:
                tv.status = Status.IN_PROGRESS.value
                bulk_update_with_history(
                    [tv],
                    TV,
                    ["status"],
                    default_user=tv.user,
                )

            repaired_tv_count += 1
            created_season_count += len(planning_seasons)
            logger.info(
                "%s - %s TV for user %s and created planning seasons %s",
                tv.item,
                "Reopened" if reopened_tv else "Reconciled",
                tv.user,
                [season.item.season_number for season in planning_seasons],
            )

    if created_season_count:
        logger.info(
            "TV season reconciliation repaired %d shows and created %d "
            "planning seasons",
            repaired_tv_count,
            created_season_count,
        )

    return repaired_tv_count, created_season_count


def process_season_episodes(item, metadata, events_bulk):
    """Process episodes for a season and add them to events_bulk."""
    tvmaze_map = {}
    if metadata.get("tvdb_id"):
        logger.info(
            "%s - TVDB ID found, fetching TVMaze episode data",
            item,
        )
        tvmaze_map = get_tvmaze_episode_map(metadata["tvdb_id"])
    else:
        logger.warning(
            "%s - No TVDB ID found, skipping TVMaze episode data",
            item,
        )

    if not metadata.get("episodes"):
        logger.warning("%s - No episodes found in metadata", item)
        return

    for episode in metadata["episodes"]:
        episode_number = episode["episode_number"]
        season_number = metadata["season_number"]

        episode_datetime = get_episode_datetime(
            episode,
            season_number,
            episode_number,
            tvmaze_map,
        )

        events_bulk.append(
            Event(
                item=item,
                content_number=episode_number,
                datetime=episode_datetime,
            ),
        )


def get_episode_datetime(episode, season_number, episode_number, tvmaze_map):
    """Determine the most accurate air datetime for an episode."""
    tvmaze_key = f"{season_number}_{episode_number}"
    tvmaze_airstamp = tvmaze_map.get(tvmaze_key)

    if tvmaze_airstamp:
        return datetime.fromisoformat(tvmaze_airstamp)

    if episode["air_date"]:
        try:
            return date_parser(episode["air_date"])
        except ValueError:
            logger.warning(
                "Invalid air date for S%sE%s from TMDB: %s",
                season_number,
                episode_number,
                episode["air_date"],
            )

    return datetime.min.replace(tzinfo=ZoneInfo("UTC"))


def get_tvmaze_episode_map(tvdb_id):
    """Fetch and process episode data from TVMaze using TVDB ID with caching."""
    cache_key = f"tvmaze_map_{tvdb_id}"
    cached_map = cache.get(cache_key)

    if cached_map:
        logger.info("%s - Using cached TVMaze episode map", tvdb_id)
        return cached_map

    show_response = get_tvmaze_response(tvdb_id)
    tvmaze_map = {}

    if show_response:
        episodes = show_response["_embedded"]["episodes"]

        for episode in episodes:
            season_num = episode.get("season")
            episode_num = episode.get("number")
            if season_num is not None and episode_num is not None:
                key = f"{season_num}_{episode_num}"
                tvmaze_map[key] = episode.get("airstamp")

    cache.set(cache_key, tvmaze_map)
    logger.info(
        "%s - Cached TVMaze episode map with %d entries",
        tvdb_id,
        len(tvmaze_map),
    )

    return tvmaze_map


def get_tvmaze_response(tvdb_id):
    """Fetch episode data from TVMaze using TVDB ID."""
    lookup_url = f"https://api.tvmaze.com/lookup/shows?thetvdb={tvdb_id}"
    try:
        lookup_response = services.api_request("TVMaze", "GET", lookup_url)
    except requests.exceptions.HTTPError as err:
        if err.response.status_code == requests.codes.not_found:
            logger.warning(
                "TVMaze lookup failed for TVDB ID %s - %s",
                tvdb_id,
                err.response.text,
            )
        else:
            logger.warning(
                "%s - TVMaze lookup error: %s",
                tvdb_id,
                err.response.text,
            )
        lookup_response = {}

    if not lookup_response:
        logger.warning("%s - No TVMaze lookup response for TVDB ID", tvdb_id)
        return {}

    tvmaze_id = lookup_response.get("id")

    if not tvmaze_id:
        logger.warning("%s - TVMaze ID not found for TVDB ID", tvdb_id)
        return {}

    show_url = f"https://api.tvmaze.com/shows/{tvmaze_id}?embed=episodes"

    try:
        return services.api_request("TVMaze", "GET", show_url)
    except requests.exceptions.HTTPError:
        return {}
