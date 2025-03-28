from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from app.models import Anime, Book, Item, Manga, Movie, Season
from events.models import Event
from events.tasks import (
    anilist_date_parser,
    date_parser,
    get_anime_schedule_bulk,
    get_tvmaze_episode_map,
    get_user_reloaded,
    process_anime_bulk,
    process_item,
    reload_calendar,
)


class ReloadCalendarTaskTests(TestCase):
    """Test the reload_calendar task."""

    def setUp(self):
        """Set up the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

        # Create anime item
        self.anime_item = Item.objects.create(
            media_id="437",
            source="mal",
            media_type="anime",
            title="Perfect Blue",
            image="http://example.com/image.jpg",
        )
        Anime.objects.create(item=self.anime_item, user=self.user, status="Planning")

        # Create movie item
        self.movie_item = Item.objects.create(
            media_id="238",
            source="tmdb",
            media_type="movie",
            title="The Godfather",
            image="http://example.com/thegodfather.jpg",
        )
        Movie.objects.create(item=self.movie_item, user=self.user, status="Planning")

        # Create season item
        self.season_item = Item.objects.create(
            media_id="1396",
            source="tmdb",
            media_type="season",
            title="Breaking Bad",
            image="http://example.com/breakingbad.jpg",
            season_number=1,
        )
        Season.objects.create(item=self.season_item, user=self.user, status="Planning")

        # Create manga item
        self.manga_item = Item.objects.create(
            media_id="1",
            source="mal",
            media_type="manga",
            title="Berserk",
            image="http://example.com/berserk.jpg",
        )
        Manga.objects.create(item=self.manga_item, user=self.user, status="Planning")

        # Create book item
        self.book_item = Item.objects.create(
            media_id="OL21733390M",
            source="openlibrary",
            media_type="book",
            title="1984",
            image="http://example.com/1984.jpg",
        )
        Book.objects.create(item=self.book_item, user=self.user, status="Planning")

    @patch("events.tasks.process_item")
    @patch("events.tasks.process_anime_bulk")
    def test_reload_calendar_all_types(
        self,
        mock_process_anime_bulk,
        mock_process_item,
    ):
        """Test reload_calendar with all media types."""
        # Setup mocks
        mock_process_item.side_effect = lambda item, events_bulk: events_bulk.append(
            Event(
                item=item,
                episode_number=1,
                datetime=timezone.now(),
            ),
        )
        # Setup mock for process_anime_bulk to create events for anime items
        mock_process_anime_bulk.side_effect = lambda items, events_bulk: [
            events_bulk.append(
                Event(
                    item=item,
                    episode_number=1,
                    datetime=timezone.now(),
                ),
            )
            for item in items
        ]

        # Call the task
        result = reload_calendar(self.user.id)

        # Verify process_anime_bulk was called for anime items
        mock_process_anime_bulk.assert_called_once()
        anime_items = mock_process_anime_bulk.call_args[0][0]
        self.assertEqual(len(anime_items), 1)
        self.assertEqual(anime_items[0].id, self.anime_item.id)

        # Verify process_item was called for non-anime items
        # 5 items: movie, tv, season, manga, book
        self.assertEqual(mock_process_item.call_count, 5)

        # Verify events were created
        self.assertTrue(Event.objects.filter(item=self.anime_item).exists())
        self.assertTrue(Event.objects.filter(item=self.movie_item).exists())
        self.assertTrue(Event.objects.filter(item=self.season_item).exists())
        self.assertTrue(Event.objects.filter(item=self.manga_item).exists())
        self.assertTrue(Event.objects.filter(item=self.book_item).exists())

        # Verify result message
        self.assertIn("The following items have been loaded to the calendar", result)
        self.assertIn("Perfect Blue", result)
        self.assertIn("The Godfather", result)
        self.assertIn("Breaking Bad", result)
        self.assertIn("Berserk", result)
        self.assertIn("1984", result)

    @patch("events.tasks.process_item")
    @patch("events.tasks.process_anime_bulk")
    def test_reload_calendar_no_changes(
        self,
        mock_process_anime_bulk,
        mock_process_item,
    ):
        """Test reload_calendar with no changes."""
        # Setup mocks to not add any events
        mock_process_item.return_value = None
        mock_process_anime_bulk.return_value = None

        # Call the task
        result = reload_calendar(self.user.id)

        # Verify result message
        self.assertEqual("There have been no changes in the calendar", result)

    @patch("events.tasks.process_item")
    def test_reload_calendar_specific_items(self, mock_process_item):
        """Test reload_calendar with specific items to process."""
        # Setup mock
        mock_process_item.side_effect = lambda item, events_bulk: events_bulk.append(
            Event(
                item=item,
                episode_number=1,
                datetime=timezone.now(),
            ),
        )

        # Call the task with specific items
        items_to_process = [self.movie_item, self.book_item]
        result = reload_calendar(self.user.id, items_to_process)

        # Verify process_item was called only for specified items
        self.assertEqual(mock_process_item.call_count, 2)

        # Verify events were created only for specified items
        self.assertFalse(Event.objects.filter(item=self.anime_item).exists())
        self.assertTrue(Event.objects.filter(item=self.movie_item).exists())
        self.assertFalse(Event.objects.filter(item=self.season_item).exists())
        self.assertFalse(Event.objects.filter(item=self.manga_item).exists())
        self.assertTrue(Event.objects.filter(item=self.book_item).exists())

        # Verify result message
        self.assertIn("The following items have been loaded to the calendar", result)
        self.assertIn("The Godfather", result)
        self.assertIn("1984", result)
        self.assertNotIn("Perfect Blue", result)
        self.assertNotIn("Breaking Bad", result)
        self.assertNotIn("Berserk", result)

    @patch("events.tasks.services.get_media_metadata")
    def test_process_item_movie(self, mock_get_media_metadata):
        """Test process_item for a movie."""
        # Setup mock
        mock_get_media_metadata.return_value = {
            "details": {
                "release_date": "1999-10-15",
            },
        }

        # Process the item
        events_bulk = []
        process_item(self.movie_item, events_bulk)

        # Verify event was added
        self.assertEqual(len(events_bulk), 1)
        self.assertEqual(events_bulk[0].item, self.movie_item)
        self.assertEqual(
            events_bulk[0].episode_number,
            1,
        )  # Movies have episode_number=1

        # Verify the date was parsed correctly
        expected_date = date_parser("1999-10-15")
        self.assertEqual(events_bulk[0].datetime, expected_date)

    @patch("events.tasks.services.get_media_metadata")
    def test_process_item_book(self, mock_get_media_metadata):
        """Test process_item for a book."""
        # Setup mock
        mock_get_media_metadata.return_value = {
            "details": {
                "publish_date": "1949-06-08",
                "number_of_pages": 328,
            },
        }

        # Process the item
        events_bulk = []
        process_item(self.book_item, events_bulk)

        # Verify event was added
        self.assertEqual(len(events_bulk), 1)
        self.assertEqual(events_bulk[0].item, self.book_item)
        self.assertEqual(events_bulk[0].episode_number, 328)  # Books use page count

        # Verify the date was parsed correctly
        expected_date = date_parser("1949-06-08")
        self.assertEqual(events_bulk[0].datetime, expected_date)

    @patch("events.tasks.tmdb.tv_with_seasons")
    @patch("events.tasks.get_tvmaze_episode_map")
    def test_process_item_season(
        self,
        mock_get_tvmaze_episode_map,
        mock_tv_with_seasons,
    ):
        """Test process_item for a TV season."""
        # Setup mocks
        mock_tv_with_seasons.return_value = {
            "season/1": {
                "season_number": 1,
                "episodes": [
                    {"episode_number": 1, "air_date": "2008-01-20"},
                    {"episode_number": 2, "air_date": "2008-01-27"},
                    {"episode_number": 3, "air_date": "2008-02-03"},
                ],
                "external_ids": {"tvdb_id": "81189"},
            },
        }

        # TVMaze data with more precise timestamps
        mock_get_tvmaze_episode_map.return_value = {
            "1_1": {"airstamp": "2008-01-20T22:00:00+00:00", "airtime": "22:00"},
            "1_2": {"airstamp": "2008-01-27T22:00:00+00:00", "airtime": "22:00"},
            "1_3": {"airstamp": "2008-02-03T22:00:00+00:00", "airtime": "22:00"},
        }

        # Process the item
        events_bulk = []
        process_item(self.season_item, events_bulk)

        # Verify events were added
        self.assertEqual(len(events_bulk), 3)

        # Verify the first episode
        self.assertEqual(events_bulk[0].item, self.season_item)
        self.assertEqual(events_bulk[0].episode_number, 1)

        # Verify TVMaze data was used (more precise timestamp)
        expected_date = datetime.fromisoformat("2008-01-20T22:00:00+00:00")
        self.assertEqual(events_bulk[0].datetime, expected_date)

    @patch("events.tasks.services.get_media_metadata")
    def test_process_item_manga(self, mock_get_media_metadata):
        """Test process_item for manga."""
        # Setup mock
        mock_get_media_metadata.return_value = {
            "details": {
                "start_date": "1989-08-25",
                "end_date": "2023-12-22",  # Completed manga
            },
            "max_progress": 375,  # Total chapters
        }

        # Process the item
        events_bulk = []
        process_item(self.manga_item, events_bulk)

        # Verify events were added
        self.assertEqual(len(events_bulk), 2)

        # First event should be for the start date
        self.assertEqual(events_bulk[0].item, self.manga_item)
        self.assertIsNone(events_bulk[0].episode_number)
        expected_start_date = date_parser("1989-08-25")
        self.assertEqual(events_bulk[0].datetime, expected_start_date)

        # Second event should be for the final chapter
        self.assertEqual(events_bulk[1].item, self.manga_item)
        self.assertEqual(events_bulk[1].episode_number, 375)
        expected_end_date = date_parser("2023-12-22")
        self.assertEqual(events_bulk[1].datetime, expected_end_date)

    @patch("events.tasks.services.api_request")
    def test_get_anime_schedule_bulk(self, mock_api_request):
        """Test get_anime_schedule_bulk function."""
        # Setup mock
        mock_api_request.return_value = {
            "data": {
                "Page": {
                    "pageInfo": {"hasNextPage": False},
                    "media": [
                        {
                            "idMal": 437,
                            "startDate": {"year": 1997, "month": 8, "day": 5},
                            "endDate": {"year": 1997, "month": 8, "day": 5},
                            "episodes": 1,
                            "airingSchedule": {
                                "nodes": [
                                    {"episode": 1, "airingAt": 870739200},  # 1997-08-05
                                ],
                            },
                        },
                    ],
                },
            },
        }

        # Call the function
        result = get_anime_schedule_bulk(["437"])

        # Verify result
        self.assertIn("437", result)
        self.assertEqual(len(result["437"]), 1)
        self.assertEqual(result["437"][0]["episode"], 1)
        self.assertEqual(result["437"][0]["airingAt"], 870739200)

    @patch("events.tasks.services.api_request")
    def test_get_anime_schedule_bulk_no_airing_schedule(self, mock_api_request):
        """Test get_anime_schedule_bulk with no airing schedule."""
        # Setup mock
        mock_api_request.return_value = {
            "data": {
                "Page": {
                    "pageInfo": {"hasNextPage": False},
                    "media": [
                        {
                            "idMal": 437,
                            "startDate": {"year": 1997, "month": 8, "day": 5},
                            "endDate": {"year": 1997, "month": 8, "day": 12},
                            "episodes": 2,
                            "airingSchedule": {"nodes": []},  # Empty airing schedule
                        },
                    ],
                },
            },
        }

        # Call the function
        result = get_anime_schedule_bulk(["437"])

        # Verify result - should create a basic schedule from dates
        self.assertIn("437", result)
        self.assertEqual(len(result["437"]), 2)  # Start and end date
        self.assertEqual(result["437"][0]["episode"], 1)

        # Convert timestamp to datetime for easier verification
        start_date = datetime.fromtimestamp(
            result["437"][0]["airingAt"],
            tz=ZoneInfo("UTC"),
        )
        self.assertEqual(start_date.year, 1997)
        self.assertEqual(start_date.month, 8)
        self.assertEqual(start_date.day, 5)

    @patch("events.tasks.services.api_request")
    def test_get_anime_schedule_bulk_filter_episodes(self, mock_api_request):
        """Test get_anime_schedule_bulk filtering episodes beyond total count."""
        # Setup mock with more episodes in schedule than total episodes
        mock_api_request.return_value = {
            "data": {
                "Page": {
                    "pageInfo": {"hasNextPage": False},
                    "media": [
                        {
                            "idMal": 437,
                            "startDate": {"year": 1997, "month": 8, "day": 5},
                            "endDate": {"year": 1997, "month": 8, "day": 5},
                            "episodes": 1,  # Only 1 episode total
                            "airingSchedule": {
                                "nodes": [
                                    {"episode": 1, "airingAt": 870739200},  # Valid
                                    {
                                        "episode": 2,
                                        "airingAt": 870825600,
                                    },  # Should be filtered
                                ],
                            },
                        },
                    ],
                },
            },
        }

        # Call the function
        result = get_anime_schedule_bulk(["437"])

        # Verify result - should filter out episode 2
        self.assertIn("437", result)
        self.assertEqual(len(result["437"]), 1)  # Only episode 1
        self.assertEqual(result["437"][0]["episode"], 1)

    @patch("events.tasks.services.api_request")
    def test_get_tvmaze_episode_map(self, mock_api_request):
        """Test get_tvmaze_episode_map function."""
        # Clear cache first
        cache.clear()

        # Setup mocks for the two API calls
        mock_api_request.side_effect = [
            # First call - lookup show
            {"id": 12345},
            # Second call - get episodes
            {
                "_embedded": {
                    "episodes": [
                        {
                            "season": 1,
                            "number": 1,
                            "airstamp": "2008-01-20T22:00:00+00:00",
                            "airtime": "22:00",
                        },
                        {
                            "season": 1,
                            "number": 2,
                            "airstamp": "2008-01-27T22:00:00+00:00",
                            "airtime": "22:00",
                        },
                    ],
                },
            },
        ]

        # Call the function
        result = get_tvmaze_episode_map("81189")

        # Verify result
        self.assertEqual(len(result), 2)
        self.assertIn("1_1", result)
        self.assertIn("1_2", result)
        self.assertEqual(result["1_1"]["airstamp"], "2008-01-20T22:00:00+00:00")
        self.assertEqual(result["1_2"]["airstamp"], "2008-01-27T22:00:00+00:00")

        # Verify cache was set
        cached_result = cache.get("tvmaze_map_81189")
        self.assertEqual(cached_result, result)

        # Reset mock and call again - should use cache
        mock_api_request.reset_mock()
        cached_result = get_tvmaze_episode_map("81189")
        mock_api_request.assert_not_called()  # Should not call API again

    @patch("events.tasks.services.api_request")
    def test_get_tvmaze_episode_map_lookup_failure(self, mock_api_request):
        """Test get_tvmaze_episode_map when lookup fails."""
        # Clear cache first
        cache.clear()

        # Setup mock to return empty response for lookup
        mock_api_request.return_value = None

        # Call the function
        result = get_tvmaze_episode_map("invalid_id")

        # Verify result is empty
        self.assertEqual(result, {})

        # Should only have called the API once (for lookup)
        mock_api_request.assert_called_once()

    def test_anilist_date_parser(self):
        """Test anilist_date_parser function."""
        # Test with complete date
        complete_date = {"year": 2024, "month": 3, "day": 28}
        result = anilist_date_parser(complete_date)

        # Convert timestamp to datetime for verification
        dt = datetime.fromtimestamp(result, tz=ZoneInfo("UTC"))
        self.assertEqual(dt.year, 2024)
        self.assertEqual(dt.month, 3)
        self.assertEqual(dt.day, 28)

        # Test with partial date (missing day)
        partial_date = {"year": 2024, "month": 3, "day": None}
        result = anilist_date_parser(partial_date)

        # Convert timestamp to datetime for verification
        dt = datetime.fromtimestamp(result, tz=ZoneInfo("UTC"))
        self.assertEqual(dt.year, 2024)
        self.assertEqual(dt.month, 3)
        self.assertEqual(dt.day, 1)  # Default to 1

        # Test with partial date (missing month and day)
        year_only_date = {"year": 2024, "month": None, "day": None}
        result = anilist_date_parser(year_only_date)

        # Convert timestamp to datetime for verification
        dt = datetime.fromtimestamp(result, tz=ZoneInfo("UTC"))
        self.assertEqual(dt.year, 2024)
        self.assertEqual(dt.month, 1)  # Default to 1
        self.assertEqual(dt.day, 1)  # Default to 1

        # Test with missing year
        missing_year = {"year": None, "month": 3, "day": 28}
        result = anilist_date_parser(missing_year)
        self.assertIsNone(result)

    def test_get_user_reloaded(self):
        """Test get_user_reloaded function."""
        # Create events
        events_bulk = [
            Event(
                item=self.anime_item,
                episode_number=1,
                datetime=timezone.now(),
            ),
            Event(
                item=self.movie_item,
                episode_number=1,
                datetime=timezone.now(),
            ),
        ]

        # Get reloaded items for user
        reloaded_items = get_user_reloaded(events_bulk, self.user)

        # Verify result
        self.assertEqual(reloaded_items.count(), 2)
        self.assertIn(self.anime_item, reloaded_items)
        self.assertIn(self.movie_item, reloaded_items)

        # Create a second user who doesn't track these items
        credentials = {"username": "test2", "password": "12345"}
        user2 = get_user_model().objects.create_user(**credentials)

        # Get reloaded items for second user
        reloaded_items = get_user_reloaded(events_bulk, user2)

        # Verify result is empty
        self.assertEqual(reloaded_items.count(), 0)

    def test_get_user_reloaded_empty(self):
        """Test get_user_reloaded with empty events list."""
        # Empty events list
        events_bulk = []

        # Get reloaded items for user
        reloaded_items = get_user_reloaded(events_bulk, self.user)

        # Verify result is empty
        self.assertEqual(reloaded_items.count(), 0)

    @patch("events.tasks.services.get_media_metadata")
    def test_process_other_invalid_date(self, mock_get_media_metadata):
        """Test process_other with invalid date."""
        # Setup mock with invalid date
        mock_get_media_metadata.return_value = {
            "details": {
                "release_date": "invalid-date",
            },
        }

        # Process the item
        events_bulk = []
        process_item(self.movie_item, events_bulk)

        # Verify no events were added
        self.assertEqual(len(events_bulk), 0)

    @patch("events.tasks.services.get_media_metadata")
    def test_process_other_no_date(self, mock_get_media_metadata):
        """Test process_other with no date."""
        # Setup mock with no date
        mock_get_media_metadata.return_value = {
            "details": {},
        }

        # Process the item
        events_bulk = []
        process_item(self.movie_item, events_bulk)

        # Verify no events were added
        self.assertEqual(len(events_bulk), 0)

    @patch("events.tasks.services.api_request")
    def test_process_anime_bulk(self, mock_api_request):
        """Test process_anime_bulk function."""
        # Setup mock
        mock_api_request.return_value = {
            "data": {
                "Page": {
                    "pageInfo": {"hasNextPage": False},
                    "media": [
                        {
                            "idMal": 437,
                            "startDate": {"year": 1997, "month": 8, "day": 5},
                            "endDate": {"year": 1997, "month": 8, "day": 5},
                            "episodes": 1,
                            "airingSchedule": {
                                "nodes": [
                                    {"episode": 1, "airingAt": 870739200},  # 1997-08-05
                                ],
                            },
                        },
                    ],
                },
            },
        }

        # Process anime items
        events_bulk = []
        process_anime_bulk([self.anime_item], events_bulk)

        # Verify events were added
        self.assertEqual(len(events_bulk), 1)
        self.assertEqual(events_bulk[0].item, self.anime_item)
        self.assertEqual(events_bulk[0].episode_number, 1)

        # Convert timestamp to datetime for verification
        expected_date = datetime.fromtimestamp(870739200, tz=ZoneInfo("UTC"))
        self.assertEqual(events_bulk[0].datetime, expected_date)

    @patch("events.tasks.services.api_request")
    def test_process_anime_bulk_no_matching_anime(self, mock_api_request):
        """Test process_anime_bulk with no matching anime."""
        # Setup mock with empty media list
        mock_api_request.return_value = {
            "data": {
                "Page": {
                    "pageInfo": {"hasNextPage": False},
                    "media": [],  # No matching anime
                },
            },
        }

        # Process anime items
        events_bulk = []
        process_anime_bulk([self.anime_item], events_bulk)

        # Verify no events were added
        self.assertEqual(len(events_bulk), 0)

    @patch("events.tasks.services.get_media_metadata")
    def test_http_error_handling(self, mock_get_media_metadata):
        """Test handling of HTTP errors in process_item."""
        # Setup mock to raise 404 error
        import requests

        response_mock = MagicMock()
        response_mock.status_code = 404
        response_mock.json.return_value = {"error": "Not found"}

        http_error = requests.exceptions.HTTPError("404 Client Error")
        http_error.response = response_mock
        mock_get_media_metadata.side_effect = http_error

        # Process the item - should not raise exception
        events_bulk = []
        process_item(self.movie_item, events_bulk)

        # Verify no events were added
        self.assertEqual(len(events_bulk), 0)

        # Setup mock to raise 500 error
        response_mock.status_code = 500
        http_error = requests.exceptions.HTTPError("500 Server Error")
        http_error.response = response_mock
        mock_get_media_metadata.side_effect = http_error

        # Process the item - should raise exception
        with self.assertRaises(requests.exceptions.HTTPError):
            process_item(self.movie_item, events_bulk)
