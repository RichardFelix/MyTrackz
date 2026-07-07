from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from app.models import Genre, Item, MediaTypes, Movie, Sources, Status
from app.tasks import backfill_item_genres


class GenreModel(TestCase):
    """Tests for Item.set_genres_from_metadata."""

    def setUp(self):
        """Prepare an item."""
        self.item = Item.objects.create(
            media_id="10",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Inception",
            image="http://example.com/image.jpg",
        )

    def test_sets_and_reuses_genres(self):
        """Genres are created once and shared between items."""
        self.item.set_genres_from_metadata({"genres": ["Action", "Sci-Fi"]})
        other = Item.objects.create(
            media_id="11",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="The Matrix",
            image="http://example.com/image.jpg",
        )
        other.set_genres_from_metadata({"genres": ["Sci-Fi"]})

        self.assertEqual(
            set(self.item.genres.values_list("name", flat=True)),
            {"Action", "Sci-Fi"},
        )
        self.assertEqual(Genre.objects.filter(name="Sci-Fi").count(), 1)

    def test_empty_metadata_keeps_existing_genres(self):
        """Metadata without genres must not wipe stored genres."""
        self.item.set_genres_from_metadata({"genres": ["Action"]})
        self.item.set_genres_from_metadata({"genres": None})
        self.item.set_genres_from_metadata({})

        self.assertEqual(
            list(self.item.genres.values_list("name", flat=True)),
            ["Action"],
        )

    def test_changed_genres_are_replaced(self):
        """A different genre set from metadata replaces the stored one."""
        self.item.set_genres_from_metadata({"genres": ["Action"]})
        self.item.set_genres_from_metadata({"genres": ["Drama", "Thriller"]})

        self.assertEqual(
            set(self.item.genres.values_list("name", flat=True)),
            {"Drama", "Thriller"},
        )


class GenreFilter(TestCase):
    """Tests for the genre filter on the media list page."""

    def setUp(self):
        """Prepare a user with two movies in different genres."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

        self.action = Item.objects.create(
            media_id="10",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Inception",
            image="http://example.com/image.jpg",
        )
        self.action.set_genres_from_metadata({"genres": ["Action"]})
        self.drama = Item.objects.create(
            media_id="11",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="The Godfather",
            image="http://example.com/image.jpg",
        )
        self.drama.set_genres_from_metadata({"genres": ["Drama"]})

        for item in (self.action, self.drama):
            movie = Movie(
                item=item,
                user=self.user,
                status=Status.COMPLETED.value,
                score=9,
            )
            Movie.save_base(movie)

        self.url = reverse(
            "medialist",
            kwargs={
                "username": self.user.username,
                "media_type": MediaTypes.MOVIE.value,
            },
        )

    def test_filters_by_genre(self):
        """Only items with the selected genre are listed."""
        response = self.client.get(self.url, {"genre": "Action"})
        self.assertContains(response, "Inception")
        self.assertNotContains(response, "The Godfather")

    def test_no_genre_shows_all(self):
        """Without a genre param, all items are listed."""
        response = self.client.get(self.url)
        self.assertContains(response, "Inception")
        self.assertContains(response, "The Godfather")

    def test_genre_choices_scoped_to_user_and_type(self):
        """The dropdown offers only genres present in this user's library."""
        stranger_credentials = {"username": "other", "password": "12345"}
        stranger = get_user_model().objects.create_user(**stranger_credentials)
        horror_item = Item.objects.create(
            media_id="12",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Alien",
            image="http://example.com/image.jpg",
        )
        horror_item.set_genres_from_metadata({"genres": ["Horror"]})
        movie = Movie(
            item=horror_item,
            user=stranger,
            status=Status.COMPLETED.value,
        )
        Movie.save_base(movie)

        response = self.client.get(self.url)
        self.assertEqual(response.context["genre_choices"], ["Action", "Drama"])


class GenreBackfill(TestCase):
    """Tests for the genre backfill task."""

    def setUp(self):
        """Prepare a tracked item without genres and one with genres."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.empty = Item.objects.create(
            media_id="10",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Inception",
            image="http://example.com/image.jpg",
        )
        self.filled = Item.objects.create(
            media_id="11",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="The Matrix",
            image="http://example.com/image.jpg",
        )
        self.filled.set_genres_from_metadata({"genres": ["Sci-Fi"]})
        for item in (self.empty, self.filled):
            movie = Movie(
                item=item,
                user=self.user,
                status=Status.COMPLETED.value,
            )
            Movie.save_base(movie)

    @patch("app.providers.services.get_media_metadata")
    def test_backfills_only_items_without_genres(self, mock_metadata):
        """The task fetches metadata only for items lacking genres."""
        mock_metadata.return_value = {"genres": ["Action", "Thriller"]}
        result = backfill_item_genres()

        mock_metadata.assert_called_once_with(
            self.empty.media_type,
            self.empty.media_id,
            self.empty.source,
            [None],
        )
        self.assertEqual(
            set(self.empty.genres.values_list("name", flat=True)),
            {"Action", "Thriller"},
        )
        self.assertIn("1 filled", result)

    @patch("app.providers.services.get_media_metadata")
    def test_provider_failure_does_not_kill_run(self, mock_metadata):
        """A metadata error is logged and counted, not raised."""
        mock_metadata.side_effect = ValueError("provider down")
        result = backfill_item_genres()

        self.assertIn("1 failed", result)
