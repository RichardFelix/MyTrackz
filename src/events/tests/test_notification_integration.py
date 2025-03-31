from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from app.models import Anime, Item, Manga, Media
from events.models import Event
from events.tasks import send_release_notifications


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class NotificationIntegrationTests(TestCase):
    """Integration tests for the notification system."""

    def setUp(self):
        """Set up test data."""
        # Create users
        self.credentials = {
            "username": "test",
            "password": "12345",
            "notification_urls": "https://example.com/notify",
        }
        self.user = get_user_model().objects.create_user(**self.credentials)

        # Create items
        self.item = Item.objects.create(
            media_id="1",
            source="mal",
            media_type="anime",
            title="Test Anime",
            image="http://example.com/anime.jpg",
        )

        # Create media tracking
        Anime.objects.create(
            item=self.item,
            user=self.user,
            status=Media.Status.IN_PROGRESS.value,
        )

    @patch("events.tasks.send_notifications")
    def test_end_to_end_notification(self, mock_send_notifications):
        """Test the entire notification flow."""
        # Setup mock
        mock_send_notifications.return_value = None

        # Create a recent event
        now = timezone.now()
        thirty_mins_ago = now - timedelta(minutes=30)

        event = Event.objects.create(
            item=self.item,
            episode_number=5,
            datetime=thirty_mins_ago,
            notification_sent=False,
        )

        # Run the task
        send_release_notifications()

        # Verify event was marked as notified
        event.refresh_from_db()
        self.assertTrue(event.notification_sent)

        # Verify send_notifications was called
        mock_send_notifications.assert_called_once()

        # Get the arguments passed to send_notifications
        user_releases, _ = mock_send_notifications.call_args[0]

        # Verify the user_releases contains our event
        self.assertIn(self.user.id, user_releases)
        self.assertEqual(len(user_releases[self.user.id]), 1)
        self.assertEqual(user_releases[self.user.id][0].id, event.id)
        self.assertEqual(user_releases[self.user.id][0].item.title, "Test Anime")
        self.assertEqual(user_releases[self.user.id][0].episode_number, 5)

    @patch("events.tasks.send_notifications")
    def test_exclude_then_notify(self, mock_send_notifications):
        """Test excluding an item then verifying it's not in notifications."""
        # Setup mock
        mock_send_notifications.return_value = None

        # Create two events
        now = timezone.now()
        thirty_mins_ago = now - timedelta(minutes=30)

        # Create a second item
        item2 = Item.objects.create(
            media_id="52991",
            source="mal",
            media_type="anime",
            title="Another Anime",
            image="http://example.com/anime.jpg",
        )

        # Track the second item
        Anime.objects.create(
            item=item2,
            user=self.user,
            status=Media.Status.IN_PROGRESS.value,
        )

        # Create events for both items
        event1 = Event.objects.create(
            item=self.item,
            episode_number=5,
            datetime=thirty_mins_ago,
            notification_sent=False,
        )

        event2 = Event.objects.create(
            item=item2,
            episode_number=3,
            datetime=thirty_mins_ago,
            notification_sent=False,
        )

        # Exclude the first item
        self.user.notification_excluded_items.add(self.item)

        # Run the task
        send_release_notifications()

        # Verify both events were marked as notified
        event1.refresh_from_db()
        event2.refresh_from_db()
        self.assertTrue(event1.notification_sent)
        self.assertTrue(event2.notification_sent)

        # Verify send_notifications was called
        mock_send_notifications.assert_called_once()

        # Get the arguments passed to send_notifications
        user_releases, _ = mock_send_notifications.call_args[0]

        # Verify the user_releases contains only the second event
        self.assertIn(self.user.id, user_releases)
        self.assertEqual(len(user_releases[self.user.id]), 1)
        self.assertEqual(user_releases[self.user.id][0].id, event2.id)
        self.assertEqual(user_releases[self.user.id][0].item.title, "Another Anime")
        self.assertEqual(user_releases[self.user.id][0].episode_number, 3)

        # Verify the first event is not in user_releases
        for events in user_releases.values():
            for event in events:
                self.assertNotEqual(event.id, event1.id)

    @patch("events.tasks.send_notifications")
    def test_no_users_with_notifications(self, mock_send_notifications):
        """Test behavior when no users have notification URLs configured."""
        # Setup mock
        mock_send_notifications.return_value = None

        # Remove notification URLs
        self.user.notification_urls = ""
        self.user.save()

        # Create a recent event
        now = timezone.now()
        thirty_mins_ago = now - timedelta(minutes=30)

        event = Event.objects.create(
            item=self.item,
            episode_number=5,
            datetime=thirty_mins_ago,
            notification_sent=False,
        )

        # Run the task
        send_release_notifications()

        # Verify event was still marked as notified
        event.refresh_from_db()
        self.assertTrue(event.notification_sent)

        # Verify send_notifications was called, but with an empty users QuerySet
        mock_send_notifications.assert_called_once()

        # Get the arguments passed to send_notifications
        user_releases, users = mock_send_notifications.call_args[0]

        # Verify that no users were passed
        self.assertEqual(len(users), 0)
        self.assertEqual(user_releases, {})

    @patch("events.tasks.send_notifications")
    def test_multiple_media_types(self, mock_send_notifications):
        """Test notifications with multiple media types."""
        # Setup mock
        mock_send_notifications.return_value = None

        # Create a manga item
        manga_item = Item.objects.create(
            media_id="3",
            source="mal",
            media_type="manga",
            title="Test Manga",
            image="http://example.com/manga.jpg",
        )

        # Track the manga item
        Manga.objects.create(
            item=manga_item,
            user=self.user,
            status=Media.Status.IN_PROGRESS.value,
        )

        # Create events for both media types
        now = timezone.now()
        thirty_mins_ago = now - timedelta(minutes=30)

        anime_event = Event.objects.create(
            item=self.item,
            episode_number=5,
            datetime=thirty_mins_ago,
            notification_sent=False,
        )

        manga_event = Event.objects.create(
            item=manga_item,
            episode_number=10,
            datetime=thirty_mins_ago,
            notification_sent=False,
        )

        # Run the task
        send_release_notifications()

        # Verify both events were marked as notified
        anime_event.refresh_from_db()
        manga_event.refresh_from_db()
        self.assertTrue(anime_event.notification_sent)
        self.assertTrue(manga_event.notification_sent)

        # Verify send_notifications was called
        mock_send_notifications.assert_called_once()

        # Get the arguments passed to send_notifications
        user_releases, _ = mock_send_notifications.call_args[0]

        # Verify the user_releases contains both events
        self.assertIn(self.user.id, user_releases)
        self.assertEqual(len(user_releases[self.user.id]), 2)

        # Get the event IDs
        event_ids = [event.id for event in user_releases[self.user.id]]
        self.assertIn(anime_event.id, event_ids)
        self.assertIn(manga_event.id, event_ids)

        # Get the event titles
        event_titles = [event.item.title for event in user_releases[self.user.id]]
        self.assertIn("Test Anime", event_titles)
        self.assertIn("Test Manga", event_titles)
