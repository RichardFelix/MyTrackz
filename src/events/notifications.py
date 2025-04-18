import logging
from datetime import UTC

import apprise
from django.apps import apps
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone

from app.models import MediaTypes
from app.templatetags import app_tags
from events.models import INACTIVE_TRACKING_STATUSES, Event

logger = logging.getLogger(__name__)


def send_releases():
    """Send notifications for recently released media."""
    now = timezone.now()
    thirty_minutes_ago = now - timezone.timedelta(minutes=30)

    # Find events that were released recently and haven't been notified yet
    events = Event.objects.filter(
        datetime__gte=thirty_minutes_ago,
        datetime__lte=now,
        notification_sent=False,
    ).select_related("item")

    if not events.exists():
        return "No recent releases found"

    # Get users who should receive notifications
    users = (
        get_user_model()
        .objects.filter(
            ~Q(notification_urls=""),
            release_notifications_enabled=True,
        )
        .prefetch_related("notification_excluded_items")
    )

    if not users.exists():
        return "No users with release notifications enabled"

    result = send_notifications(
        events=events,
        users=users,
        title="🔔 YamTrack: New Releases Available! 🔔",
    )

    # Mark events as notified
    if result["event_ids"]:
        Event.objects.filter(id__in=result["event_ids"]).update(
            notification_sent=True,
        )
        logger.info("Marked %s events as notified", len(result["event_ids"]))

    return f"{result['event_count']} recent releases processed"


def send_daily_digest():
    """Send daily digest of today's releases to users."""
    # Get current date in the timezone defined in settings
    now_in_current_tz = timezone.localtime()

    # Create start and end of today in the current timezone
    today_start = now_in_current_tz.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    today_end = today_start + timezone.timedelta(days=1)

    # Convert back to UTC for database query
    today_start_utc = today_start.astimezone(UTC)
    today_end_utc = today_end.astimezone(UTC)

    # Get today's events using the converted UTC times
    events = Event.objects.filter(
        datetime__gte=today_start_utc,
        datetime__lt=today_end_utc,
    ).select_related("item")

    if not events.exists():
        return "No releases scheduled for today"

    # Get users who have enabled daily digest
    users = (
        get_user_model()
        .objects.filter(
            ~Q(notification_urls=""),
            daily_digest_enabled=True,
        )
        .prefetch_related("notification_excluded_items")
    )

    if not users.exists():
        return "No users with daily digest enabled"

    # Format date for display in local timezone
    title = (
        f"📆 YamTrack: Today's Releases ({today_start.date().strftime('%b %d, %Y')}) 📆"
    )

    result = send_notifications(
        events=events,
        users=users,
        title=title,
    )

    return f"Daily digest sent for {result['event_count']} releases"


def send_notifications(
    events,
    users,
    title,
):
    """Process events and send notifications to appropriate users.

    Args:
        events: QuerySet of Event objects
        users: QuerySet of User objects
        title: Notification title

    Returns:
        Dictionary with results information
    """
    event_count = events.count()
    logger.info("Found %s events for notification", event_count)
    logger.info("Found %s eligible users", users.count())

    # Prepare user data and event data
    user_data = prepare_user_data(users)
    event_data = prepare_event_data(events)

    # Match users with their relevant releases
    user_releases = match_users_to_releases(event_data, user_data)

    # Send the notifications
    deliver_notifications(
        user_releases,
        user_data,
        title,
    )

    return {
        "event_count": event_count,
        "event_ids": event_data["event_ids"],
    }


def prepare_user_data(users):
    """Prepare user data for notification processing.

    Args:
        users: QuerySet of User objects

    Returns:
        Dictionary with user data mappings
    """
    # Create user exclusion mapping
    user_exclusions = {
        user.id: set(user.notification_excluded_items.values_list("id", flat=True))
        for user in users
    }

    # Create a mapping of users to their active media types
    user_media_types = {user.id: user.get_active_media_types() for user in users}

    # Create a mapping of users to their notification URLs
    user_notification_urls = {}
    for user in users:
        urls = [
            url.strip() for url in user.notification_urls.splitlines() if url.strip()
        ]
        if urls:
            user_notification_urls[user.id] = urls

    # Create a mapping of user IDs to user objects
    users_by_id = {user.id: user for user in users}

    return {
        "exclusions": user_exclusions,
        "media_types": user_media_types,
        "notification_urls": user_notification_urls,
        "users_by_id": users_by_id,
    }


def prepare_event_data(events):
    """Prepare event data for notification processing.

    Args:
        events: QuerySet of Event objects

    Returns:
        Dictionary with event data
    """
    # Group events by media type for more efficient processing
    events_by_media_type = {}
    event_ids = []

    for event in events:
        media_type = event.item.media_type
        if media_type not in events_by_media_type:
            events_by_media_type[media_type] = []
        events_by_media_type[media_type].append(event)
        event_ids.append(event.id)

    return {
        "by_media_type": events_by_media_type,
        "event_ids": event_ids,
    }


def match_users_to_releases(event_data, user_data):
    """Match users with the releases they should be notified about.

    Args:
        event_data: Dictionary with event information
        user_data: Dictionary with user information

    Returns:
        Dictionary mapping user IDs to lists of events
    """
    user_releases = {}
    events_by_media_type = event_data["by_media_type"]
    user_exclusions = user_data["exclusions"]
    user_media_types = user_data["media_types"]

    for media_type, media_events in events_by_media_type.items():
        model_name = media_type.capitalize()
        model = apps.get_model("app", model_name)

        # Get all unique item IDs from the events
        item_ids = [event.item.id for event in media_events]
        unique_item_ids = set(item_ids)

        # Create a mapping of item IDs to LISTS of events for that item
        item_to_events = {}
        for event in media_events:
            item_id = event.item.id
            if item_id not in item_to_events:
                item_to_events[item_id] = []
            item_to_events[item_id].append(event)

        logger.info("Item to events mapping: %s", item_to_events)

        # Get tracking records for these items
        tracking_records = (
            model.objects.filter(
                item_id__in=unique_item_ids,
            )
            .exclude(
                Q(status__in=INACTIVE_TRACKING_STATUSES)
                | Q(user__notification_urls=""),
            )
            .values_list("user_id", "item_id")
        )

        # Process tracking records
        for user_id, item_id in tracking_records:
            # Skip if user not in our target users
            if user_id not in user_exclusions:
                continue

            # Skip if user has excluded this item
            if item_id in user_exclusions[user_id]:
                logger.info(
                    "User %s has excluded item %s from notifications, skipping",
                    user_id,
                    item_id,
                )
                continue

            # Get all events for this item
            item_events = item_to_events.get(item_id, [])
            if not item_events:
                continue

            # Skip if media type not in user's active types
            if media_type not in user_media_types.get(user_id, []):
                continue

            # Add all events to user's releases
            if user_id not in user_releases:
                user_releases[user_id] = []

            user_releases[user_id].extend(item_events)

    return user_releases


def deliver_notifications(
    user_releases,
    user_data,
    title,
):
    """Deliver notifications to users.

    Args:
        user_releases: Dictionary mapping user IDs to lists of events
        user_data: Dictionary with user information
        title: Notification title
    """
    users_by_id = user_data["users_by_id"]
    user_notification_urls = user_data["notification_urls"]

    for user_id, releases in user_releases.items():
        if not releases:
            continue

        user = users_by_id.get(user_id)
        if not user:
            logger.error("User %s not found", user_id)
            continue

        # Get notification URLs for this user
        urls = user_notification_urls.get(user_id, [])
        if not urls:
            continue

        # Format notification
        notification_body = format_notification(releases=releases)

        # Send notification
        send_user_notification(user, urls, title, notification_body)


def send_user_notification(user, urls, title, body):
    """Send a notification to a specific user.

    Args:
        user: User object
        urls: List of notification URLs
        title: Notification title
        body: Notification body
    """
    apobj = apprise.Apprise()
    for url in urls:
        apobj.add(url)

    try:
        result = apobj.notify(title=title, body=body)

        if result:
            logger.info(
                "Notification sent to %s",
                user.username,
            )
        else:
            logger.error(
                "Failed to send notification to %s",
                user.username,
            )
    except Exception:
        logger.exception("Error sending notification to %s", user.username)


def format_notification(releases):
    """Format notification text for releases.

    Args:
        releases: List of Event objects to include in the notification

    Returns:
        Formatted notification text as a string
    """
    # Group releases by media type
    releases_by_type = {}
    for event in releases:
        media_type = event.item.media_type
        if media_type not in releases_by_type:
            releases_by_type[media_type] = []
        releases_by_type[media_type].append(event)

    # Format the notification body
    notification_body = []

    notification_body.append("--------------------------------------------")

    # Add releases grouped by media type
    for media_type, media_events in releases_by_type.items():
        icon = app_tags.unicode_icon(media_type)

        # Add a header for each media type with icon
        if media_type == MediaTypes.SEASON.value:
            notification_body.append(f"{icon}  TV Shows")
        else:
            notification_body.append(f"{icon}  {media_type.upper()}")

        sorted_events = sorted(media_events, key=lambda e: e.datetime)

        for event in sorted_events:
            if event.is_sentinel_time:
                # Don't show time for sentinel times
                notification_body.append(f"  • {event}")
            else:
                # Convert to local timezone and format
                local_dt = timezone.localtime(event.datetime)
                time_str = local_dt.strftime("%H:%M")
                notification_body.append(f"  • {event} ({time_str})")

        # Add a blank line between media types
        notification_body.append("")

    notification_body.append("Enjoy your media!")

    return "\n".join(notification_body)
