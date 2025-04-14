import logging

import apprise
from django.apps import apps
from django.contrib.auth import get_user_model
from django.db.models import (
    Q,
)
from django.utils import timezone

from app.models import MediaTypes
from app.templatetags import app_tags
from events.models import INACTIVE_TRACKING_STATUSES, Event

logger = logging.getLogger(__name__)


def send_releases():
    """Send notifications for recently released media."""
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
                    status__in=INACTIVE_TRACKING_STATUSES,
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
