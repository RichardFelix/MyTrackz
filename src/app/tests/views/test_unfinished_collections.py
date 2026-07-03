from unittest.mock import patch

import requests
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from app.models import Book, Item, MediaTypes, Movie, Sources, Status
from app.tasks import refresh_unfinished_collections


class UnfinishedCollectionsViewTests(TestCase):
    """Test the unfinished collections home widget."""

    def setUp(self):
        """Create a user, log in, and complete a movie."""
        # The widget cache is keyed per user id and fakeredis state persists
        # across tests in the same process, so start every test cold.
        cache.clear()

        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

        self.metadata_patcher = patch("app.providers.services.get_media_metadata")
        self.mock_get_media_metadata = self.metadata_patcher.start()
        self.addCleanup(self.metadata_patcher.stop)
        # Minimal metadata so Movie.save()'s process_status() succeeds while
        # fixtures are being created below.
        self.mock_get_media_metadata.return_value = {"max_progress": 1}

        movie_item = Item.objects.create(
            media_id="10",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Finished Movie",
            image="http://example.com/image.jpg",
        )
        self.movie = Movie.objects.create(
            item=movie_item,
            user=self.user,
            status=Status.COMPLETED.value,
            end_date=timezone.now(),
        )

    def _get_widget(self):
        """Return the rendered widget.

        The first GET is a cache miss that queues the build (which runs
        eagerly in tests) and returns the invisible polling fragment; the
        second GET serves the computed feed from the cache.
        """
        self.client.get(reverse("unfinished_collections"))
        return self.client.get(reverse("unfinished_collections"))

    def test_shows_untracked_collection_item(self):
        """A collection entry not yet tracked by the user should be surfaced."""
        self.mock_get_media_metadata.return_value = {
            "related": {
                "Test Collection": [
                    {
                        "media_id": "20",
                        "source": Sources.TMDB.value,
                        "media_type": MediaTypes.MOVIE.value,
                        "title": "Sequel Movie",
                        "image": "http://example.com/sequel.jpg",
                    },
                ],
                "recommendations": [
                    {
                        "media_id": "30",
                        "source": Sources.TMDB.value,
                        "media_type": MediaTypes.MOVIE.value,
                        "title": "Generic Recommendation",
                        "image": "http://example.com/rec.jpg",
                    },
                ],
            },
        }

        response = self._get_widget()

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response, "app/components/home_unfinished_collections.html"
        )

        suggestions = response.context["suggestions"]
        titles = [suggestion["item"]["title"] for suggestion in suggestions]
        self.assertIn("Sequel Movie", titles)
        self.assertNotIn("Generic Recommendation", titles)

    def test_hides_already_tracked_collection_item(self):
        """A collection entry the user already tracks should not be suggested."""
        tracked_item = Item.objects.create(
            media_id="20",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Sequel Movie",
            image="http://example.com/sequel.jpg",
        )
        Movie.objects.create(
            item=tracked_item,
            user=self.user,
            status=Status.PLANNING.value,
        )

        self.mock_get_media_metadata.return_value = {
            "related": {
                "Test Collection": [
                    {
                        "media_id": "20",
                        "source": Sources.TMDB.value,
                        "media_type": MediaTypes.MOVIE.value,
                        "title": "Sequel Movie",
                        "image": "http://example.com/sequel.jpg",
                    },
                ],
            },
        }

        response = self._get_widget()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["suggestions"], [])

    def test_ignores_other_editions_section(self):
        """Other editions of the same book aren't genuine follow-ups."""
        book_item = Item.objects.create(
            media_id="1",
            source=Sources.OPENLIBRARY.value,
            media_type=MediaTypes.BOOK.value,
            title="Finished Book",
            image="http://example.com/book.jpg",
        )
        Book.objects.create(
            item=book_item,
            user=self.user,
            status=Status.COMPLETED.value,
            end_date=timezone.now(),
        )

        self.mock_get_media_metadata.return_value = {
            "related": {
                "other_editions": [
                    {
                        "media_id": "2",
                        "source": Sources.OPENLIBRARY.value,
                        "media_type": MediaTypes.BOOK.value,
                        "title": "Finished Book (Paperback)",
                        "image": "http://example.com/paperback.jpg",
                    },
                ],
            },
        }

        response = self._get_widget()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["suggestions"], [])

    def test_no_suggestions_renders_empty(self):
        """The widget should render nothing when there are no suggestions."""
        self.mock_get_media_metadata.return_value = {"related": {}}

        response = self._get_widget()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["suggestions"], [])
        self.assertNotContains(response, "Continue the Story")

    def test_degrades_gracefully_on_provider_network_error(self):
        """A network failure fetching related data shouldn't break the widget."""
        self.mock_get_media_metadata.side_effect = requests.exceptions.ConnectionError

        response = self._get_widget()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["suggestions"], [])

    def _sequel_metadata(self):
        return {
            "related": {
                "Test Collection": [
                    {
                        "media_id": "20",
                        "source": Sources.TMDB.value,
                        "media_type": MediaTypes.MOVIE.value,
                        "title": "Sequel Movie",
                        "image": "http://example.com/sequel.jpg",
                    },
                ],
            },
        }

    def test_cold_load_returns_polling_fragment(self):
        """A cache miss should return the invisible self-polling fragment."""
        self.mock_get_media_metadata.return_value = self._sequel_metadata()

        response = self.client.get(reverse("unfinished_collections"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response, "app/components/unfinished_collections_loading.html"
        )
        self.assertContains(response, reverse("unfinished_collections"))

    def test_warm_load_makes_no_provider_calls(self):
        """A cached widget must render without touching any provider."""
        self.mock_get_media_metadata.return_value = self._sequel_metadata()
        self._get_widget()

        self.mock_get_media_metadata.reset_mock()
        response = self.client.get(reverse("unfinished_collections"))

        self.assertTemplateUsed(
            response, "app/components/home_unfinished_collections.html"
        )
        titles = [s["item"]["title"] for s in response.context["suggestions"]]
        self.assertEqual(titles, ["Sequel Movie"])
        self.mock_get_media_metadata.assert_not_called()

    def test_tracking_a_suggestion_hides_it_from_the_cached_widget(self):
        """Tracking a suggested item removes it without waiting for a rebuild."""
        self.mock_get_media_metadata.return_value = self._sequel_metadata()
        self._get_widget()

        suggested_item = Item.objects.get(
            media_id="20", media_type=MediaTypes.MOVIE.value
        )
        Movie.objects.create(
            item=suggested_item, user=self.user, status=Status.PLANNING.value
        )

        response = self.client.get(reverse("unfinished_collections"))

        self.assertEqual(response.context["suggestions"], [])

    def test_refresh_unfinished_collections_task_precomputes(self):
        """The beat task fills the cache so the first widget render is warm."""
        self.mock_get_media_metadata.return_value = self._sequel_metadata()

        refresh_unfinished_collections()

        response = self.client.get(reverse("unfinished_collections"))

        self.assertTemplateUsed(
            response, "app/components/home_unfinished_collections.html"
        )
        titles = [s["item"]["title"] for s in response.context["suggestions"]]
        self.assertEqual(titles, ["Sequel Movie"])

    def test_polling_does_not_queue_duplicate_builds(self):
        """While a build is pending, further polls must not queue more builds."""
        with patch("app.views.compute_unfinished_collections.delay") as mock_delay:
            self.client.get(reverse("unfinished_collections"))
            self.client.get(reverse("unfinished_collections"))

        mock_delay.assert_called_once_with(self.user.id)
