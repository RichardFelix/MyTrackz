from unittest.mock import patch

import requests
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from app.models import Item, MediaTypes, Movie, Sources, Status
from app.providers import services
from app.tasks import compute_trending_feed

MOVIE_PICK = {
    "media_id": "100",
    "source": Sources.TMDB.value,
    "media_type": MediaTypes.MOVIE.value,
    "title": "Trending Movie",
    "image": "http://example.com/movie.jpg",
}
TV_PICK = {
    "media_id": "200",
    "source": Sources.TMDB.value,
    "media_type": MediaTypes.TV.value,
    "title": "Trending TV Show",
    "image": "http://example.com/tv.jpg",
}


class TrendingViewTests(TestCase):
    """Test the trending page and its global precomputed feed."""

    def setUp(self):
        """Create a user, log in, and mock the provider trending dispatch."""
        # The trending feed cache is global and fakeredis state persists
        # across tests in the same process, so start every test cold.
        cache.clear()

        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

        self.trending_patcher = patch("app.providers.services.trending")
        self.mock_trending = self.trending_patcher.start()
        self.addCleanup(self.trending_patcher.stop)
        self.mock_trending.side_effect = self._trending_side_effect

    @staticmethod
    def _trending_side_effect(media_type):
        picks = {
            MediaTypes.MOVIE.value: [dict(MOVIE_PICK)],
            MediaTypes.TV.value: [dict(TV_PICK)],
        }
        return picks.get(media_type, [])

    def _get_feed(self):
        """Return the rendered feed.

        The first GET is a cache miss that queues the build (which runs
        eagerly in tests) and returns the loading fragment; the second GET
        serves the computed feed from the cache.
        """
        self.client.get(reverse("trending_feed"))
        return self.client.get(reverse("trending_feed"))

    def test_trending_page_renders(self):
        """The trending page shell should render without loading the feed."""
        response = self.client.get(reverse("trending"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/trending.html")
        self.mock_trending.assert_not_called()

    def test_cold_load_shows_building_state(self):
        """A cache miss should return the self-polling loading fragment."""
        response = self.client.get(reverse("trending_feed"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/components/discover_loading.html")
        self.assertContains(response, reverse("trending_feed"))

    def test_shows_trending_items_with_type_pills(self):
        """The warm feed should render items and per-type filter pills."""
        response = self._get_feed()

        self.assertTemplateUsed(
            response, "app/components/discover_recommendations.html"
        )
        titles = [s["item"]["title"] for s in response.context["suggestions"]]
        self.assertIn("Trending Movie", titles)
        self.assertIn("Trending TV Show", titles)
        self.assertEqual(
            response.context["media_type_counts"],
            [(MediaTypes.MOVIE.value, 1), (MediaTypes.TV.value, 1)],
        )
        self.assertContains(response, "TV Shows (1)")

    def test_warm_load_makes_no_provider_calls(self):
        """A cached feed must render without touching any provider."""
        self._get_feed()

        self.mock_trending.reset_mock()
        response = self.client.get(reverse("trending_feed"))

        self.assertEqual(response.status_code, 200)
        self.mock_trending.assert_not_called()

    def test_hides_tracked_items(self):
        """Anything the user already tracks must not appear in trending."""
        self._get_feed()

        trending_item = Item.objects.get(
            media_id="100", media_type=MediaTypes.MOVIE.value
        )
        Movie.objects.create(
            item=trending_item, user=self.user, status=Status.PLANNING.value
        )

        response = self.client.get(reverse("trending_feed"))

        titles = [s["item"]["title"] for s in response.context["suggestions"]]
        self.assertNotIn("Trending Movie", titles)
        self.assertIn("Trending TV Show", titles)

    def test_hides_disabled_media_types(self):
        """Media types the user disabled must not appear in trending."""
        self.user.movie_enabled = False
        self.user.save(update_fields=["movie_enabled"])

        response = self._get_feed()

        titles = [s["item"]["title"] for s in response.context["suggestions"]]
        self.assertNotIn("Trending Movie", titles)
        self.assertIn("Trending TV Show", titles)

    def test_refresh_endpoint_invalidates_and_rebuilds(self):
        """The refresh endpoint drops the cached feed and serves fresh data."""
        self._get_feed()

        new_pick = dict(MOVIE_PICK, media_id="101", title="Fresh Trending Movie")
        self.mock_trending.side_effect = (
            lambda media_type: [new_pick]
            if media_type == MediaTypes.MOVIE.value
            else []
        )
        response = self.client.post(reverse("trending_refresh"))

        self.assertTemplateUsed(response, "app/components/discover_loading.html")

        response = self.client.get(reverse("trending_feed"))
        titles = [s["item"]["title"] for s in response.context["suggestions"]]
        self.assertEqual(titles, ["Fresh Trending Movie"])

    def test_compute_trending_feed_task_precomputes(self):
        """The beat task fills the global cache so the first view is warm."""
        compute_trending_feed()

        response = self.client.get(reverse("trending_feed"))

        self.assertTemplateUsed(
            response, "app/components/discover_recommendations.html"
        )
        titles = [s["item"]["title"] for s in response.context["suggestions"]]
        self.assertIn("Trending Movie", titles)

    def test_polling_does_not_queue_duplicate_builds(self):
        """While a build is pending, further polls must not queue more builds."""
        with patch("app.views.compute_trending_feed.delay") as mock_delay:
            self.client.get(reverse("trending_feed"))
            self.client.get(reverse("trending_feed"))

        mock_delay.assert_called_once_with()

    def test_one_provider_failing_keeps_the_others(self):
        """A provider error for one type should only drop that type."""

        def side_effect(media_type):
            if media_type == MediaTypes.MOVIE.value:
                raise services.ProviderAPIError(Sources.TMDB.value, ValueError("down"))
            return self._trending_side_effect(media_type)

        self.mock_trending.side_effect = side_effect

        response = self._get_feed()

        titles = [s["item"]["title"] for s in response.context["suggestions"]]
        self.assertNotIn("Trending Movie", titles)
        self.assertIn("Trending TV Show", titles)

    def test_degrades_gracefully_on_network_error(self):
        """A blanket network failure should render the empty state, not a 500."""
        self.mock_trending.side_effect = requests.exceptions.ConnectionError

        response = self._get_feed()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["suggestions"], [])
        self.assertContains(response, "Nothing trending right now")
