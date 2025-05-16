from datetime import UTC, datetime

from django.test import TestCase
from django.utils import timezone

from app import media_type_config
from app.history_processor import format_description
from app.models import Media, MediaTypes


class HistoryProcessorTests(TestCase):
    """Test the history processor functions."""

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
            format_description(
                "status",
                None,
                Media.Status.IN_PROGRESS.value,
                MediaTypes.TV.value,
            ),
            "Started watching",
        )
        self.assertEqual(
            format_description(
                "status",
                None,
                Media.Status.COMPLETED.value,
                MediaTypes.MANGA.value,
            ),
            "Finished reading",
        )
        self.assertEqual(
            format_description(
                "status",
                None,
                Media.Status.PLANNING.value,
                MediaTypes.GAME.value,
            ),
            "Added to playing list",
        )
        self.assertEqual(
            format_description(
                "status",
                None,
                Media.Status.DROPPED.value,
                MediaTypes.BOOK.value,
            ),
            "Stopped reading",
        )
        self.assertEqual(
            format_description(
                "status",
                None,
                Media.Status.PAUSED.value,
                MediaTypes.ANIME.value,
            ),
            "Paused watching",
        )
        self.assertEqual(
            format_description("status", None, "Custom Status", MediaTypes.TV.value),
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
                MediaTypes.TV.value,
            ),
            "Started watching",
        )
        self.assertEqual(
            format_description(
                "status",
                Media.Status.IN_PROGRESS.value,
                Media.Status.COMPLETED.value,
                MediaTypes.MANGA.value,
            ),
            "Finished reading",
        )
        self.assertEqual(
            format_description(
                "status",
                Media.Status.IN_PROGRESS.value,
                Media.Status.PAUSED.value,
                MediaTypes.GAME.value,
            ),
            "Paused playing",
        )
        self.assertEqual(
            format_description(
                "status",
                Media.Status.PAUSED.value,
                Media.Status.IN_PROGRESS.value,
                MediaTypes.BOOK.value,
            ),
            "Resumed reading",
        )
        self.assertEqual(
            format_description(
                "status",
                Media.Status.IN_PROGRESS.value,
                Media.Status.DROPPED.value,
                MediaTypes.ANIME.value,
            ),
            "Stopped watching",
        )
        self.assertEqual(
            format_description(
                "status",
                Media.Status.COMPLETED.value,
                Media.Status.REPEATING.value,
                MediaTypes.MOVIE.value,
            ),
            "Started rewatching",
        )
        self.assertEqual(
            format_description(
                "status",
                Media.Status.REPEATING.value,
                Media.Status.COMPLETED.value,
                MediaTypes.MANGA.value,
            ),
            "Finished rereading",
        )
        self.assertEqual(
            format_description("status", "Custom1", "Custom2", MediaTypes.TV.value),
            "Changed status from Custom1 to Custom2",
        )

    def test_format_description_score(self):
        """Test format_description for score changes."""
        # Initial score
        self.assertEqual(
            format_description("score", None, 8.5, MediaTypes.TV.value),
            "Rated 8.5/10",
        )
        self.assertEqual(
            format_description("score", 0, 7.0, MediaTypes.ANIME.value),
            "Rated 7.0/10",
        )
        # Score change
        self.assertEqual(
            format_description("score", 6.5, 8.0, MediaTypes.MOVIE.value),
            "Changed rating from 6.5 to 8.0",
        )

    def test_format_description_progress(self):
        """Test format_description for progress changes."""
        # Initial progress
        self.assertEqual(
            format_description("progress", None, 120, MediaTypes.GAME.value),
            "Played for 2h 00min",
        )
        self.assertEqual(
            format_description("progress", None, 5, MediaTypes.BOOK.value),
            "Read 5 pages",
        )
        self.assertEqual(
            format_description("progress", None, 10, MediaTypes.MANGA.value),
            "Read 10 chapters",
        )

        # Progress change
        self.assertEqual(
            format_description("progress", 60, 90, MediaTypes.GAME.value),
            "Added 30min of playtime",
        )
        self.assertEqual(
            format_description("progress", 90, 60, MediaTypes.GAME.value),
            "Removed 30min of playtime",
        )
        self.assertEqual(
            format_description("progress", 10, 15, MediaTypes.BOOK.value),
            "Read 5 pages",
        )
        self.assertEqual(
            format_description("progress", 5, 10, MediaTypes.MANGA.value),
            "Read 5 chapters",
        )

    def test_format_description_repeats(self):
        """Test format_description for repeat count changes."""
        # Repeat increment
        self.assertEqual(
            format_description("repeats", 0, 1, MediaTypes.TV.value),
            "Watched again for the 2nd time",
        )
        self.assertEqual(
            format_description("repeats", 1, 2, MediaTypes.BOOK.value),
            "Read again for the 3rd time",
        )

        # Repeat adjustment
        self.assertEqual(
            format_description("repeats", 2, 1, MediaTypes.GAME.value),
            "Adjusted repeat count from 2 to 1",
        )

    def test_format_description_dates(self):
        """Test format_description for date changes."""
        # Initial dates
        start_date = datetime(2023, 3, 15, 0, 0, 0, tzinfo=UTC)
        end_date = datetime(2023, 4, 20, 0, 0, 0, tzinfo=UTC)

        start_date_local = timezone.localtime(start_date)
        end_date_local = timezone.localtime(end_date)

        self.assertEqual(
            format_description("start_date", None, start_date),
            f"Started on {start_date_local.strftime('%Y-%m-%d %H:%M')}",
        )
        self.assertEqual(
            format_description("end_date", None, end_date),
            f"Finished on {end_date_local.strftime('%Y-%m-%d %H:%M')}",
        )

        # Date changes
        new_start = datetime(2023, 5, 1, 0, 0, 0, tzinfo=UTC)
        new_start_local = timezone.localtime(new_start)
        self.assertEqual(
            format_description("start_date", start_date, new_start),
            (
                f"Changed start date from {start_date_local.strftime('%Y-%m-%d %H:%M')}"
                f" to {new_start_local.strftime('%Y-%m-%d %H:%M')}"
            ),
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
