import json
import zoneinfo
from datetime import datetime
from unittest.mock import MagicMock, Mock, patch

from django.contrib import auth
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse
from django_celery_beat.models import CrontabSchedule, PeriodicTask

from app.models import Item
from users import helpers


class Profile(TestCase):
    """Test profile page."""

    def setUp(self):
        """Create user for the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    def test_change_username(self):
        """Test changing username."""
        self.assertEqual(auth.get_user(self.client).username, "test")
        self.client.post(
            reverse("account"),
            {
                "username": "new_test",
            },
        )
        self.assertEqual(auth.get_user(self.client).username, "new_test")

    def test_change_password(self):
        """Test changing password."""
        self.assertEqual(auth.get_user(self.client).check_password("12345"), True)
        self.client.post(
            reverse("account"),
            {
                "old_password": "12345",
                "new_password1": "*FNoZN64",
                "new_password2": "*FNoZN64",
            },
        )
        self.assertEqual(auth.get_user(self.client).check_password("*FNoZN64"), True)

    def test_invalid_password_change(self):
        """Test password change with incorrect old password."""
        response = self.client.post(
            reverse("account"),
            {
                "old_password": "wrongpass",
                "new_password1": "newpass123",
                "new_password2": "newpass123",
            },
        )
        self.assertTrue(auth.get_user(self.client).check_password("12345"))
        self.assertContains(response, "Your old password was entered incorrectly")


class DemoProfileTests(TestCase):
    """Extended profile tests."""

    def setUp(self):
        """Create user for the tests."""
        self.credentials = {"username": "testuser", "password": "testpass123"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    def test_demo_user_cannot_change_username(self):
        """Test that demo users cannot change their username."""
        self.user.is_demo = True
        self.user.save()

        response = self.client.post(
            reverse("account"),
            {
                "username": "new_username",
            },
        )
        self.assertEqual(auth.get_user(self.client).username, "testuser")
        self.assertContains(response, "not allowed for the demo account")

    def test_demo_user_cannot_change_password(self):
        """Test that demo users cannot change their password."""
        self.user.is_demo = True
        self.user.save()

        response = self.client.post(
            reverse("account"),
            {
                "old_password": "testpass123",
                "new_password1": "newpass123",
                "new_password2": "newpass123",
            },
        )
        self.assertTrue(auth.get_user(self.client).check_password("testpass123"))
        self.assertContains(response, "not allowed for the demo account")


class NotificationExclusionTests(TestCase):
    """Tests for notification exclusion functionality."""

    def setUp(self):
        """Set up test data."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

        # Create items
        self.item1 = Item.objects.create(
            media_id="1",
            source="mal",
            media_type="anime",
            title="Test Anime",
            image="http://example.com/anime.jpg",
        )

        self.item2 = Item.objects.create(
            media_id="2",
            source="mal",
            media_type="manga",
            title="Test Manga",
            image="http://example.com/manga.jpg",
        )

    def test_exclude_item(self):
        """Test excluding an item from notifications."""
        response = self.client.post(
            reverse("exclude_notification_item"),
            {"item_id": self.item1.id},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "users/components/excluded_items.html")

        # Verify item was added to exclusions
        self.assertTrue(
            self.user.notification_excluded_items.filter(id=self.item1.id).exists(),
        )

    def test_include_item(self):
        """Test removing an item from exclusions."""
        # First add the item to exclusions
        self.user.notification_excluded_items.add(self.item1)

        response = self.client.post(
            reverse("include_notification_item"),
            {"item_id": self.item1.id},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "users/components/excluded_items.html")

        # Verify item was removed from exclusions
        self.assertFalse(
            self.user.notification_excluded_items.filter(id=self.item1.id).exists(),
        )

    def test_search_items(self):
        """Test searching for items to exclude."""
        response = self.client.get(
            reverse("search_notification_items"),
            {"q": "Test"},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "users/components/search_results.html")

        # Both items should be in results
        self.assertContains(response, "Test Anime")
        self.assertContains(response, "Test Manga")

        # Add item1 to exclusions
        self.user.notification_excluded_items.add(self.item1)

        response = self.client.get(
            reverse("search_notification_items"),
            {"q": "Test"},
            HTTP_HX_REQUEST="true",
        )

        # Only item2 should be in results now
        self.assertNotContains(response, "Test Anime")
        self.assertContains(response, "Test Manga")

    def test_search_items_short_query(self):
        """Test searching with a query that's too short."""
        response = self.client.get(
            reverse("search_notification_items"),
            {"q": "T"},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "users/components/search_results.html")

        # No items should be returned for a 1-character query
        self.assertNotContains(response, "Test Anime")
        self.assertNotContains(response, "Test Manga")

    def test_search_items_empty_query(self):
        """Test searching with an empty query."""
        response = self.client.get(
            reverse("search_notification_items"),
            {"q": ""},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "users/components/search_results.html")

        # No items should be returned for an empty query
        self.assertNotContains(response, "Test Anime")
        self.assertNotContains(response, "Test Manga")

    @patch("apprise.Apprise")
    def test_test_notification(self, mock_apprise):
        """Test the test notification endpoint."""
        # User with notification URLs
        self.user.notification_urls = "https://example.com/notify"
        self.user.save()

        mock_instance = MagicMock()
        mock_apprise.return_value = mock_instance
        mock_instance.notify.return_value = True

        response = self.client.get(reverse("test_notification"))

        # Should redirect back to notifications page
        self.assertRedirects(response, reverse("notifications"))

        # Should have called notify
        mock_instance.notify.assert_called_once()

        # Check for success message
        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages), 1)
        self.assertIn("successfully", str(messages[0]))

    def test_test_notification_no_urls(self):
        """Test the test notification endpoint with no URLs configured."""
        # User without notification URLs
        self.user.notification_urls = ""
        self.user.save()

        response = self.client.get(reverse("test_notification"))

        # Should redirect back to notifications page
        self.assertRedirects(response, reverse("notifications"))

        # Check for error message
        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages), 1)
        self.assertIn("No notification URLs configured", str(messages[0]))

    @patch("apprise.Apprise")
    def test_test_notification_failure(self, mock_apprise):
        """Test the test notification endpoint when notification fails."""
        # User with notification URLs
        self.user.notification_urls = "https://example.com/notify"
        self.user.save()

        mock_instance = MagicMock()
        mock_apprise.return_value = mock_instance
        mock_instance.notify.return_value = False

        response = self.client.get(reverse("test_notification"))

        # Should redirect back to notifications page
        self.assertRedirects(response, reverse("notifications"))

        # Should have called notify
        mock_instance.notify.assert_called_once()

        # Check for error message
        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages), 1)
        self.assertIn("Failed", str(messages[0]))


class HelpersTest(TestCase):
    """Test helper functions."""

    def test_process_task_result_success(self):
        """Test processing a successful task result."""
        task = Mock()
        task.status = "SUCCESS"
        task.result = json.dumps("Imported 5 items")

        processed_task = helpers.process_task_result(task)
        self.assertEqual(processed_task.result, '"Imported 5 items"')

    def test_process_task_result_failure(self):
        """Test processing a failed task result."""
        task = Mock()
        task.status = "FAILURE"
        task.result = json.dumps({"exc_message": ["Task failed with error"]})

        processed_task = helpers.process_task_result(task)
        self.assertEqual(processed_task.result, "Task failed with error")

    def test_process_task_result_started(self):
        """Test processing a started task result."""
        task = Mock()
        task.status = "STARTED"
        task.result = None

        processed_task = helpers.process_task_result(task)
        self.assertEqual(processed_task.result, "Task in progress")

    def test_process_task_result_pending(self):
        """Test processing a pending task result."""
        task = Mock()
        task.status = "PENDING"
        task.result = None

        processed_task = helpers.process_task_result(task)
        self.assertEqual(processed_task.result, "Waiting for task to start")

    @patch("django.utils.timezone.now")
    def test_get_next_run_info_daily(self, mock_now):
        """Test getting next run info for daily task."""
        # Set up mock current time
        current_time = datetime(2025, 2, 6, 12, 0, tzinfo=zoneinfo.ZoneInfo("UTC"))
        mock_now.return_value = current_time

        crontab = CrontabSchedule.objects.create(
            minute="0",
            hour="14",
            day_of_week="*",
            day_of_month="*",
            month_of_year="*",
            timezone="UTC",
        )
        periodic_task = PeriodicTask.objects.create(
            name="Daily Import",
            task="import_task",
            crontab=crontab,
        )

        next_run_info = helpers.get_next_run_info(periodic_task)

        expected_next_run = datetime(2025, 2, 6, 14, 0, tzinfo=zoneinfo.ZoneInfo("UTC"))
        self.assertEqual(next_run_info["next_run"], expected_next_run)
        self.assertEqual(next_run_info["frequency"], "Every Day")

    @patch("django.utils.timezone.now")
    def test_get_next_run_info_every_2_days(self, mock_now):
        """Test getting next run info for every 2 days task."""
        # Thursday, so next run should be same day at 14:00
        current_time = datetime(2025, 2, 6, 12, 0, tzinfo=zoneinfo.ZoneInfo("UTC"))
        mock_now.return_value = current_time

        crontab = CrontabSchedule.objects.create(
            minute="0",
            hour="14",
            day_of_week="*/2",
            day_of_month="*",
            month_of_year="*",
            timezone="UTC",
        )
        periodic_task = PeriodicTask.objects.create(
            name="Every 2 Days Import",
            task="import_task",
            crontab=crontab,
        )

        next_run_info = helpers.get_next_run_info(periodic_task)

        # Since we're testing on Thursday (day 4), and it's before 14:00,
        # the next run should be the same day at 14:00
        expected_next_run = datetime(2025, 2, 6, 14, 0, tzinfo=zoneinfo.ZoneInfo("UTC"))
        self.assertEqual(next_run_info["next_run"], expected_next_run)
        self.assertEqual(next_run_info["frequency"], "Every 2 days")

    @patch("django.utils.timezone.now")
    def test_get_next_run_info_every_2_days_after_todays_run(self, mock_now):
        """Test getting next run info for every 2 days."""
        # Thursday after scheduled time, so next run should be Saturday
        current_time = datetime(2025, 2, 6, 15, 0, tzinfo=zoneinfo.ZoneInfo("UTC"))
        mock_now.return_value = current_time

        crontab = CrontabSchedule.objects.create(
            minute="0",
            hour="14",
            day_of_week="*/2",
            day_of_month="*",
            month_of_year="*",
            timezone="UTC",
        )
        periodic_task = PeriodicTask.objects.create(
            name="Every 2 Days Import",
            task="import_task",
            crontab=crontab,
        )

        next_run_info = helpers.get_next_run_info(periodic_task)

        # Since we're testing on Thursday after 14:00,
        # the next run should be Saturday at 14:00
        expected_next_run = datetime(2025, 2, 8, 14, 0, tzinfo=zoneinfo.ZoneInfo("UTC"))
        self.assertEqual(next_run_info["next_run"], expected_next_run)
        self.assertEqual(next_run_info["frequency"], "Every 2 days")

    def test_get_next_run_info_custom_cron(self):
        """Test getting next run info for custom cron schedule."""
        crontab = CrontabSchedule.objects.create(
            minute="30",
            hour="*/4",
            day_of_week="1,3,5",
            day_of_month="*",
            month_of_year="*",
            timezone="UTC",
        )
        periodic_task = PeriodicTask.objects.create(
            name="Custom Import",
            task="import_task",
            crontab=crontab,
        )

        next_run_info = helpers.get_next_run_info(periodic_task)

        self.assertEqual(next_run_info["frequency"], "Cron: 30 */4 * * 1,3,5")

    def test_get_next_run_info_no_crontab(self):
        """Test getting next run info for task without crontab."""
        periodic_task = Mock()
        periodic_task.crontab = None

        next_run_info = helpers.get_next_run_info(periodic_task)
        self.assertIsNone(next_run_info)
