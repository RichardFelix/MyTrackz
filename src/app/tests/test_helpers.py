from datetime import date
from unittest.mock import MagicMock, patch

from django.http import HttpRequest
from django.test import TestCase

from app import media_type_config
from app.helpers import (
    form_error_messages,
    format_description,
    minutes_to_hhmm,
    redirect_back,
    tailwind_to_hex,
)
from app.models import Colors, Media, MediaTypes


class HelpersTest(TestCase):
    """Test helper functions."""

    def test_tailwind_to_hex_covers_all_media_types(self):
        """Test that tailwind_to_hex covers all media types defined in Colors."""
        # Get all media types from the Colors enum
        for media_type in MediaTypes:
            color_value = Colors[media_type.name].value
            # Convert from "text-color-400" to "color-500"
            tailwind_color = color_value.replace("text-", "").replace("-400", "-500")
            # Ensure the color is defined in tailwind_to_hex
            self.assertIsNotNone(
                tailwind_to_hex(tailwind_color),
                f"Color for {media_type.name} ({tailwind_color}) not defined",
            )

    def test_minutes_to_hhmm(self):
        """Test conversion of minutes to HH:MM format."""
        # Test minutes only
        self.assertEqual(minutes_to_hhmm(30), "30min")

        # Test hours and minutes
        self.assertEqual(minutes_to_hhmm(90), "1h 30min")
        self.assertEqual(minutes_to_hhmm(125), "2h 05min")

        # Test zero
        self.assertEqual(minutes_to_hhmm(0), "0min")

    @patch("app.helpers.url_has_allowed_host_and_scheme")
    @patch("app.helpers.HttpResponseRedirect")
    @patch("app.helpers.redirect")
    def test_redirect_back_with_next(self, _, mock_http_redirect, mock_url_check):
        """Test redirect_back with a 'next' parameter."""
        mock_url_check.return_value = True
        mock_http_redirect.return_value = "redirected"

        request = MagicMock()
        request.GET = {"next": "http://example.com/path?page=2&sort=name"}

        result = redirect_back(request)

        # Check that we redirected to the URL without the page parameter
        mock_http_redirect.assert_called_once()
        redirect_url = mock_http_redirect.call_args[0][0]
        self.assertEqual(redirect_url, "http://example.com/path?sort=name")
        self.assertEqual(result, "redirected")

    @patch("app.helpers.url_has_allowed_host_and_scheme")
    @patch("app.helpers.redirect")
    def test_redirect_back_without_next(self, mock_redirect, mock_url_check):
        """Test redirect_back without a 'next' parameter."""
        mock_url_check.return_value = False
        mock_redirect.return_value = "home_redirect"

        request = MagicMock()
        request.GET = {}

        result = redirect_back(request)

        mock_redirect.assert_called_once_with("home")
        self.assertEqual(result, "home_redirect")

    def test_get_verb_covers_all_media_types(self):
        """Test that get_verb covers all media types defined in MediaTypes."""
        # Get all media types from the MediaTypes enum
        for media_type in MediaTypes:
            # Ensure both present and past tense verbs are defined
            try:
                media_type_config.get_verb(media_type.value, past_tense=False)
                media_type_config.get_verb(media_type.value, past_tense=True)
            except KeyError:
                self.fail(f"Media type {media_type.name} not defined in get_verb")

    def test_format_description_status_initial(self):
        """Test format_description for initial status changes."""
        # Test initial status settings
        self.assertEqual(
            format_description("status", None, Media.Status.IN_PROGRESS.value, "tv"),
            "Started watching",
        )
        self.assertEqual(
            format_description("status", None, Media.Status.COMPLETED.value, "manga"),
            "Finished reading",
        )
        self.assertEqual(
            format_description("status", None, Media.Status.PLANNING.value, "game"),
            "Added to playing list",
        )
        self.assertEqual(
            format_description("status", None, Media.Status.DROPPED.value, "book"),
            "Stopped reading",
        )
        self.assertEqual(
            format_description("status", None, Media.Status.PAUSED.value, "anime"),
            "Paused watching",
        )
        self.assertEqual(
            format_description("status", None, "Custom Status", "tv"),
            "Status set to Custom Status",
        )

    def test_format_description_status_transitions(self):
        """Test format_description for status transitions."""
        # Test status transitions
        self.assertEqual(
            format_description(
                "status",
                Media.Status.PLANNING.value,
                Media.Status.IN_PROGRESS.value,
                "tv",
            ),
            "Started watching",
        )
        self.assertEqual(
            format_description(
                "status",
                Media.Status.IN_PROGRESS.value,
                Media.Status.COMPLETED.value,
                "manga",
            ),
            "Finished reading",
        )
        self.assertEqual(
            format_description(
                "status",
                Media.Status.IN_PROGRESS.value,
                Media.Status.PAUSED.value,
                "game",
            ),
            "Paused playing",
        )
        self.assertEqual(
            format_description(
                "status",
                Media.Status.PAUSED.value,
                Media.Status.IN_PROGRESS.value,
                "book",
            ),
            "Resumed reading",
        )
        self.assertEqual(
            format_description(
                "status",
                Media.Status.IN_PROGRESS.value,
                Media.Status.DROPPED.value,
                "anime",
            ),
            "Stopped watching",
        )
        self.assertEqual(
            format_description(
                "status",
                Media.Status.COMPLETED.value,
                Media.Status.REPEATING.value,
                "movie",
            ),
            "Started rewatching",
        )
        self.assertEqual(
            format_description(
                "status",
                Media.Status.REPEATING.value,
                Media.Status.COMPLETED.value,
                "manga",
            ),
            "Finished rereading",
        )
        self.assertEqual(
            format_description("status", "Custom1", "Custom2", "tv"),
            "Changed status from Custom1 to Custom2",
        )

    def test_format_description_score(self):
        """Test format_description for score changes."""
        # Initial score
        self.assertEqual(
            format_description("score", None, 8.5, "tv"),
            "Rated 8.5/10",
        )
        self.assertEqual(
            format_description("score", 0, 7.0, "anime"),
            "Rated 7.0/10",
        )
        # Score change
        self.assertEqual(
            format_description("score", 6.5, 8.0, "movie"),
            "Changed rating from 6.5 to 8.0",
        )

    def test_format_description_progress(self):
        """Test format_description for progress changes."""
        # Initial progress
        self.assertEqual(
            format_description("progress", None, 120, "game"),
            "Played for 2h 00min",
        )
        self.assertEqual(
            format_description("progress", None, 5, "book"),
            "Read 5 pages",
        )
        self.assertEqual(
            format_description("progress", None, 10, "manga"),
            "Read 10 chapters",
        )
        self.assertEqual(
            format_description("progress", None, 3, "tv"),
            "Watched 3 episodes",
        )

        # Progress change
        self.assertEqual(
            format_description("progress", 60, 90, "game"),
            "Added 30min of playtime",
        )
        self.assertEqual(
            format_description("progress", 90, 60, "game"),
            "Removed 30min of playtime",
        )
        self.assertEqual(
            format_description("progress", 10, 15, "book"),
            "Read 5 pages",
        )
        self.assertEqual(
            format_description("progress", 5, 10, "manga"),
            "Read 5 chapters",
        )
        self.assertEqual(
            format_description("progress", 3, 5, "tv"),
            "Watched 2 episodes",
        )

    def test_format_description_repeats(self):
        """Test format_description for repeat count changes."""
        # Initial repeat
        self.assertEqual(
            format_description("repeats", None, 0, "tv"),
            "Watched for the first time",
        )
        self.assertEqual(
            format_description("repeats", None, 0, "book"),
            "Read for the first time",
        )
        self.assertEqual(
            format_description("repeats", None, 0, "game"),
            "Played for the first time",
        )

        # Repeat increment
        self.assertEqual(
            format_description("repeats", 0, 1, "tv"),
            "Watched again (#2)",
        )
        self.assertEqual(
            format_description("repeats", 1, 2, "book"),
            "Read again (#3)",
        )

        # Repeat adjustment
        self.assertEqual(
            format_description("repeats", 2, 1, "game"),
            "Adjusted repeat count from 2 to 1",
        )

    def test_format_description_dates(self):
        """Test format_description for date changes."""
        # Initial dates
        start_date = date(2023, 3, 15)
        end_date = date(2023, 4, 20)

        self.assertEqual(
            format_description("start_date", None, start_date),
            "Started on March 15, 2023",
        )
        self.assertEqual(
            format_description("end_date", None, end_date),
            "Finished on April 20, 2023",
        )

        # Date changes
        new_start = date(2023, 5, 1)
        self.assertEqual(
            format_description("start_date", start_date, new_start),
            "Changed start date to May 1, 2023",
        )

        # Date removal
        self.assertEqual(
            format_description("end_date", end_date, None),
            "Removed end date",
        )

    def test_format_description_notes(self):
        """Test format_description for notes changes."""
        # Initial notes
        self.assertEqual(
            format_description("notes", None, "Test notes"),
            "Added notes",
        )

        # Update notes
        self.assertEqual(
            format_description("notes", "Old notes", "New notes"),
            "Updated notes",
        )

        # Remove notes
        self.assertEqual(
            format_description("notes", "Old notes", ""),
            "Removed notes",
        )

    def test_format_description_generic(self):
        """Test format_description for generic field changes."""
        self.assertEqual(
            format_description("custom_field", "old", "new"),
            "Updated custom field from old to new",
        )

    @patch("app.helpers.messages")
    def test_form_error_messages(self, mock_messages):
        """Test form_error_messages function."""
        form = MagicMock()
        form.errors = {
            "title": ["This field is required."],
            "release_date": ["Enter a valid date."],
        }
        request = HttpRequest()

        form_error_messages(form, request)

        # Check that error messages were added
        self.assertEqual(mock_messages.error.call_count, 2)
        mock_messages.error.assert_any_call(request, "Title: This field is required.")
        mock_messages.error.assert_any_call(
            request,
            "Release Date: Enter a valid date.",
        )
