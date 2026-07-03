from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from app.models import TV, Episode, Item, MediaTypes, Movie, Season, Sources, Status


class QueryCountRegressionTests(TestCase):
    """Lock in constant query counts on list-style pages.

    TV/Season date/progress properties walk seasons and episodes in Python;
    MediaManager._apply_prefetch_related keeps that O(1) in queries. These
    tests fail if someone adds a per-row query (an N+1) to the media list or
    home page rendering paths.
    """

    def setUp(self):
        """Create a user, log in, and mock provider metadata."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

        episodes = [{"episode_number": number} for number in range(1, 10)]
        self.metadata_patcher = patch(
            "app.providers.services.get_media_metadata",
            return_value={
                "max_progress": 10,
                "related": {"seasons": []},
                "season/1": {"episodes": episodes},
                "season/2": {"episodes": episodes},
            },
        )
        self.metadata_patcher.start()
        self.addCleanup(self.metadata_patcher.stop)

    def _make_tv(self, index):
        tv_item = Item.objects.create(
            media_id=str(1000 + index),
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title=f"Show {index}",
            image="http://example.com/image.jpg",
        )
        tv = TV.objects.create(
            item=tv_item, user=self.user, status=Status.IN_PROGRESS.value
        )
        for season_number in range(1, 3):
            season_item = Item.objects.create(
                media_id=str(1000 + index),
                source=Sources.TMDB.value,
                media_type=MediaTypes.SEASON.value,
                season_number=season_number,
                title=f"Show {index}",
                image="http://example.com/image.jpg",
            )
            season = Season.objects.create(
                item=season_item,
                user=self.user,
                related_tv=tv,
                status=Status.IN_PROGRESS.value,
            )
            for episode_number in range(1, 4):
                episode_item = Item.objects.create(
                    media_id=str(1000 + index),
                    source=Sources.TMDB.value,
                    media_type=MediaTypes.EPISODE.value,
                    season_number=season_number,
                    episode_number=episode_number,
                    title=f"Show {index}",
                    image="http://example.com/image.jpg",
                )
                Episode.objects.create(
                    item=episode_item,
                    related_season=season,
                    end_date=timezone.now(),
                )

    def _make_movie(self, index):
        movie_item = Item.objects.create(
            media_id=str(2000 + index),
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title=f"Movie {index}",
            image="http://example.com/image.jpg",
        )
        Movie.objects.create(
            item=movie_item, user=self.user, status=Status.IN_PROGRESS.value
        )

    def _count_queries(self, url):
        with CaptureQueriesContext(connection) as context:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        return len(context.captured_queries)

    def test_tv_media_list_query_count_does_not_scale_with_shows(self):
        """Rendering more TV shows must not run more queries per show."""
        url = reverse(
            "medialist",
            kwargs={"username": self.user.username, "media_type": "tv"},
        )
        self._make_tv(1)
        baseline = self._count_queries(url)

        self._make_tv(2)
        self._make_tv(3)
        with_more_shows = self._count_queries(url)

        self.assertEqual(baseline, with_more_shows)

    def test_home_query_count_does_not_scale_with_media(self):
        """Rendering more tracked media on home must not add per-row queries."""
        self._make_tv(1)
        self._make_movie(1)
        baseline = self._count_queries(reverse("home"))

        for index in range(2, 5):
            self._make_tv(index)
            self._make_movie(index)
        with_more_media = self._count_queries(reverse("home"))

        self.assertEqual(baseline, with_more_media)
