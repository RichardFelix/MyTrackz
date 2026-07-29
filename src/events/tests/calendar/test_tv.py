import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import requests
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from app.models import (
    TV,
    BasicMedia,
    Item,
    MediaTypes,
    Season,
    Status,
)
from events.calendar.helpers import date_parser
from events.calendar.tv import (
    get_episode_datetime,
    get_seasons_to_process,
    get_tvmaze_episode_map,
    get_tvmaze_response,
    process_season_episodes,
    process_tv,
    process_tv_seasons,
    reconcile_completed_tv_seasons,
)
from events.models import Event
from events.tests.calendar.utils import CalendarFixturesMixin
from users.models import HomeSortChoices


class CalendarTVTests(CalendarFixturesMixin, TestCase):
    """Test TV calendar processing."""

    @patch("events.calendar.tv.process_season_episodes")
    @patch("events.calendar.tv.tmdb.tv_with_seasons")
    def test_season_poster_falls_back_to_show_then_refreshes(
        self,
        mock_tv_with_seasons,
        mock_process_season_episodes,
    ):
        """A later calendar reload replaces the temporary show poster fallback."""
        season_number = 4
        mock_tv_with_seasons.return_value = {
            f"season/{season_number}": {
                "image": settings.IMG_NONE,
                "season_number": season_number,
                "episodes": [],
            },
        }

        process_tv_seasons(self.tv_item, [season_number], [])

        season_item = Item.objects.get(
            media_id=self.tv_item.media_id,
            source=self.tv_item.source,
            media_type=MediaTypes.SEASON.value,
            season_number=season_number,
        )
        self.assertEqual(season_item.image, self.tv_item.image)

        season_image = "http://example.com/season4.jpg"
        mock_tv_with_seasons.return_value[f"season/{season_number}"]["image"] = (
            season_image
        )
        with patch("app.tasks.cache_item_image.delay") as mock_cache_image:
            process_tv_seasons(self.tv_item, [season_number], [])

        season_item.refresh_from_db()
        self.assertEqual(season_item.image, season_image)
        mock_cache_image.assert_called_once_with(season_item.id, season_image)
        self.assertEqual(mock_process_season_episodes.call_count, 2)

    @patch("events.calendar.tv.process_season_episodes")
    @patch("events.calendar.tv.tmdb.tv_with_seasons")
    def test_missing_season_poster_does_not_replace_existing_poster(
        self,
        mock_tv_with_seasons,
        _mock_process_season_episodes,
    ):
        """Missing provider data must not downgrade a real season poster."""
        mock_tv_with_seasons.return_value = {
            "season/1": {
                "image": settings.IMG_NONE,
                "season_number": 1,
                "episodes": [],
            },
        }
        original_image = self.season_item.image

        process_tv_seasons(self.tv_item, [1], [])

        self.season_item.refresh_from_db()
        self.assertEqual(self.season_item.image, original_image)

    @patch("events.calendar.tv.tmdb.tv")
    @patch("events.calendar.tv.tmdb.tv_with_seasons")
    @patch("events.calendar.tv.get_tvmaze_episode_map")
    def test_process_tv_season(
        self,
        mock_get_tvmaze_episode_map,
        mock_tv_with_seasons,
        mock_tv,
    ):
        """Test processing for a TV season."""
        mock_tv.return_value = {
            "related": {
                "seasons": [
                    {"season_number": 1, "episodes": [1, 2, 3]},
                    {"season_number": 2, "episodes": [1, 2]},
                    {"season_number": 3, "episodes": [1]},
                ],
            },
            "next_episode_season": 2,
        }

        mock_tv_with_seasons.return_value = {
            "season/1": {
                "image": "http://example.com/season1.jpg",
                "season_number": 1,
                "episodes": [
                    {"episode_number": 1, "air_date": "2008-01-20"},
                    {"episode_number": 2, "air_date": "2008-01-27"},
                    {"episode_number": 3, "air_date": "2008-02-03"},
                ],
                "tvdb_id": "81189",
            },
            "season/2": {
                "image": "http://example.com/season2.jpg",
                "season_number": 2,
                "episodes": [
                    {"episode_number": 1, "air_date": "2009-01-20"},
                    {"episode_number": 2, "air_date": "2009-01-27"},
                ],
                "tvdb_id": "81189",
            },
            "season/3": {
                "image": "http://example.com/season3.jpg",
                "season_number": 3,
                "episodes": [
                    {"episode_number": 1, "air_date": "2010-01-20"},
                ],
                "tvdb_id": "81189",
            },
        }

        mock_get_tvmaze_episode_map.return_value = {
            "1_1": {
                "airdate": "2008-01-20",
                "airstamp": "2008-01-20T22:00:00+00:00",
            },
            "1_2": {
                "airdate": "2008-01-27",
                "airstamp": "2008-01-27T22:00:00+00:00",
            },
            "1_3": {
                "airdate": "2008-02-03",
                "airstamp": "2008-02-03T22:00:00+00:00",
            },
        }

        events_bulk = []
        process_tv(self.tv_item, events_bulk)

        self.assertEqual(len(events_bulk), 6)
        self.assertEqual(events_bulk[0].item, self.season_item)
        self.assertEqual(events_bulk[0].content_number, 1)

        expected_date = datetime.datetime.fromisoformat("2008-01-20T22:00:00+00:00")
        self.assertEqual(events_bulk[0].datetime, expected_date)

    @patch("events.calendar.tv.get_tvmaze_episode_map")
    @patch("events.calendar.tv.tmdb.tv_with_seasons")
    @patch("events.calendar.tv.tmdb.tv")
    def test_saved_new_season_event_reopens_completed_show_as_planning(
        self,
        mock_tv,
        mock_tv_with_seasons,
        mock_get_tvmaze_episode_map,
    ):
        """A newly saved season event should reopen a completed TV."""
        TV.objects.filter(item=self.tv_item, user=self.user).update(
            status=Status.COMPLETED.value,
        )
        Season.objects.filter(item=self.season_item, user=self.user).update(
            status=Status.COMPLETED.value,
        )
        Event.objects.create(
            item=self.season_item,
            content_number=1,
            datetime=date_parser("2008-01-20"),
        )

        mock_tv.return_value = {
            "related": {
                "seasons": [
                    {"season_number": 1, "episodes": [1]},
                    {"season_number": 2, "episodes": [1]},
                ],
            },
            "next_episode_season": 2,
        }
        mock_tv_with_seasons.return_value = {
            "season/2": {
                "image": "http://example.com/season2.jpg",
                "season_number": 2,
                "episodes": [
                    {"episode_number": 1, "air_date": "2027-01-20"},
                ],
                "tvdb_id": "81189",
            },
        }
        mock_get_tvmaze_episode_map.return_value = {}

        events_bulk = []
        process_tv(self.tv_item, events_bulk)
        Event.objects.bulk_create(events_bulk)
        repaired_tvs, created_seasons = reconcile_completed_tv_seasons(
            user=self.user,
        )

        season_two_item = Item.objects.get(
            media_id=self.tv_item.media_id,
            source=self.tv_item.source,
            media_type=self.season_item.media_type,
            season_number=2,
        )
        season_two = Season.objects.get(item=season_two_item, user=self.user)
        tv = TV.objects.get(item=self.tv_item, user=self.user)

        self.assertEqual(tv.status, Status.IN_PROGRESS.value)
        self.assertEqual(season_two.status, Status.PLANNING.value)
        self.assertEqual(len(events_bulk), 1)
        self.assertEqual((repaired_tvs, created_seasons), (1, 1))

    @patch("events.calendar.tv.get_tvmaze_episode_map")
    @patch("events.calendar.tv.tmdb.tv_with_seasons")
    @patch("events.calendar.tv.tmdb.tv")
    def test_process_tv_does_not_reopen_completed_show_for_past_only_season(
        self,
        mock_tv,
        mock_tv_with_seasons,
        mock_get_tvmaze_episode_map,
    ):
        """Past-only seasons should not reopen a completed TV entry."""
        TV.objects.filter(item=self.tv_item, user=self.user).update(
            status=Status.COMPLETED.value,
        )
        Season.objects.filter(item=self.season_item, user=self.user).update(
            status=Status.COMPLETED.value,
        )
        Event.objects.create(
            item=self.season_item,
            content_number=1,
            datetime=date_parser("2008-01-20"),
        )

        mock_tv.return_value = {
            "related": {
                "seasons": [
                    {"season_number": 1, "episodes": [1]},
                    {"season_number": 2, "episodes": [1]},
                ],
            },
            "next_episode_season": 2,
        }
        mock_tv_with_seasons.return_value = {
            "season/2": {
                "image": "http://example.com/season2.jpg",
                "season_number": 2,
                "episodes": [
                    {"episode_number": 1, "air_date": "2010-01-20"},
                ],
                "tvdb_id": "81189",
            },
        }
        mock_get_tvmaze_episode_map.return_value = {}

        events_bulk = []
        process_tv(self.tv_item, events_bulk)
        Event.objects.bulk_create(events_bulk)
        repaired_tvs, created_seasons = reconcile_completed_tv_seasons(
            user=self.user,
        )

        tv = TV.objects.get(item=self.tv_item, user=self.user)
        self.assertEqual(tv.status, Status.COMPLETED.value)
        self.assertFalse(
            Season.objects.filter(
                item__media_id=self.tv_item.media_id,
                item__source=self.tv_item.source,
                item__season_number=2,
                user=self.user,
            ).exists(),
        )
        self.assertEqual(len(events_bulk), 1)
        self.assertEqual((repaired_tvs, created_seasons), (0, 0))

    def test_reconcile_repairs_existing_future_season_event_idempotently(self):
        """Saved future events should repair tracker drift exactly once."""
        tv = TV.objects.get(item=self.tv_item, user=self.user)
        TV.objects.filter(pk=tv.pk).update(status=Status.COMPLETED.value)
        Season.objects.filter(
            item=self.season_item,
            user=self.user,
        ).update(status=Status.COMPLETED.value)
        season_four_item = Item.objects.create(
            media_id=self.tv_item.media_id,
            source=self.tv_item.source,
            media_type=MediaTypes.SEASON.value,
            title=self.tv_item.title,
            image="http://example.com/season4.jpg",
            season_number=4,
        )
        Event.objects.create(
            item=season_four_item,
            content_number=1,
            datetime=timezone.now() + timezone.timedelta(hours=4),
        )

        result = reconcile_completed_tv_seasons(user=self.user.id)

        self.assertEqual(result, (1, 1))
        tv.refresh_from_db()
        season_four = Season.objects.get(item=season_four_item, user=self.user)
        self.assertEqual(tv.status, Status.IN_PROGRESS.value)
        self.assertEqual(season_four.status, Status.PLANNING.value)
        self.assertEqual(season_four.history.count(), 1)
        self.assertEqual(season_four.history.first().history_user, self.user)
        self.assertEqual(tv.history.first().history_user, self.user)

        planning_home = BasicMedia.objects.get_home_status(
            user=self.user,
            status=Status.PLANNING.value,
            sort_by=HomeSortChoices.UPCOMING,
            items_limit=14,
        )
        self.assertEqual(
            planning_home[MediaTypes.SEASON.value]["items"],
            [season_four],
        )

        Event.objects.filter(item=season_four_item).update(
            datetime=timezone.now() - timezone.timedelta(minutes=1),
        )
        result = reconcile_completed_tv_seasons(user=self.user)

        self.assertEqual(result, (0, 0))
        self.assertEqual(
            Season.objects.filter(item=season_four_item, user=self.user).count(),
            1,
        )
        season_four.refresh_from_db()
        self.assertEqual(season_four.status, Status.PLANNING.value)

    def test_reconcile_repairs_in_progress_tv_without_status_change(self):
        """An already reopened TV should still receive its missing season."""
        tv = TV.objects.get(item=self.tv_item, user=self.user)
        TV.objects.filter(pk=tv.pk).update(status=Status.IN_PROGRESS.value)
        Season.objects.filter(
            item=self.season_item,
            user=self.user,
        ).update(status=Status.IN_PROGRESS.value)
        season_four_item = Item.objects.create(
            media_id=self.tv_item.media_id,
            source=self.tv_item.source,
            media_type=MediaTypes.SEASON.value,
            title=self.tv_item.title,
            image="http://example.com/season4.jpg",
            season_number=4,
        )
        Event.objects.create(
            item=season_four_item,
            content_number=1,
            datetime=timezone.now() + timezone.timedelta(hours=4),
        )
        history_count = tv.history.count()

        result = reconcile_completed_tv_seasons(user=self.user)

        self.assertEqual(result, (1, 1))
        tv.refresh_from_db()
        season_four = Season.objects.get(item=season_four_item, user=self.user)
        self.assertEqual(tv.status, Status.IN_PROGRESS.value)
        self.assertEqual(tv.history.count(), history_count)
        self.assertEqual(season_four.status, Status.PLANNING.value)
        self.assertEqual(season_four.history.first().history_user, self.user)

        planning_home = BasicMedia.objects.get_home_status(
            user=self.user,
            status=Status.PLANNING.value,
            sort_by=HomeSortChoices.UPCOMING,
            items_limit=14,
        )
        self.assertNotIn(
            season_four,
            planning_home.get(MediaTypes.SEASON.value, {}).get("items", []),
        )

    def test_reconcile_is_user_scoped(self):
        """A manual refresh should only repair the requesting user's tracker."""
        second_user = get_user_model().objects.create_user(username="second")
        first_tv = TV.objects.get(item=self.tv_item, user=self.user)
        TV.objects.filter(pk=first_tv.pk).update(status=Status.COMPLETED.value)
        second_tv = TV.objects.create(
            item=self.tv_item,
            user=second_user,
            status=Status.PLANNING.value,
        )
        TV.objects.filter(pk=second_tv.pk).update(status=Status.COMPLETED.value)
        season_two_item = Item.objects.create(
            media_id=self.tv_item.media_id,
            source=self.tv_item.source,
            media_type=MediaTypes.SEASON.value,
            title=self.tv_item.title,
            image="http://example.com/season2.jpg",
            season_number=2,
        )
        Event.objects.create(
            item=season_two_item,
            content_number=1,
            datetime=timezone.now() + timezone.timedelta(days=1),
        )

        result = reconcile_completed_tv_seasons(user=self.user)

        self.assertEqual(result, (1, 1))
        self.assertTrue(
            Season.objects.filter(item=season_two_item, user=self.user).exists(),
        )
        self.assertFalse(
            Season.objects.filter(item=season_two_item, user=second_user).exists(),
        )
        second_tv.refresh_from_db()
        self.assertEqual(second_tv.status, Status.COMPLETED.value)

        result = reconcile_completed_tv_seasons()

        self.assertEqual(result, (1, 1))
        second_tv.refresh_from_db()
        self.assertEqual(second_tv.status, Status.IN_PROGRESS.value)
        self.assertTrue(
            Season.objects.filter(item=season_two_item, user=second_user).exists(),
        )

    def test_reconcile_excludes_specials_past_events_and_ineligible_tv(self):
        """Only future regular seasons belonging to eligible TVs are repaired."""
        tv = TV.objects.get(item=self.tv_item, user=self.user)
        special_item = Item.objects.create(
            media_id=self.tv_item.media_id,
            source=self.tv_item.source,
            media_type=MediaTypes.SEASON.value,
            title=self.tv_item.title,
            image="http://example.com/specials.jpg",
            season_number=0,
        )
        past_season_item = Item.objects.create(
            media_id=self.tv_item.media_id,
            source=self.tv_item.source,
            media_type=MediaTypes.SEASON.value,
            title=self.tv_item.title,
            image="http://example.com/season2.jpg",
            season_number=2,
        )
        Event.objects.create(
            item=special_item,
            content_number=1,
            datetime=timezone.now() + timezone.timedelta(days=1),
        )
        Event.objects.create(
            item=past_season_item,
            content_number=1,
            datetime=timezone.now() - timezone.timedelta(days=1),
        )

        result = reconcile_completed_tv_seasons(user=self.user)

        self.assertEqual(result, (0, 0))
        self.assertFalse(
            Season.objects.filter(
                user=self.user,
                item__in=(special_item, past_season_item),
            ).exists(),
        )
        tv.refresh_from_db()
        self.assertEqual(tv.status, Status.PLANNING.value)

    @patch("events.calendar.tv.services.api_request")
    def test_get_tvmaze_episode_map(self, mock_api_request):
        """Test get_tvmaze_episode_map function."""
        cache.clear()

        mock_api_request.side_effect = [
            {"id": 12345},
            {
                "_embedded": {
                    "episodes": [
                        {
                            "season": 1,
                            "number": 1,
                            "airdate": "2008-01-20",
                            "airstamp": "2008-01-20T22:00:00+00:00",
                            "airtime": "22:00",
                        },
                        {
                            "season": 1,
                            "number": 2,
                            "airdate": "2008-01-27",
                            "airstamp": "2008-01-27T22:00:00+00:00",
                            "airtime": "22:00",
                        },
                    ],
                },
            },
        ]

        result = get_tvmaze_episode_map("81189")

        self.assertEqual(len(result), 2)
        self.assertIn("1_1", result)
        self.assertIn("1_2", result)
        self.assertEqual(
            result["1_1"],
            {
                "airdate": "2008-01-20",
                "airstamp": "2008-01-20T22:00:00+00:00",
            },
        )
        self.assertEqual(
            result["1_2"],
            {
                "airdate": "2008-01-27",
                "airstamp": "2008-01-27T22:00:00+00:00",
            },
        )

        cached_result = cache.get("tvmaze_episode_map_v2_81189")
        self.assertEqual(cached_result, result)

        mock_api_request.reset_mock()
        get_tvmaze_episode_map("81189")
        mock_api_request.assert_not_called()

    @patch("events.calendar.tv.services.api_request")
    def test_get_tvmaze_episode_map_lookup_failure(self, mock_api_request):
        """Test get_tvmaze_episode_map when lookup fails."""
        cache.clear()
        mock_api_request.return_value = None

        result = get_tvmaze_episode_map("invalid_id")

        self.assertEqual(result, {})
        mock_api_request.assert_called_once()

    def test_get_episode_datetime_falls_back_to_tmdb_air_date(self):
        """TMDB dates should be used when TVMaze has no timestamp."""
        result = get_episode_datetime(
            {"air_date": "2025-01-31"},
            season_number=1,
            episode_number=2,
            tvmaze_map={},
        )

        self.assertEqual(result, date_parser("2025-01-31"))

    def test_get_episode_datetime_uses_tvmaze_when_local_dates_agree(self):
        """Matching local dates should retain TVMaze's exact timestamp."""
        result = get_episode_datetime(
            {"air_date": "2026-07-24"},
            season_number=28,
            episode_number=9,
            tvmaze_map={
                "28_9": {
                    "airdate": "2026-07-24",
                    "airstamp": "2026-07-25T01:00:00+00:00",
                },
            },
        )

        self.assertEqual(
            result,
            datetime.datetime.fromisoformat("2026-07-25T01:00:00+00:00"),
        )

    def test_get_episode_datetime_uses_tmdb_when_dates_conflict(self):
        """TMDB should win when TVMaze maps the episode number to another date."""
        result = get_episode_datetime(
            {"air_date": "2026-07-24"},
            season_number=28,
            episode_number=9,
            tvmaze_map={
                "28_9": {
                    "airdate": "2026-07-29",
                    "airstamp": "2026-07-30T00:00:00+00:00",
                },
            },
        )

        self.assertEqual(result, date_parser("2026-07-24"))

    def test_get_episode_datetime_uses_tvmaze_when_tmdb_date_is_missing(self):
        """TVMaze should supply the timestamp when TMDB has no usable date."""
        result = get_episode_datetime(
            {"air_date": None},
            season_number=1,
            episode_number=2,
            tvmaze_map={
                "1_2": {
                    "airdate": "2025-01-31",
                    "airstamp": "2025-02-01T01:00:00+00:00",
                },
            },
        )

        self.assertEqual(
            result,
            datetime.datetime.fromisoformat("2025-02-01T01:00:00+00:00"),
        )

    def test_get_episode_datetime_ignores_incomplete_tvmaze_data(self):
        """TVMaze entries without a local date should not override TMDB."""
        result = get_episode_datetime(
            {"air_date": "2025-01-31"},
            season_number=1,
            episode_number=2,
            tvmaze_map={
                "1_2": {
                    "airdate": None,
                    "airstamp": "2025-02-01T01:00:00+00:00",
                },
            },
        )

        self.assertEqual(result, date_parser("2025-01-31"))

    def test_get_episode_datetime_falls_back_when_tvmaze_airstamp_is_invalid(self):
        """Malformed TVMaze timestamps should fall back to the TMDB date."""
        result = get_episode_datetime(
            {"air_date": "2025-01-31"},
            season_number=1,
            episode_number=2,
            tvmaze_map={
                "1_2": {
                    "airdate": "2025-01-31",
                    "airstamp": "not-a-datetime",
                },
            },
        )

        self.assertEqual(result, date_parser("2025-01-31"))

    def test_get_episode_datetime_returns_placeholder_for_invalid_date(self):
        """Invalid or missing episode dates should produce a placeholder datetime."""
        result = get_episode_datetime(
            {"air_date": "not-a-date"},
            season_number=1,
            episode_number=2,
            tvmaze_map={},
        )

        self.assertEqual(result, datetime.datetime.min.replace(tzinfo=ZoneInfo("UTC")))

    @patch("events.calendar.tv.services.api_request")
    def test_get_tvmaze_response_returns_empty_on_not_found(self, mock_api_request):
        """A 404 from the TVMaze lookup should be tolerated."""
        response = MagicMock()
        response.status_code = requests.codes.not_found
        response.text = "missing"
        mock_api_request.side_effect = requests.exceptions.HTTPError(response=response)

        self.assertEqual(get_tvmaze_response("999"), {})

    @patch("events.calendar.tv.tmdb.tv")
    def test_get_seasons_to_process_returns_empty_when_no_seasons(self, mock_tv):
        """TV metadata without seasons should short-circuit processing."""
        mock_tv.return_value = {"related": {"seasons": []}}

        self.assertEqual(get_seasons_to_process(self.tv_item), [])

    @patch("events.calendar.tv.tmdb.tv")
    def test_get_seasons_to_process_always_includes_latest_regular_season(
        self,
        mock_tv,
    ):
        """A selected show should revisit its latest season without a next episode."""
        season_two_item = Item.objects.create(
            media_id=self.tv_item.media_id,
            source=self.tv_item.source,
            media_type=MediaTypes.SEASON.value,
            title=self.tv_item.title,
            image=self.tv_item.image,
            season_number=2,
        )
        special_item = Item.objects.create(
            media_id=self.tv_item.media_id,
            source=self.tv_item.source,
            media_type=MediaTypes.SEASON.value,
            title=self.tv_item.title,
            image=self.tv_item.image,
            season_number=0,
        )
        for item in (special_item, self.season_item, season_two_item):
            Event.objects.create(
                item=item,
                content_number=1,
                datetime=timezone.now() - timezone.timedelta(days=1),
            )
        mock_tv.return_value = {
            "related": {
                "seasons": [
                    {"season_number": 0},
                    {"season_number": 1},
                    {"season_number": 2},
                ],
            },
            "next_episode_season": None,
        }

        self.assertEqual(get_seasons_to_process(self.tv_item), [2])

    @patch("events.calendar.tv.get_seasons_to_process")
    def test_process_tv_returns_when_no_seasons_need_processing(
        self,
        mock_get_seasons_to_process,
    ):
        """process_tv should stop cleanly when there is nothing new to fetch."""
        mock_get_seasons_to_process.return_value = []

        events_bulk = []
        process_tv(self.tv_item, events_bulk)

        self.assertEqual(events_bulk, [])

    def test_process_season_episodes_handles_missing_tvdb_and_episodes(self):
        """A season without TVDB data or episodes should not add events."""
        events_bulk = []
        process_season_episodes(
            self.season_item,
            {
                "season_number": 1,
                "episodes": [],
            },
            events_bulk,
        )

        self.assertEqual(events_bulk, [])

    @patch("events.calendar.tv.services.api_request")
    def test_get_tvmaze_response_returns_empty_when_lookup_has_no_id(
        self,
        mock_api_request,
    ):
        """Lookup responses without a TVMaze id should return an empty mapping."""
        mock_api_request.return_value = {"name": "Breaking Bad"}

        self.assertEqual(get_tvmaze_response("81189"), {})

    @patch("events.calendar.tv.services.api_request")
    def test_get_tvmaze_response_returns_empty_when_episode_fetch_fails(
        self,
        mock_api_request,
    ):
        """Episode fetch errors after a successful lookup should be tolerated."""
        response = MagicMock()
        response.status_code = 500
        response.text = "boom"
        mock_api_request.side_effect = [
            {"id": 12345},
            requests.exceptions.HTTPError(response=response),
        ]

        self.assertEqual(get_tvmaze_response("81189"), {})
