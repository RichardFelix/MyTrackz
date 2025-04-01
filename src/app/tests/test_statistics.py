import datetime
from unittest.mock import MagicMock, patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db.models import Count
from django.test import TestCase
from django.utils import timezone

from app import statistics
from app.models import TV, Episode, Item, Media, Season

User = get_user_model()


class StatisticsTests(TestCase):
    """Test the statistics module functions."""

    def setUp(self):
        """Set up test data."""
        self.credentials = {"username": "testuser", "password": "testpassword"}
        self.user = get_user_model().objects.create_user(**self.credentials)

        # Create some test items
        self.tv_item = Item.objects.create(
            media_id="1668",
            source="tmdb",
            media_type="tv",
            title="Test TV Show",
        )

        # Create season item
        self.season_item = Item.objects.create(
            media_id="1668",
            source="tmdb",
            media_type="season",
            title="Test TV Show",
            season_number=1,
        )

        # Create episode items
        self.episode1_item = Item.objects.create(
            media_id="1668",
            source="tmdb",
            media_type="episode",
            title="Test TV Show",
            season_number=1,
            episode_number=1,
        )

        self.episode2_item = Item.objects.create(
            media_id="1668",
            source="tmdb",
            media_type="episode",
            title="Test TV Show",
            season_number=1,
            episode_number=2,
        )

        self.movie_item = Item.objects.create(
            media_id="238",
            source="tmdb",
            media_type="movie",
            title="Test Movie",
        )

        self.anime_item = Item.objects.create(
            media_id="437",
            source="mal",
            media_type="anime",
            title="Test Anime",
        )

        # Create test media
        self.tv = TV.objects.create(
            user=self.user,
            item=self.tv_item,
            status=Media.Status.IN_PROGRESS.value,
            score=8.5,
        )

        self.season = Season.objects.create(
            user=self.user,
            item=self.season_item,
            related_tv=self.tv,
            status=Media.Status.IN_PROGRESS.value,
            score=8.0,
        )

        # Create episodes
        self.episode1 = Episode.objects.create(
            item=self.episode1_item,
            related_season=self.season,
            end_date=datetime.date(2025, 1, 1),
        )

        self.episode2 = Episode.objects.create(
            item=self.episode2_item,
            related_season=self.season,
            end_date=datetime.date(2025, 1, 15),
        )

        # Create a movie with different dates
        self.movie = apps.get_model("app", "movie").objects.create(
            user=self.user,
            item=self.movie_item,
            status=Media.Status.PLANNING.value,
            score=7.5,
            start_date=datetime.date(2025, 2, 1),
            end_date=datetime.date(2025, 2, 1),
        )

        # Create an anime with different dates
        self.anime = apps.get_model("app", "anime").objects.create(
            user=self.user,
            item=self.anime_item,
            status=Media.Status.COMPLETED.value,
            score=None,
            start_date=datetime.date(2025, 3, 1),
            end_date=datetime.date(2025, 3, 31),
        )

    def test_get_level(self):
        """Test the get_level function."""
        self.assertEqual(statistics.get_level(0), 0)
        self.assertEqual(statistics.get_level(1), 1)
        self.assertEqual(statistics.get_level(3), 1)
        self.assertEqual(statistics.get_level(4), 2)
        self.assertEqual(statistics.get_level(6), 2)
        self.assertEqual(statistics.get_level(7), 3)
        self.assertEqual(statistics.get_level(9), 3)
        self.assertEqual(statistics.get_level(10), 4)
        self.assertEqual(statistics.get_level(20), 4)

    @patch("app.statistics.get_filtered_historical_data")
    def test_get_activity_data(self, mock_get_filtered_data):
        """Test the get_activity_data function."""
        start_date = datetime.date(2025, 1, 1)
        end_date = datetime.date(2025, 3, 31)

        # Mock the historical data
        mock_get_filtered_data.return_value = [
            {"date": datetime.date(2025, 1, 1), "count": 2},
            {"date": datetime.date(2025, 1, 2), "count": 1},
            {"date": datetime.date(2025, 1, 3), "count": 3},
            {"date": datetime.date(2025, 1, 4), "count": 0},
            {"date": datetime.date(2025, 1, 5), "count": 5},
            {"date": datetime.date(2025, 1, 6), "count": 2},
            {"date": datetime.date(2025, 1, 7), "count": 1},
            {"date": datetime.date(2025, 1, 8), "count": 4},
            {"date": datetime.date(2025, 1, 9), "count": 0},
            {"date": datetime.date(2025, 1, 10), "count": 0},
            {"date": datetime.date(2025, 3, 31), "count": 3},  # Last day
        ]

        result = statistics.get_activity_data(self.user, start_date, end_date)

        # Check that the function returns the expected structure
        self.assertIn("calendar_weeks", result)
        self.assertIn("months", result)
        self.assertIn("stats", result)

        # Check stats
        stats = result["stats"]
        self.assertIn("most_active_day", stats)
        self.assertIn("most_active_day_percentage", stats)
        self.assertIn("current_streak", stats)
        self.assertIn("longest_streak", stats)

        # Check calendar data
        calendar_weeks = result["calendar_weeks"]
        self.assertIsInstance(calendar_weeks, list)

        # Verify the first day is aligned to Monday
        first_week = calendar_weeks[0]
        self.assertEqual(len(first_week), 7)  # 7 days in a week

        # Check months data
        months = result["months"]
        self.assertIsInstance(months, list)

    @patch("app.statistics.apps.get_model")
    def test_get_earliest_media_start_date(self, mock_get_model):
        """Test the get_earliest_media_start_date function."""
        # Setup mock models for regular media types
        mock_model = MagicMock()
        mock_model.__name__ = "GenericModel"
        mock_model.objects.filter.return_value.aggregate.return_value = {
            "earliest": datetime.date(2024, 1, 1),
        }

        # Setup mock Episode model
        mock_episode_model = MagicMock()
        mock_episode_model.__name__ = "Episode"
        mock_episode_model.objects.filter.return_value.aggregate.return_value = {
            "earliest": datetime.date(2023, 12, 15),  # Earlier than other models
        }

        # Make get_model return different models based on input
        def side_effect(_, model_name):
            if model_name == "episode":
                return mock_episode_model
            return mock_model

        mock_get_model.side_effect = side_effect

        result = statistics.get_earliest_media_start_date(self.user)

        # Should return the earliest date from all models (Episode in this case)
        self.assertEqual(result, datetime.date(2023, 12, 15))

        # Verify that the function is checking the correct models
        # It should not check 'season' or 'tv' directly as they're excluded
        model_calls = [call[0][1] for call in mock_get_model.call_args_list]
        self.assertNotIn("tv", model_calls)
        self.assertNotIn("season", model_calls)
        self.assertIn("episode", model_calls)

    @patch("app.statistics.apps.get_model")
    def test_get_earliest_historical_date(self, mock_get_model):
        """Test the get_earliest_historical_date function."""
        # Setup mock historical models
        mock_historical_model = MagicMock()
        mock_historical_model.objects.filter.return_value.aggregate.return_value = {
            "earliest": datetime.datetime(
                2024,
                1,
                1,
                12,
                0,
                0,
                tzinfo=timezone.get_current_timezone(),
            ),
        }

        mock_get_model.return_value = mock_historical_model

        # Mock Episode.history
        with patch("app.models.Episode.history") as mock_episode_history:
            mock_episode_history.filter.return_value.aggregate.return_value = {
                "earliest": datetime.datetime(
                    2023,
                    12,
                    1,
                    12,
                    0,
                    0,
                    tzinfo=timezone.get_current_timezone(),
                ),
            }

            # Mock BasicMedia.objects.get_historical_models
            with patch(
                "app.models.BasicMedia.objects.get_historical_models",
            ) as mock_get_historical_models:
                mock_get_historical_models.return_value = [
                    "historicalmodel1",
                    "historicalmodel2",
                ]

                result = statistics.get_earliest_historical_date(self.user)

                # Should return the earliest date from all historical models
                self.assertEqual(result, datetime.date(2023, 12, 1))

    @patch("app.statistics.get_earliest_media_start_date")
    @patch("app.statistics.get_earliest_historical_date")
    @patch("django.core.cache.cache.get")
    @patch("django.core.cache.cache.set")
    def test_get_first_interaction_date(
        self,
        mock_cache_set,
        mock_cache_get,
        mock_get_historical,
        mock_get_media,
    ):
        """Test the get_first_interaction_date function."""
        # Test cache hit
        mock_cache_get.return_value = datetime.date(2024, 1, 1)
        result = statistics.get_first_interaction_date(self.user)
        self.assertEqual(result, datetime.date(2024, 1, 1))

        # Test cache miss, with both dates available
        mock_cache_get.return_value = None
        mock_get_media.return_value = datetime.date(2024, 2, 1)
        mock_get_historical.return_value = datetime.date(2024, 1, 1)

        result = statistics.get_first_interaction_date(self.user)
        self.assertEqual(result, datetime.date(2024, 1, 1))
        mock_cache_set.assert_called_once()

        # Test with only media date
        mock_cache_get.return_value = None
        mock_cache_set.reset_mock()
        mock_get_media.return_value = datetime.date(2024, 2, 1)
        mock_get_historical.return_value = None

        result = statistics.get_first_interaction_date(self.user)
        self.assertEqual(result, datetime.date(2024, 2, 1))
        mock_cache_set.assert_called_once()

        # Test with no dates
        mock_cache_get.return_value = None
        mock_cache_set.reset_mock()
        mock_get_media.return_value = None
        mock_get_historical.return_value = None

        result = statistics.get_first_interaction_date(self.user)
        # Should fall back to user.date_joined
        self.assertEqual(result, self.user.date_joined.date())
        mock_cache_set.assert_called_once()

    @patch("app.statistics.BasicMedia.objects.get_historical_models")
    @patch("app.statistics.apps.get_model")
    def test_get_filtered_historical_data(
        self,
        mock_get_model,
        mock_get_historical_models,
    ):
        """Test the get_filtered_historical_data function."""
        # Setup test dates
        start_date = datetime.date(2025, 1, 1)
        end_date = datetime.date(2025, 3, 31)

        # Mock historical models list
        mock_get_historical_models.return_value = [
            "historicalmodel1",
            "historicalmodel2",
        ]

        # Create mock historical data for first model
        mock_historical_model1 = MagicMock()
        filter_chain1 = mock_historical_model1.objects.filter.return_value
        annotate_chain1 = filter_chain1.annotate.return_value
        values_chain1 = annotate_chain1.values.return_value
        values_chain1.annotate.return_value = [
            {"date": datetime.date(2025, 1, 5), "count": 3},
            {"date": datetime.date(2025, 1, 10), "count": 2},
        ]

        # Create mock historical data for second model
        mock_historical_model2 = MagicMock()
        filter_chain2 = mock_historical_model2.objects.filter.return_value
        annotate_chain2 = filter_chain2.annotate.return_value
        values_chain2 = annotate_chain2.values.return_value
        values_chain2.annotate.return_value = [
            {"date": datetime.date(2025, 2, 15), "count": 1},
            {"date": datetime.date(2025, 3, 20), "count": 4},
        ]

        # Setup the get_model mock to return different models based on input
        def side_effect(_, model_name):
            if model_name == "historicalmodel1":
                return mock_historical_model1
            if model_name == "historicalmodel2":
                return mock_historical_model2
            return MagicMock()

        mock_get_model.side_effect = side_effect

        # Call the function
        result = statistics.get_filtered_historical_data(
            start_date,
            end_date,
            self.user,
        )

        # Verify results
        self.assertEqual(len(result), 4)  # Should have 4 date entries

        # Check that the data from both models is combined
        expected_data = [
            {"date": datetime.date(2025, 1, 5), "count": 3},
            {"date": datetime.date(2025, 1, 10), "count": 2},
            {"date": datetime.date(2025, 2, 15), "count": 1},
            {"date": datetime.date(2025, 3, 20), "count": 4},
        ]

        # Check that all expected data is in the result
        for item in expected_data:
            self.assertIn(item, result)

        # Verify the filter calls were made correctly
        for model_mock in [mock_historical_model1, mock_historical_model2]:
            filter_kwargs = model_mock.objects.filter.call_args[1]
            self.assertEqual(filter_kwargs["history_user_id"], self.user)
            self.assertEqual(filter_kwargs["history_date__date__gte"], start_date)
            self.assertEqual(filter_kwargs["history_date__date__lte"], end_date)

            # Verify the annotation and values calls
            model_mock.objects.filter.return_value.annotate.assert_called_once()
            filter_annotate = model_mock.objects.filter.return_value.annotate
            filter_annotate.return_value.values.assert_called_once_with("date")
            values_return = filter_annotate.return_value.values.return_value
            values_return.annotate.assert_called_once_with(count=Count("id"))

    def test_calculate_day_of_week_stats(self):
        """Test the calculate_day_of_week_stats function."""
        # Create sample date counts
        date_counts = {
            datetime.date(2025, 1, 1): 2,  # Wednesday
            datetime.date(2025, 1, 2): 1,  # Thursday
            datetime.date(2025, 1, 3): 3,  # Friday
            datetime.date(2025, 1, 4): 0,  # Saturday
            datetime.date(2025, 1, 5): 5,  # Sunday
            datetime.date(2025, 1, 6): 2,  # Monday
            datetime.date(2025, 1, 7): 1,  # Tuesday
            datetime.date(2025, 1, 8): 4,  # Wednesday
            datetime.date(2025, 1, 9): 0,  # Thursday
            datetime.date(2025, 1, 10): 0,  # Friday
            datetime.date(2025, 1, 12): 5,  # Sunday
            datetime.date(2025, 1, 19): 3,  # Sunday
        }

        start_date = datetime.date(2025, 1, 1)

        most_active_day, percentage = statistics.calculate_day_of_week_stats(
            date_counts,
            start_date,
        )

        # Sunday has highest count (3 occurrences)
        self.assertEqual(most_active_day, "Sunday")
        # 3 out of 9 active days = ~33%
        self.assertEqual(percentage, 33)

        # Test with empty data
        empty_counts = {}
        most_active_day, percentage = statistics.calculate_day_of_week_stats(
            empty_counts,
            start_date,
        )
        self.assertIsNone(most_active_day)
        self.assertEqual(percentage, 0)

    def test_calculate_streaks(self):
        """Test the calculate_streaks function."""
        # Create sample date counts
        today = datetime.date(2025, 3, 31)
        yesterday = today - datetime.timedelta(days=1)
        two_days_ago = today - datetime.timedelta(days=2)

        # Test current streak
        date_counts = {
            today: 1,
            yesterday: 2,
            two_days_ago: 3,
            datetime.date(2025, 3, 27): 0,
            datetime.date(2025, 3, 26): 1,
            datetime.date(2025, 3, 25): 1,
            datetime.date(2025, 3, 24): 1,
            datetime.date(2025, 3, 23): 1,
            datetime.date(2025, 3, 22): 0,
            datetime.date(2025, 3, 21): 1,
        }

        current_streak, longest_streak = statistics.calculate_streaks(
            date_counts,
            today,
        )

        # Current streak should be 3 (today, yesterday, two days ago)
        self.assertEqual(current_streak, 3)
        # Longest streak should be 4 (Mar 23-26)
        self.assertEqual(longest_streak, 4)

        # Test no current streak
        date_counts = {
            yesterday: 2,
            two_days_ago: 3,
            datetime.date(2025, 3, 27): 0,
            datetime.date(2025, 3, 26): 1,
        }

        current_streak, longest_streak = statistics.calculate_streaks(
            date_counts,
            today,
        )

        # No activity today, so current streak is 0
        self.assertEqual(current_streak, 0)
        # Longest streak should be 2 (Mar 29-30)
        self.assertEqual(longest_streak, 2)

        # Test empty data
        empty_counts = {}
        current_streak, longest_streak = statistics.calculate_streaks(
            empty_counts,
            today,
        )
        self.assertEqual(current_streak, 0)
        self.assertEqual(longest_streak, 0)

    def test_get_user_media(self):
        """Test the get_user_media function."""
        start_date = datetime.date(2025, 1, 1)
        end_date = datetime.date(2025, 3, 31)

        user_media, media_count = statistics.get_user_media(
            self.user,
            start_date,
            end_date,
        )

        # Check that we have the expected media types
        self.assertIn("tv", user_media)
        self.assertIn("season", user_media)
        self.assertIn("movie", user_media)
        self.assertIn("anime", user_media)

        # Check counts
        self.assertEqual(media_count["total"], 4)  # TV, Season, Movie, Anime
        self.assertEqual(media_count["tv"], 1)
        self.assertEqual(media_count["season"], 1)
        self.assertEqual(media_count["movie"], 1)
        self.assertEqual(media_count["anime"], 1)

        # Test with different date range
        start_date = datetime.date(2025, 2, 1)
        end_date = datetime.date(2025, 2, 28)

        user_media, media_count = statistics.get_user_media(
            self.user,
            start_date,
            end_date,
        )

        # Should only include movie (TV, Season, and episodes are in January)
        self.assertEqual(media_count["total"], 1)
        self.assertEqual(media_count["movie"], 1)
        self.assertEqual(media_count["tv"], 0)
        self.assertEqual(media_count["season"], 0)

    def test_get_media_type_distribution(self):
        """Test the get_media_type_distribution function."""
        media_count = {
            "total": 3,
            "tv": 1,
            "movie": 1,
            "anime": 1,
            "book": 0,  # Should be excluded
        }

        chart_data = statistics.get_media_type_distribution(media_count)

        # Check structure
        self.assertIn("labels", chart_data)
        self.assertIn("datasets", chart_data)
        self.assertEqual(len(chart_data["datasets"]), 1)
        self.assertIn("data", chart_data["datasets"][0])
        self.assertIn("backgroundColor", chart_data["datasets"][0])

        # Check content
        self.assertEqual(len(chart_data["labels"]), 3)  # 3 media types with count > 0
        self.assertEqual(len(chart_data["datasets"][0]["data"]), 3)
        self.assertEqual(len(chart_data["datasets"][0]["backgroundColor"]), 3)

        # Book should be excluded (count = 0)
        self.assertNotIn("Book", chart_data["labels"])

    def test_get_status_distribution(self):
        """Test the get_status_distribution function."""
        # Create user_media dict with our test objects
        user_media = {
            "tv": TV.objects.filter(user=self.user),
            "movie": apps.get_model("app", "movie").objects.filter(user=self.user),
            "anime": apps.get_model("app", "anime").objects.filter(user=self.user),
        }

        status_distribution = statistics.get_status_distribution(user_media)

        # Check structure
        self.assertIn("labels", status_distribution)
        self.assertIn("datasets", status_distribution)
        self.assertIn("total_completed", status_distribution)

        # Check content
        self.assertEqual(len(status_distribution["labels"]), 3)  # 3 media types
        self.assertEqual(
            len(status_distribution["datasets"]),
            len(Media.Status.values),
        )  # All statuses

        # Check total completed count
        self.assertEqual(
            status_distribution["total_completed"],
            1,
        )  # Only anime is completed

        # Check individual status counts
        completed_dataset = next(
            d
            for d in status_distribution["datasets"]
            if d["label"] == Media.Status.COMPLETED.value
        )
        in_progress_dataset = next(
            d
            for d in status_distribution["datasets"]
            if d["label"] == Media.Status.IN_PROGRESS.value
        )
        planning_dataset = next(
            d
            for d in status_distribution["datasets"]
            if d["label"] == Media.Status.PLANNING.value
        )

        self.assertEqual(completed_dataset["total"], 1)  # Anime
        self.assertEqual(in_progress_dataset["total"], 1)  # TV
        self.assertEqual(planning_dataset["total"], 1)  # Movie

    def test_get_status_pie_chart_data(self):
        """Test the get_status_pie_chart_data function."""
        # Create sample status distribution
        status_distribution = {
            "labels": ["TV", "Movie", "Anime"],
            "datasets": [
                {
                    "label": Media.Status.COMPLETED.value,
                    "data": [1, 0, 0],
                    "background_color": "#10b981",
                    "total": 1,
                },
                {
                    "label": Media.Status.IN_PROGRESS.value,
                    "data": [0, 1, 0],
                    "background_color": "#6366f1",
                    "total": 1,
                },
                {
                    "label": Media.Status.PLANNING.value,
                    "data": [0, 0, 1],
                    "background_color": "#3b82f6",
                    "total": 1,
                },
                {
                    "label": Media.Status.PAUSED.value,
                    "data": [0, 0, 0],
                    "background_color": "#f97316",
                    "total": 0,
                },
            ],
            "total_completed": 1,
        }

        chart_data = statistics.get_status_pie_chart_data(status_distribution)

        # Check structure
        self.assertIn("labels", chart_data)
        self.assertIn("datasets", chart_data)
        self.assertEqual(len(chart_data["datasets"]), 1)
        self.assertIn("data", chart_data["datasets"][0])
        self.assertIn("backgroundColor", chart_data["datasets"][0])

        # Check content - should only include statuses with count > 0
        self.assertEqual(len(chart_data["labels"]), 3)
        self.assertEqual(len(chart_data["datasets"][0]["data"]), 3)
        self.assertEqual(len(chart_data["datasets"][0]["backgroundColor"]), 3)

        # PAUSED status should be excluded (total = 0)
        self.assertNotIn(Media.Status.PAUSED.value, chart_data["labels"])

    def test_get_score_distribution(self):
        """Test the get_score_distribution function."""
        # Create user_media dict with our test objects
        user_media = {
            "tv": TV.objects.filter(user=self.user),
            "movie": apps.get_model("app", "movie").objects.filter(user=self.user),
            "anime": apps.get_model("app", "anime").objects.filter(user=self.user),
        }

        score_distribution = statistics.get_score_distribution(user_media)

        # Check structure
        self.assertIn("labels", score_distribution)
        self.assertIn("datasets", score_distribution)
        self.assertIn("average_score", score_distribution)
        self.assertIn("total_scored", score_distribution)
        self.assertIn("top_rated", score_distribution)

        # Check content
        self.assertEqual(len(score_distribution["labels"]), 11)  # Scores 0-10
        self.assertEqual(len(score_distribution["datasets"]), 3)  # 3 media types

        # Check average score and total scored
        self.assertEqual(
            score_distribution["total_scored"],
            2,
        )  # TV and Movie have scores
        self.assertEqual(score_distribution["average_score"], 8.0)  # (8.5 + 7.5) / 2

        # Check top rated
        self.assertEqual(
            len(score_distribution["top_rated"]),
            2,
        )  # Only 2 items have scores
        self.assertEqual(
            score_distribution["top_rated"][0]["score"],
            8.5,
        )  # TV should be first
        self.assertEqual(
            score_distribution["top_rated"][1]["score"],
            7.5,
        )  # Movie should be second

    def test_get_status_color(self):
        """Test the get_status_color function."""
        # Test all status colors
        for status in Media.Status.values:
            color = statistics.get_status_color(status)
            self.assertIsNotNone(color)
            self.assertTrue(color.startswith("#"))

        # Test unknown status
        unknown_color = statistics.get_status_color("unknown")
        self.assertEqual(unknown_color, "rgba(201, 203, 207)")

    def test_get_timeline(self):
        """Test the get_timeline function."""
        # Create user_media dict with our test objects
        user_media = {
            "tv": TV.objects.filter(user=self.user),
            "season": Season.objects.filter(user=self.user),
            "movie": apps.get_model("app", "movie").objects.filter(user=self.user),
            "anime": apps.get_model("app", "anime").objects.filter(user=self.user),
        }

        timeline = statistics.get_timeline(user_media)

        # Check structure - should be a dict with month-year keys
        self.assertIsInstance(timeline, dict)

        # Check content
        self.assertIn("January 2025", timeline)  # Season spans Jan 1-15
        self.assertIn("February 2025", timeline)  # Movie on Feb 1
        self.assertIn("March 2025", timeline)  # Anime starts on Mar 1

        # Check items in each month
        self.assertEqual(len(timeline["January 2025"]), 1)  # Season
        self.assertEqual(len(timeline["February 2025"]), 1)  # Movie
        self.assertEqual(len(timeline["March 2025"]), 1)  # Anime

        # Check sorting - should be in chronological order
        months = list(timeline.keys())
        self.assertEqual(months[0], "March 2025")  # Most recent first
        self.assertEqual(months[1], "February 2025")
        self.assertEqual(months[2], "January 2025")
