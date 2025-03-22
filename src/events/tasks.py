import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from django.db.models import Q

from app.models import Item
from app.providers import services, tmdb
from app.templatetags import app_tags
from events.models import Event

logger = logging.getLogger(__name__)

DEFAULT_MONTH_DAY = "-01-01"
DEFAULT_DAY = "-01"


@shared_task(name="Reload calendar")
def reload_calendar(user=None, items_to_process=None):  # used for metadata
    """Refresh the calendar with latest dates for all users."""
    if not items_to_process:
        items_to_process = Event.objects.get_items_to_process()

    events_bulk = []
    anime_to_process = []

    for item in items_to_process:
        # anime can later be processed in bulk
        if item.media_type == "anime":
            anime_to_process.append(item)
        else:
            process_item(item, events_bulk)

    # process anime items in bulk
    process_anime_bulk(anime_to_process, events_bulk)

    for event in events_bulk:
        Event.objects.update_or_create(
            item=event.item,
            episode_number=event.episode_number,
            defaults={"datetime": event.datetime},
        )

    if user:
        reloaded_items = get_user_reloaded(events_bulk, user)
    else:
        reloaded_items = {event.item for event in events_bulk}

    reloaded_count = len(reloaded_items)
    result_msg = "\n".join(
        f"{item} ({app_tags.media_type_readable_plural(item.media_type)})"
        for item in reloaded_items
    )

    if reloaded_count > 0:
        return f"""The following items have been loaded to the calendar:\n
                    {result_msg}"""
    return "There have been no changes in the calendar"


def process_item(item, events_bulk):
    """Process each item and add events to the event list."""
    try:
        if item.media_type == "season":
            tv_with_seasons_metadata = tmdb.tv_with_seasons(
                item.media_id,
                [item.season_number],
            )
            metadata = tv_with_seasons_metadata[f"season/{item.season_number}"]
            process_season(item, metadata, events_bulk)
        else:
            metadata = services.get_media_metadata(
                item.media_type,
                item.media_id,
                item.source,
            )
            process_other(item, metadata, events_bulk)
    except requests.exceptions.HTTPError as err:
        # happens for niche media in which the mappings during import are incorrect
        if err.response.status_code == requests.codes.not_found:
            pass
        else:
            raise


def process_anime_bulk(items, events_bulk):
    """Process multiple anime items and add events to the event list."""
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

            if airing_schedule:
                # Filter episodes if total_episodes is known
                # happens with idMal: 54857
                if total_episodes is not None:
                    airing_schedule = [
                        episode
                        for episode in airing_schedule
                        if episode["episode"] <= total_episodes
                    ]
                    # Log any filtering that occurred
                    if len(media["airingSchedule"]["nodes"]) > len(airing_schedule):
                        logger.info(
                            "Filtered episodes for MAL ID %s - keep only %s episodes",
                            mal_id,
                            total_episodes,
                        )
            # No airing schedule, create basic one from dates
            else:
                airing_schedule = []
                start_date_timestamp = anilist_date_parser(media["startDate"])

                # Add first episode
                if start_date_timestamp:
                    airing_schedule.append(
                        {"episode": 1, "airingAt": start_date_timestamp},
                    )

                # Add last episode if we know total episodes
                if total_episodes and total_episodes > 1:
                    end_date_timestamp = anilist_date_parser(media["endDate"])
                    if end_date_timestamp:
                        airing_schedule.append(
                            {"episode": total_episodes, "airingAt": end_date_timestamp},
                        )

            # Store the processed schedule
            all_data[mal_id] = airing_schedule

        if not response["data"]["Page"]["pageInfo"]["hasNextPage"]:
            break
        page += 1

    return all_data


def process_season(item, metadata, events_bulk):
    """Process season item and add events to the event list."""
    # Check if we have TVMaze data available
    tvmaze_episodes = None

    if metadata["external_ids"].get("tvdb_id"):
        tvdb_id = metadata["external_ids"]["tvdb_id"]
        tvmaze_episodes = get_tvmaze_episodes(tvdb_id)

    # Create a mapping of TVMaze episodes by season and episode number
    tvmaze_map = {}
    if tvmaze_episodes:
        for ep in tvmaze_episodes:
            season_num = ep.get("season")
            episode_num = ep.get("number")
            if season_num is not None and episode_num is not None:
                key = f"{season_num}_{episode_num}"
                value = {"airstamp": ep["airstamp"], "airtime": ep["airtime"]}
                tvmaze_map[key] = value

    for episode in metadata["episodes"]:
        episode_number = episode["episode_number"]
        season_number = metadata["season_number"]

        # First check if we have TVMaze data for this episode
        tvmaze_key = f"{season_number}_{episode_number}"
        tvmaze_episode = tvmaze_map.get(tvmaze_key)

        # only use TVMaze data if it has an airtime
        if tvmaze_episode and tvmaze_episode["airtime"]:
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


def get_tvmaze_episodes(tvdb_id):
    """Fetch episode data from TVMaze using TVDB ID with caching."""
    # Check cache first
    cache_key = f"tvmaze_{tvdb_id}"
    cached_data = cache.get(cache_key)
    if cached_data:
        return cached_data

    # First, lookup the TVMaze ID using the TVDB ID
    lookup_url = f"https://api.tvmaze.com/lookup/shows?thetvdb={tvdb_id}"
    lookup_response = services.api_request("TVMaze", "GET", lookup_url)
    tvmaze_id = lookup_response["id"]

    if not tvmaze_id:
        logger.warning("TVMaze ID not found for TVDB ID %s", tvdb_id)
        return None

    # Now fetch the show with embedded episodes
    show_url = f"https://api.tvmaze.com/shows/{tvmaze_id}?embed=episodes"
    show_response = services.api_request("TVMaze", "GET", show_url)

    episodes = show_response["_embedded"]["episodes"]
    # Cache the result for 24 hours
    cache.set(cache_key, episodes, timeout=86400)
    return episodes


def process_other(item, metadata, events_bulk):
    """Process other types of items and add events to the event list."""
    # it will have either of these keys
    date_keys = ["start_date", "release_date", "first_air_date", "publish_date"]
    for date_key in date_keys:
        if date_key in metadata["details"] and metadata["details"][date_key]:
            try:
                episode_datetime = date_parser(metadata["details"][date_key])
                if item.media_type == "book" and metadata["details"]["number_of_pages"]:
                    episode_number = metadata["details"]["number_of_pages"]
                elif item.media_type == "movie":
                    episode_number = 1
                else:
                    episode_number = None
                events_bulk.append(
                    Event(
                        item=item,
                        episode_number=episode_number,
                        datetime=episode_datetime,
                    ),
                )
            except ValueError:
                pass
    if item.media_type == "manga" and metadata["max_progress"]:
        # MyAnimeList manga has an end date when it's completed
        if "end_date" in metadata["details"] and metadata["details"]["end_date"]:
            episode_datetime = date_parser(metadata["details"]["end_date"])
        # MangaUpdates doesn't have an end date, so use a placeholder
        else:
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
    # Preprocess the date string
    parts = date_str.split("-")
    if len(parts) == year_only_parts:
        date_str += DEFAULT_MONTH_DAY
    elif len(parts) == year_month_parts:
        # Year and month are provided, append "-01"
        date_str += DEFAULT_DAY

    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=ZoneInfo("UTC"))
    # Set to max time and add UTC timezone
    return dt.replace(
        hour=settings.SENTINEL_TIME_HOUR,
        minute=settings.SENTINEL_TIME_MINUTE,
        second=settings.SENTINEL_TIME_SECOND,
        microsecond=settings.SENTINEL_TIME_MICROSECOND,
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
        hour=settings.SENTINEL_TIME_HOUR,
        minute=settings.SENTINEL_TIME_MINUTE,
        second=settings.SENTINEL_TIME_SECOND,
        microsecond=settings.SENTINEL_TIME_MICROSECOND,
        tzinfo=ZoneInfo("UTC"),
    )

    return dt.timestamp()


def get_user_reloaded(reloaded_events, user):
    """Get the items that have been reloaded for the user."""
    event_item_ids = {event.item_id for event in reloaded_events}

    media_type_groups = {}
    for item_id, media_type in Item.objects.filter(
        id__in=event_item_ids,
    ).values_list("id", "media_type"):
        media_type_groups.setdefault(media_type, set()).add(item_id)

    # Return an empty queryset if media_type_groups is empty
    if not media_type_groups:
        return Item.objects.none()

    q_filters = Q()
    for media_type, item_ids in media_type_groups.items():
        q_filters |= Q(
            id__in=item_ids,
            media_type=media_type,
            **{f"{media_type}__user": user},
        )

    return Item.objects.filter(q_filters).distinct()
