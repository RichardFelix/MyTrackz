import json
import zoneinfo
from datetime import datetime
from unittest.mock import Mock, patch

from django.test import TestCase
from django_celery_beat.models import CrontabSchedule, PeriodicTask

from users import helpers


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
