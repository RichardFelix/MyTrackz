from datetime import datetime
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from app.models import Anime, Item
from events.models import Event
from events.tasks import date_parser, reload_calendar


class CalendarViewTests(TestCase):
    """Calendar view tests."""

    def setUp(self):
        """Set up the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)
        self.item = Item.objects.create(
            media_id="1",
            source="mal",
            media_type="anime",
            title="My Anime",
            image="http://example.com/image.jpg",
        )
        Anime.objects.create(item=self.item, user=self.user)

        now = timezone.now()
        Event.objects.create(item=self.item, datetime=now)

    def test_calendar_view(self):
        """Test that the calendar view."""
        response = self.client.get(reverse("release_calendar"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "events/calendar.html")
        self.assertTrue(len(response.context["release_dict"]) > 0)


class ReloadCalendarTaskTests(TestCase):
    """Test the reload_calendar task."""

    def setUp(self):
        """Set up the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.item = Item.objects.create(
            media_id="437",
            source="mal",
            media_type="anime",
            title="Perfect Blue",
            image="http://example.com/image.jpg",
        )
        Anime.objects.create(item=self.item, user=self.user, status="Planning")

    def test_reload_calendar(self):
        """Test the reload_calendar task."""
        reload_calendar(self.user.id)
        self.assertTrue(Event.objects.filter(item=self.item).exists())

    def test_date_parser_valid(self):
        """Test date_parser with valid date strings."""
        from django.conf import settings

        valid_date = "2024-08-17"
        parsed_date = date_parser(valid_date)

        # Create expected datetime with sentinel time values
        expected_date = datetime(
            2024,
            8,
            17,
            hour=settings.SENTINEL_TIME_HOUR,
            minute=settings.SENTINEL_TIME_MINUTE,
            second=settings.SENTINEL_TIME_SECOND,
            microsecond=settings.SENTINEL_TIME_MICROSECOND,
            tzinfo=ZoneInfo("UTC"),
        )

        self.assertEqual(parsed_date, expected_date)

    def test_date_parser_invalid(self):
        """Test date_parser raises ValueError with invalid date strings."""
        with self.assertRaises(ValueError):
            date_parser("invalid-date")

    def test_date_parser_year_only(self):
        """Test date_parser with year-only date strings."""
        from django.conf import settings

        year_only_date = "2024"
        parsed_date = date_parser(year_only_date)

        # Create expected datetime with sentinel time values
        expected_date = datetime(
            2024,
            1,
            1,
            hour=settings.SENTINEL_TIME_HOUR,
            minute=settings.SENTINEL_TIME_MINUTE,
            second=settings.SENTINEL_TIME_SECOND,
            microsecond=settings.SENTINEL_TIME_MICROSECOND,
            tzinfo=ZoneInfo("UTC"),
        )

        self.assertEqual(parsed_date, expected_date)

    def test_date_parser_year_month(self):
        """Test date_parser with year and month date strings."""
        from django.conf import settings

        year_month_date = "2024-08"
        parsed_date = date_parser(year_month_date)

        # Create expected datetime with sentinel time values
        expected_date = datetime(
            2024,
            8,
            1,
            hour=settings.SENTINEL_TIME_HOUR,
            minute=settings.SENTINEL_TIME_MINUTE,
            second=settings.SENTINEL_TIME_SECOND,
            microsecond=settings.SENTINEL_TIME_MICROSECOND,
            tzinfo=ZoneInfo("UTC"),
        )

        self.assertEqual(parsed_date, expected_date)
