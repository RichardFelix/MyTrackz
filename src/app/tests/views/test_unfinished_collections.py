from unittest.mock import patch

import requests
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from app.models import Book, Item, MediaTypes, Movie, Sources, Status


class UnfinishedCollectionsViewTests(TestCase):
    """Test the unfinished collections home widget."""

    def setUp(self):
        """Create a user, log in, and complete a movie."""
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

        response = self.client.get(reverse("unfinished_collections"))

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

        response = self.client.get(reverse("unfinished_collections"))

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

        response = self.client.get(reverse("unfinished_collections"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["suggestions"], [])

    def test_no_suggestions_renders_empty(self):
        """The widget should render nothing when there are no suggestions."""
        self.mock_get_media_metadata.return_value = {"related": {}}

        response = self.client.get(reverse("unfinished_collections"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["suggestions"], [])
        self.assertNotContains(response, "Continue the Story")

    def test_degrades_gracefully_on_provider_network_error(self):
        """A network failure fetching related data shouldn't break the widget."""
        self.mock_get_media_metadata.side_effect = requests.exceptions.ConnectionError

        response = self.client.get(reverse("unfinished_collections"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["suggestions"], [])
