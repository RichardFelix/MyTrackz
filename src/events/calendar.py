import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from django.core.cache import cache
from django.db.models import Count, Exists, OuterRef, Q, Subquery
from django.utils import timezone

from app import media_type_config
from app.models import Item, Media, MediaTypes, Sources
from app.providers import comicvine, services, tmdb
from events.models import INACTIVE_TRACKING_STATUSES, Event, SentinelDatetime

logger = logging.getLogger(__name__)


def fetch_releases(user=None, items_to_process=None):
    """Fetch and process releases for the calendar."""
    if not items_to_process:
        items_to_process = get_items_to_process(user)

    if not items_to_process:
        return "No items to process"

    events_bulk = []
    anime_to_process = []
    processed_items = {}

    for item in items_to_process:
        processed_items[item.id] = set()

        # anime can later be processed in bulk
        if item.media_type == MediaTypes.ANIME.value:
            anime_to_process.append(item)
        elif item.media_type == MediaTypes.SEASON.value:
            process_season(item, events_bulk)
        elif item.media_type == MediaTypes.COMIC.value:
            process_comic(item, events_bulk)
        else:
            process_other(item, events_bulk)

    process_anime_bulk(anime_to_process, events_bulk)

    for event in events_bulk:
        Event.objects.update_or_create(
            item=event.item,
            episode_number=event.episode_number,
            defaults={"datetime": event.datetime},
        )

    cleanup_invalid_events(processed_items, events_bulk)

    result_msg = "\n".join(
        f"{item} ({item.get_media_type_display()})" for item in items_to_process
    )

    if len(items_to_process) > 0:
        return f"""Releases have been fetched for the following items:
                    {result_msg}"""
    return "No releases have been fetched for any items."


def cleanup_invalid_events(processed_items, events_bulk):
    """Remove events that are no longer valid based on updated episode counts."""
    if not processed_items:
        return

    # Record all episode numbers being processed for each item
    for event in events_bulk:
        if event.episode_number is not None:
            processed_items[event.item.id].add(event.episode_number)

    all_events = Event.objects.filter(
        item_id__in=processed_items.keys(),
    ).select_related("item")

    events_to_delete = []

    for event in all_events:
        if (
            event.episode_number is not None
            and event.item_id in processed_items
            and event.episode_number not in processed_items[event.item_id]
        ):
            logger.info(
                "Invalid event detected: %s - Episode %s (scheduled for %s)",
                event.item,
                event.episode_number,
                event.datetime,
            )
            events_to_delete.append(event.id)

    if events_to_delete:
        deleted_count = Event.objects.filter(id__in=events_to_delete).delete()[0]
        logger.info("Deleted %s invalid events for updated items", deleted_count)


def get_items_to_process(user=None):
    """Get items to process for the calendar."""
    media_types_with_status = [
        choice.value for choice in MediaTypes if choice != MediaTypes.EPISODE
    ]

    active_query = Q()

    for media_type in media_types_with_status:
        base_media_query = Q(
            **{f"{media_type}__isnull": False},
            **{
                f"{media_type}__status__in": [
                    status
                    for status in Media.Status.values
                    if status not in INACTIVE_TRACKING_STATUSES
                ],
            },
        )

        if user:
            base_media_query &= Q(**{f"{media_type}__user": user})

        active_query |= base_media_query

    # Get all items with at least one active media
    items_with_active_media = Item.objects.filter(active_query).distinct()

    # Subquery to check if an item has any future events
    now = timezone.now()
    future_events = Event.objects.filter(
        item=OuterRef("pk"),
        datetime__gte=now,
    )

    # Subquery to check if a comic has events in the last year
    one_year_ago = now - timezone.timedelta(days=365)
    recent_comic_events = Event.objects.filter(
        item=OuterRef("pk"),
        item__media_type=MediaTypes.COMIC.value,
        datetime__gte=one_year_ago,
    ).order_by("-datetime")

    # comics with events in the last year should be processed
    return (
        items_with_active_media.annotate(
            event_count=Count("event"),
            latest_comic_event=Subquery(recent_comic_events.values("datetime")[:1]),
        )
        .filter(
            Q(Exists(future_events))  # has future events
            | Q(event__isnull=True)  # no events
            | (
                Q(media_type=MediaTypes.COMIC.value)
                & Q(latest_comic_event__isnull=False)
            ),
        )
        .distinct()
    )


def process_anime_bulk(items, events_bulk):
    """Process multiple anime items and add events to the event list."""
    if not items:
        return

    anime_data = get_anime_schedule_bulk([item.media_id for item in items])

    for item in items:
        # it may not have the media_id if no matching anime was found
        episodes = anime_data.get(item.media_id)

        if episodes:
            for episode in episodes:
                episode_datetime = datetime.fromtimestamp(
                    episode["airingAt"],
                    tz=ZoneInfo("UTC"),
                )
                events_bulk.append(
                    Event(
                        item=item,
                        episode_number=episode["episode"],
                        datetime=episode_datetime,
                    ),
                )


def get_anime_schedule_bulk(media_ids):
    """Get the airing schedule for multiple anime items from AniList API."""
    all_data = {}
    page = 1
    url = "https://graphql.anilist.co"
    query = """
    query ($ids: [Int], $page: Int) {
      Page(page: $page) {
        pageInfo {
          hasNextPage
        }
        media(idMal_in: $ids, type: ANIME) {
          idMal
          startDate {
            year
            month
            day
          }
          endDate {
            year
            month
            day
          }
          episodes
          airingSchedule {
            nodes {
              episode
              airingAt
            }
          }
        }
      }
    }
    """

    while True:
        variables = {"ids": media_ids, "page": page}
        response = services.api_request(
            "ANILIST",
            "POST",
            url,
            params={"query": query, "variables": variables},
        )

        for media in response["data"]["Page"]["media"]:
            airing_schedule = media["airingSchedule"]["nodes"]
            total_episodes = media["episodes"]
            mal_id = str(media["idMal"])

            # First check if we know the total episode count
            if total_episodes:
                if airing_schedule:
                    # Filter out episodes beyond the total count
                    original_length = len(airing_schedule)
                    airing_schedule = [
                        episode
                        for episode in airing_schedule
                        if episode["episode"] <= total_episodes
                    ]

                    # Log if any filtering occurred
                    if original_length > len(airing_schedule):
                        logger.info(
                            "Filtered episodes for MAL ID %s - keep only %s episodes",
                            mal_id,
                            total_episodes,
                        )
                elif not airing_schedule:
                    # No airing schedule but we know episode count, create from dates
                    start_date_timestamp = anilist_date_parser(media["startDate"])

                    # Add first episode
                    if start_date_timestamp:
                        airing_schedule.append(
                            {"episode": 1, "airingAt": start_date_timestamp},
                        )

                    end_date_timestamp = anilist_date_parser(media["endDate"])
                    # Add last episode
                    if end_date_timestamp and total_episodes > 1:
                        airing_schedule.append(
                            {"episode": total_episodes, "airingAt": end_date_timestamp},
                        )

            # Store the processed schedule
            all_data[mal_id] = airing_schedule

        if not response["data"]["Page"]["pageInfo"]["hasNextPage"]:
            break
        page += 1

    return all_data


def process_season(item, events_bulk):
    """Process season item and add events to the event list."""
    try:
        logger.info("Fetching releases for %s", item)
        tv_with_seasons_metadata = tmdb.tv_with_seasons(
            item.media_id,
            [item.season_number],
        )
        metadata = tv_with_seasons_metadata[f"season/{item.season_number}"]
    except requests.exceptions.HTTPError as err:
        if err.response.status_code == requests.codes.not_found:
            logger.warning(
                "Failed to fetch metadata for %s - %s",
                item,
                err.response.json(),
            )
            return
        raise

    # Get TVMaze episode data if available
    tvmaze_map = {}
    if metadata["external_ids"].get("tvdb_id"):
        logger.info(
            "%s - TVDB ID found, fetching TVMaze episode data",
            item,
        )
        tvdb_id = metadata["external_ids"]["tvdb_id"]
        tvmaze_map = get_tvmaze_episode_map(tvdb_id)
    else:
        logger.warning(
            "%s - No TVDB ID found, skipping TVMaze episode data",
            item,
        )

    for episode in metadata["episodes"]:
        episode_number = episode["episode_number"]
        season_number = metadata["season_number"]

        # First check if we have TVMaze data for this episode
        tvmaze_key = f"{season_number}_{episode_number}"
        tvmaze_episode = tvmaze_map.get(tvmaze_key)

        # only use TVMaze data if it has an airstamp
        if tvmaze_episode and tvmaze_episode["airstamp"]:
            episode_datetime = datetime.fromisoformat(tvmaze_episode["airstamp"])
        elif episode["air_date"]:
            # Fall back to TMDB data (date only)
            try:
                episode_datetime = date_parser(episode["air_date"])
            except ValueError:
                logger.warning(
                    "%s - Invalid air date for episode %s from TMDB",
                    item,
                    episode_number,
                )
                episode_datetime = datetime.min.replace(tzinfo=ZoneInfo("UTC"))
        else:
            # No air date available
            episode_datetime = datetime.max.replace(tzinfo=ZoneInfo("UTC"))

        events_bulk.append(
            Event(
                item=item,
                episode_number=episode_number,
                datetime=episode_datetime,
            ),
        )


def get_tvmaze_episode_map(tvdb_id):
    """Fetch and process episode data from TVMaze using TVDB ID with caching."""
    # Check cache first for the processed map
    cache_key = f"tvmaze_map_{tvdb_id}"
    cached_map = cache.get(cache_key)

    if cached_map:
        logger.info("%s - Using cached TVMaze episode map", tvdb_id)
        return cached_map

    # First, lookup the TVMaze ID using the TVDB ID
    lookup_url = f"https://api.tvmaze.com/lookup/shows?thetvdb={tvdb_id}"
    try:
        lookup_response = services.api_request("TVMaze", "GET", lookup_url)
    except requests.exceptions.HTTPError as err:
        if err.response.status_code == requests.codes.not_found:
            logger.warning(
                "TVMaze lookup failed for TVDB ID %s - %s",
                tvdb_id,
                err.response.json(),
            )
            return {}
        raise

    if not lookup_response:
        logger.warning("%s - No TVMaze lookup response for TVDB ID", tvdb_id)
        return {}

    tvmaze_id = lookup_response.get("id")

    if not tvmaze_id:
        logger.warning("%s - TVMaze ID not found for TVDB ID", tvdb_id)
        return {}

    # Now fetch the show with embedded episodes
    show_url = f"https://api.tvmaze.com/shows/{tvmaze_id}?embed=episodes"
    show_response = services.api_request("TVMaze", "GET", show_url)

    # Process episodes into the map format we need
    tvmaze_map = {}
    episodes = show_response["_embedded"]["episodes"]

    for ep in episodes:
        season_num = ep.get("season")
        episode_num = ep.get("number")
        if season_num is not None and episode_num is not None:
            key = f"{season_num}_{episode_num}"
            value = {"airstamp": ep.get("airstamp"), "airtime": ep.get("airtime")}
            tvmaze_map[key] = value

    # Cache the processed map for 24 hours
    cache.set(cache_key, tvmaze_map, timeout=86400)
    logger.info(
        "%s - Cached TVMaze episode map with %d entries",
        tvdb_id,
        len(tvmaze_map),
    )

    return tvmaze_map


def process_comic(item, events_bulk):
    """Process comic item and add events to the event list."""
    logger.info("Fetching releases for %s", item)
    try:
        metadata = services.get_media_metadata(
            item.media_type,
            item.media_id,
            item.source,
        )
    except requests.exceptions.HTTPError as err:
        # happens for niche media in which the mappings during import are incorrect
        if err.response.status_code == requests.codes.not_found:
            logger.warning(
                "Failed to fetch metadata for %s - %s",
                item,
                err.response.json(),
            )
            return
        raise

    # get latest event
    latest_event = Event.objects.filter(item=item).order_by("-datetime").first()
    last_issue_event_number = latest_event.episode_number if latest_event else 0
    last_published_issue_number = metadata["max_progress"]
    if last_issue_event_number == last_published_issue_number:
        return

    # add latest issue
    issue_metadata = comicvine.issue(metadata["last_issue_id"])

    if issue_metadata["store_date"]:
        issue_datetime = date_parser(issue_metadata["store_date"])
    elif issue_metadata["cover_date"]:
        issue_datetime = date_parser(issue_metadata["cover_date"])
    else:
        return

    events_bulk.append(
        Event(
            item=item,
            episode_number=last_published_issue_number,
            datetime=issue_datetime,
        ),
    )


def process_other(item, events_bulk):
    """Process other types of items and add events to the event list."""
    logger.info("Fetching releases for %s", item)
    try:
        metadata = services.get_media_metadata(
            item.media_type,
            item.media_id,
            item.source,
        )
    except requests.exceptions.HTTPError as err:
        # happens for niche media in which the mappings during import are incorrect
        if err.response.status_code == requests.codes.not_found:
            logger.warning(
                "Failed to fetch metadata for %s - %s",
                item,
                err.response.json(),
            )
            return
        raise

    date_key = media_type_config.get_date_key(item.media_type)

    if date_key in metadata["details"] and metadata["details"][date_key]:
        try:
            episode_datetime = date_parser(metadata["details"][date_key])
            episode_number = metadata["max_progress"]
            events_bulk.append(
                Event(
                    item=item,
                    episode_number=episode_number,
                    datetime=episode_datetime,
                ),
            )
        except ValueError:
            pass

    elif item.source == Sources.MANGAUPDATES.value and metadata["max_progress"]:
        # MangaUpdates doesn't have an end date, so use a placeholder
        episode_datetime = datetime.min.replace(tzinfo=ZoneInfo("UTC"))
        events_bulk.append(
            Event(
                item=item,
                episode_number=metadata["max_progress"],
                datetime=episode_datetime,
            ),
        )


def date_parser(date_str):
    """Parse string in %Y-%m-%d to datetime. Raises ValueError if invalid."""
    year_only_parts = 1
    year_month_parts = 2
    default_month_day = "-01-01"
    default_day = "-01"
    # Preprocess the date string
    parts = date_str.split("-")
    if len(parts) == year_only_parts:
        date_str += default_month_day
    elif len(parts) == year_month_parts:
        # Year and month are provided, append "-01"
        date_str += default_day

    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=ZoneInfo("UTC"))
    # Set to max time and add UTC timezone
    return dt.replace(
        hour=SentinelDatetime.HOUR,
        minute=SentinelDatetime.MINUTE,
        second=SentinelDatetime.SECOND,
        microsecond=SentinelDatetime.MICROSECOND,
        tzinfo=ZoneInfo("UTC"),
    )


def anilist_date_parser(start_date):
    """Parse the start date from AniList to a timestamp."""
    if not start_date["year"]:
        return None

    month = start_date["month"] or 1
    day = start_date["day"] or 1

    # Create date with max time
    dt = datetime(
        start_date["year"],
        month,
        day,
        hour=SentinelDatetime.HOUR,
        minute=SentinelDatetime.MINUTE,
        second=SentinelDatetime.SECOND,
        microsecond=SentinelDatetime.MICROSECOND,
        tzinfo=ZoneInfo("UTC"),
    )

    return dt.timestamp()
