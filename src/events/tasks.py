import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import apprise
import requests
from celery import shared_task
from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db.models import (
    Q,
)
from django.utils import timezone

from app import media_type_config
from app.models import Item, Media, MediaTypes, Sources
from app.providers import comicvine, services, tmdb
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
        if item.media_type == MediaTypes.ANIME.value:
            anime_to_process.append(item)
        elif item.media_type == MediaTypes.SEASON.value:
            process_season(item, events_bulk)
        elif item.media_type == MediaTypes.COMIC.value:
            process_comic(item, events_bulk)
        else:
            process_other(item, events_bulk)

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
        f"{item} ({item.get_media_type_display()})" for item in reloaded_items
    )

    if reloaded_count > 0:
        return f"""The following items have been loaded to the calendar:\n
                    {result_msg}"""
    return "There have been no changes in the calendar"


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
        tvdb_id = metadata["external_ids"]["tvdb_id"]
        tvmaze_map = get_tvmaze_episode_map(tvdb_id)

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
    lookup_response = services.api_request("TVMaze", "GET", lookup_url)

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


@shared_task(name="Send release notifications")
def send_release_notifications():
    """Send notifications for recently released media."""
    logger.info("Starting recent release notification task")

    # Find events that were released in the past hour and haven't been notified yet
    now = timezone.now()
    one_hour_ago = now - timezone.timedelta(hours=1)

    recent_events = Event.objects.filter(
        datetime__gte=one_hour_ago,
        datetime__lte=now,
        notification_sent=False,
    ).select_related("item")

    if not recent_events.exists():
        return "No recent releases found in the past hour"

    event_count = recent_events.count()
    logger.info("Found %s recent releases in the past hour", event_count)

    users_with_notifications = (
        get_user_model()
        .objects.filter(
            ~Q(notification_urls=""),
        )
        .prefetch_related("notification_excluded_items")
    )
    logger.info(
        "Found %s users with notification URLs",
        users_with_notifications.count(),
    )

    user_exclusions = {
        user.id: set(user.notification_excluded_items.values_list("id", flat=True))
        for user in users_with_notifications
    }

    user_releases, events_to_mark = process_events(recent_events, user_exclusions)

    send_notifications(user_releases, users_with_notifications)

    if events_to_mark:
        Event.objects.filter(id__in=events_to_mark).update(notification_sent=True)
        logger.info("Marked %s events as notified", len(events_to_mark))

    return f"{event_count} recent releases processed"


def process_events(recent_events, user_exclusions):
    """Process events and determine which users should receive notifications."""
    user_releases = {}
    events_to_mark = []

    events_by_media_type = {}
    for event in recent_events:
        media_type = event.item.media_type
        if media_type not in events_by_media_type:
            events_by_media_type[media_type] = []
        events_by_media_type[media_type].append(event)
        events_to_mark.append(event.id)

    # Process events by media type
    for media_type, events in events_by_media_type.items():
        model_name = media_type.capitalize()
        model = apps.get_model("app", model_name)

        item_ids = [event.item.id for event in events]

        tracking_records = (
            model.objects.filter(
                item_id__in=item_ids,
            )
            .exclude(
                Q(
                    status__in=[
                        Media.Status.PAUSED.value,
                        Media.Status.DROPPED.value,
                    ],
                )
                | Q(user__notification_urls=""),
            )
            .select_related("user")
            .values_list("user_id", "item_id")
        )

        # Create a dict mapping item_id to list of user_ids
        item_to_users = {}
        for user_id, item_id in tracking_records:
            if item_id not in item_to_users:
                item_to_users[item_id] = []
            item_to_users[item_id].append(user_id)

        # Match users with events they should be notified about
        for event in events:
            item_id = event.item.id
            users_tracking = item_to_users.get(item_id, [])

            for user_id in users_tracking:
                # Skip if user has excluded this item
                if item_id in user_exclusions.get(user_id, set()):
                    logger.info(
                        "User %s has excluded item %s from notifications, skipping",
                        user_id,
                        item_id,
                    )
                    continue

                # Add this event to the user's list of releases
                if user_id not in user_releases:
                    user_releases[user_id] = []
                user_releases[user_id].append(event)

    return user_releases, events_to_mark


def send_notifications(user_releases, users_with_notifications):
    """Send notifications to users about their releases."""
    users_by_id = {user.id: user for user in users_with_notifications}

    # Send a single notification to each user with all their releases
    for user_id, releases in user_releases.items():
        user = users_by_id.get(user_id)
        if not user:
            logger.error("User %s not found", user_id)
            continue

        # Filter releases based on user's active media types
        active_media_types = user.get_active_media_types()
        filtered_releases = [
            release
            for release in releases
            if release.item.media_type in active_media_types
        ]

        # Skip if no releases match active media types
        if not filtered_releases:
            continue

        apobj = apprise.Apprise()
        notification_urls = [
            url.strip() for url in user.notification_urls.splitlines() if url.strip()
        ]
        for url in notification_urls:
            apobj.add(url)

        notification_body = format_notification_text(filtered_releases)

        try:
            result = apobj.notify(
                title="🔔 YamTrack: New Releases Available! 🔔",
                body=notification_body,
            )

            if result:
                logger.info(
                    "Notification sent to %s for %s releases",
                    user.username,
                    len(filtered_releases),
                )
            else:
                logger.error(
                    "Failed to send notification to %s for %s releases",
                    user.username,
                    len(filtered_releases),
                )
        except Exception:
            logger.exception("Error sending notification to %s", user.username)


def format_notification_text(releases):
    """Format notification text for a user based on their releases."""
    # Group releases by media type for better organization
    releases_by_type = {}
    for event in releases:
        media_type = event.item.media_type
        if media_type not in releases_by_type:
            releases_by_type[media_type] = []
        releases_by_type[media_type].append(event)

    # Format the notification body with better structure
    notification_body = []
    notification_body.append("--------------------------------------------")

    # Add releases grouped by media type
    for media_type, events in releases_by_type.items():
        icon = app_tags.unicode_icon(media_type)

        # Add a header for each media type with icon
        if media_type == MediaTypes.SEASON.value:
            notification_body.append(f"{icon}  TV Shows")
        else:
            notification_body.append(f"{icon}  {media_type.upper()}")

        for event in events:
            notification_body.extend(
                [f"  • {event}"],
            )

        # Add a blank line between media types
        notification_body.append("")

    notification_body.append("Enjoy your media!")

    return "\n".join(notification_body)
