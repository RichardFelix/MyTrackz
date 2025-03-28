from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.db import models
from django.test import TestCase
from django.utils import timezone

from app.models import Anime, Item, Manga, Media
from events.models import Event
from events.tasks import (
    format_notification_text,
    process_events,
    send_notifications,
    send_recent_release_notifications,
)


class NotificationTaskTests(TestCase):
    """Tests for notification-related tasks."""

    def setUp(self):
        """Set up test data."""
        # Create users
        credentials = {
            "username": "user1",
            "password": "password1",
            "notification_urls": "https://example.com/notify1",
        }
        self.user1 = get_user_model().objects.create_user(**credentials)

        credentials = {
            "username": "user2",
            "password": "password2",
            "notification_urls": "https://example.com/notify2",
        }
        self.user2 = get_user_model().objects.create_user(**credentials)

        credentials = {
            "username": "user3",
            "password": "password3",
        }
        self.user3 = get_user_model().objects.create_user(**credentials)

        # Create items
        self.anime_item = Item.objects.create(
            media_id="1",
            source="mal",
            media_type="anime",
            title="Test Anime",
            image="http://example.com/anime.jpg",
        )

        self.manga_item = Item.objects.create(
            media_id="1",
            source="mal",
            media_type="manga",
            title="Test Manga",
            image="http://example.com/manga.jpg",
        )

        # Create media tracking
        Anime.objects.create(
            item=self.anime_item,
            user=self.user1,
            status=Media.Status.IN_PROGRESS.value,
        )

        Anime.objects.create(
            item=self.anime_item,
            user=self.user2,
            status=Media.Status.IN_PROGRESS.value,
        )

        Anime.objects.create(
            item=self.anime_item,
            user=self.user3,
            status=Media.Status.IN_PROGRESS.value,
        )

        Manga.objects.create(
            item=self.manga_item,
            user=self.user1,
            status=Media.Status.IN_PROGRESS.value,
        )

        Manga.objects.create(
            item=self.manga_item,
            user=self.user2,
            status=Media.Status.PAUSED.value,
        )

        # Create events
        now = timezone.now()
        thirty_mins_ago = now - timedelta(minutes=30)

        self.anime_event = Event.objects.create(
            item=self.anime_item,
            episode_number=5,
            datetime=thirty_mins_ago,
            notification_sent=False,
        )

        self.manga_event = Event.objects.create(
            item=self.manga_item,
            episode_number=10,
            datetime=thirty_mins_ago,
            notification_sent=False,
        )

        # User1 excludes manga_item
        self.user1.notification_excluded_items.add(self.manga_item)

    @patch("events.tasks.send_notifications")
    def test_send_recent_release_notifications(self, mock_send_notifications):
        """Test the send_recent_release_notifications task."""
        # Run the task
        send_recent_release_notifications()

        # Check that events were marked as notified
        self.anime_event.refresh_from_db()
        self.manga_event.refresh_from_db()
        self.assertTrue(self.anime_event.notification_sent)
        self.assertTrue(self.manga_event.notification_sent)

        # Check that send_notifications was called with correct data
        mock_send_notifications.assert_called_once()

        # Extract the arguments
        user_releases, users = mock_send_notifications.call_args[0]

        # Verify user_releases contains expected data
        self.assertIn(self.user1.id, user_releases)
        self.assertIn(self.user2.id, user_releases)

        # User1 should only have anime_event (manga is excluded)
        self.assertEqual(len(user_releases[self.user1.id]), 1)
        self.assertEqual(user_releases[self.user1.id][0].id, self.anime_event.id)

        # User2 should only have anime_event (manga is paused)
        self.assertEqual(len(user_releases[self.user2.id]), 1)
        self.assertEqual(user_releases[self.user2.id][0].id, self.anime_event.id)

    def test_process_events(self):
        """Test the process_events function."""
        # Get users with notifications
        users_with_notifications = (
            get_user_model()
            .objects.filter(
                ~models.Q(notification_urls=""),
            )
            .prefetch_related("notification_excluded_items")
        )

        # Create user exclusions dict
        user_exclusions = {
            user.id: set(user.notification_excluded_items.values_list("id", flat=True))
            for user in users_with_notifications
        }

        # Get recent events
        recent_events = Event.objects.filter(
            notification_sent=False,
        ).select_related("item")

        # Process events
        user_releases, events_to_mark = process_events(recent_events, user_exclusions)

        # Verify results
        self.assertEqual(len(events_to_mark), 2)  # Both events should be marked
        self.assertIn(self.anime_event.id, events_to_mark)
        self.assertIn(self.manga_event.id, events_to_mark)

        # User1 should only have anime_event (manga is excluded)
        self.assertIn(self.user1.id, user_releases)
        self.assertEqual(len(user_releases[self.user1.id]), 1)
        self.assertEqual(user_releases[self.user1.id][0].id, self.anime_event.id)

        # User2 should only have anime_event (manga is paused)
        self.assertIn(self.user2.id, user_releases)
        self.assertEqual(len(user_releases[self.user2.id]), 1)
        self.assertEqual(user_releases[self.user2.id][0].id, self.anime_event.id)

        # User3 should not be in user_releases (no notification URLs)
        self.assertNotIn(self.user3.id, user_releases)

    @patch("apprise.Apprise")
    def test_send_notifications(self, mock_apprise):
        """Test the send_notifications function."""
        # Setup mock
        mock_instance = MagicMock()
        mock_apprise.return_value = mock_instance
        mock_instance.notify.return_value = True

        # Create test data
        user_releases = {
            self.user1.id: [self.anime_event],
            self.user2.id: [self.anime_event, self.manga_event],
        }

        users_with_notifications = (
            get_user_model()
            .objects.filter(
                id__in=[self.user1.id, self.user2.id],
            )
            .prefetch_related("notification_excluded_items")
        )

        # Call function
        send_notifications(user_releases, users_with_notifications)

        # Verify Apprise was called correctly
        self.assertEqual(mock_instance.notify.call_count, 2)  # Once for each user

        # Check that URLs were added correctly
        self.assertEqual(mock_instance.add.call_count, 2)  # Once for each user

    def test_format_notification_text(self):
        """Test the format_notification_text function."""
        # Test with multiple media types
        releases = [self.anime_event, self.manga_event]
        notification_text = format_notification_text(releases)

        # Verify text contains expected content
        self.assertIn("ANIME", notification_text)
        self.assertIn("MANGA", notification_text)
        self.assertIn("Test Anime", notification_text)
        self.assertIn("Test Manga", notification_text)
        self.assertIn("Ep. 5", notification_text)
        self.assertIn("Ch. 10", notification_text)

        # Test with single media type
        releases = [self.anime_event]
        notification_text = format_notification_text(releases)

        # Verify text contains expected content
        self.assertIn("ANIME", notification_text)
        self.assertIn("Test Anime", notification_text)
        self.assertIn("Ep. 5", notification_text)
        self.assertNotIn("MANGA", notification_text)
        self.assertNotIn("Test Manga", notification_text)

    @patch("events.tasks.process_events")
    def test_no_recent_events(self, mock_process_events):
        """Test behavior when no recent events are found."""
        # Mark all events as notified
        Event.objects.all().update(notification_sent=True)

        # Run the task
        send_recent_release_notifications()

        # Verify process_events was not called
        mock_process_events.assert_not_called()

    def test_user_exclusion(self):
        """Test that user exclusions are respected."""
        # Create user exclusions dict
        user_exclusions = {
            self.user1.id: {self.manga_item.id},
            self.user2.id: set(),
        }

        # Get recent events
        recent_events = Event.objects.filter(
            notification_sent=False,
        ).select_related("item")

        # Process events
        user_releases, _ = process_events(recent_events, user_exclusions)

        # Verify user1 doesn't get manga notifications
        self.assertIn(self.user1.id, user_releases)
        user1_event_ids = [event.item_id for event in user_releases[self.user1.id]]
        self.assertIn(self.anime_item.id, user1_event_ids)
        self.assertNotIn(self.manga_item.id, user1_event_ids)

        # Verify user2 gets anime notifications but not manga (due to paused status)
        self.assertIn(self.user2.id, user_releases)
        user2_event_ids = [event.item_id for event in user_releases[self.user2.id]]
        self.assertIn(self.anime_item.id, user2_event_ids)
        self.assertNotIn(self.manga_item.id, user2_event_ids)

    def test_empty_user_releases(self):
        """Test behavior when user_releases is empty."""
        # Create a new user who excludes all items
        self.credentials = {
            "username": "test",
            "password": "12345",
            "notification_urls": "https://example.com/notify4",
        }
        user4 = get_user_model().objects.create_user(**self.credentials)

        # Create user exclusions dict where user4 excludes all items
        user_exclusions = {
            user4.id: {self.anime_item.id, self.manga_item.id},
        }

        # Get recent events
        recent_events = Event.objects.filter(
            notification_sent=False,
        ).select_related("item")

        # Process events - should result in empty user_releases for user4
        user_releases, events_to_mark = process_events(recent_events, user_exclusions)

        # Verify user4 is not in user_releases
        self.assertNotIn(user4.id, user_releases)

        # But events should still be marked
        self.assertEqual(len(events_to_mark), 2)

    def test_future_events_not_included(self):
        """Test that future events are not included in notifications."""
        # Create a future event
        now = timezone.now()
        one_hour_ahead = now + timedelta(hours=1)

        future_event = Event.objects.create(
            item=self.anime_item,
            episode_number=6,
            datetime=one_hour_ahead,
            notification_sent=False,
        )

        # Run the task
        send_recent_release_notifications()

        # Future event should not be marked as notified
        future_event.refresh_from_db()
        self.assertFalse(future_event.notification_sent)

    def test_old_events_not_included(self):
        """Test that old events are not included in notifications."""
        # Create an old event
        now = timezone.now()
        two_hours_ago = now - timedelta(hours=2)

        old_event = Event.objects.create(
            item=self.anime_item,
            episode_number=4,
            datetime=two_hours_ago,
            notification_sent=False,
        )

        # Run the task
        send_recent_release_notifications()

        # Old event should not be marked as notified
        old_event.refresh_from_db()
        self.assertFalse(old_event.notification_sent)

    @patch("apprise.Apprise")
    def test_exception_during_notification(self, mock_apprise):
        """Test handling of exceptions during notification."""
        # Setup mock to raise exception
        mock_instance = MagicMock()
        mock_apprise.return_value = mock_instance
        mock_instance.notify.side_effect = Exception("Test exception")

        # Create test data
        user_releases = {
            self.user1.id: [self.anime_event],
        }

        users_with_notifications = (
            get_user_model()
            .objects.filter(
                id__in=[self.user1.id],
            )
            .prefetch_related("notification_excluded_items")
        )

        # Call function - should not propagate exception
        send_notifications(user_releases, users_with_notifications)

        # Verify notification attempt was made
        mock_instance.notify.assert_called_once()
