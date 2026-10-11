from unittest.mock import patch
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from app.models import Item, MediaTypes, Movie, Sources, Status


class LibraryItemUpdateTests(TestCase):
    """Test in-place library edits that swap one card instead of reloading."""

    def setUp(self):
        """Create a user with a few tracked movies."""
        metadata_patcher = patch("app.providers.services.get_media_metadata")
        self.mock_get_media_metadata = metadata_patcher.start()
        self.mock_get_media_metadata.return_value = {"max_progress": 1}
        self.addCleanup(metadata_patcher.stop)

        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

        self.movies = []
        for i in range(1, 4):
            item = Item.objects.create(
                media_id=str(i),
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
                title=f"Test Movie {i}",
                image="http://example.com/image.jpg",
            )
            self.movies.append(
                Movie.objects.create(
                    item=item,
                    user=self.user,
                    status=Status.IN_PROGRESS.value,
                    progress=0,
                    score=i,
                ),
            )

    def _save(self, movie, filters=None, **fields):
        data = {
            "instance_id": movie.id,
            "media_id": movie.item.media_id,
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.MOVIE.value,
            "status": movie.status,
            "progress": movie.progress,
            "score": movie.score,
            "library_item": "1",
            "library_filters": urlencode(filters or {"layout": "grid"}),
            **fields,
        }
        return self.client.post(
            reverse("media_save"), data, headers={"hx-request": "true"}
        )

    def test_save_returns_updated_card(self):
        """Saving from the library returns just the card, not a redirect."""
        movie = self.movies[0]
        response = self._save(movie, score=7)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/components/media_card.html")
        self.assertNotIn("HX-Reswap", response)
        self.assertContains(response, 'id="media-item-count"')
        self.assertContains(response, "3 items")
        self.assertContains(response, '"library_item": "1"')
        movie.refresh_from_db()
        self.assertEqual(movie.score, 7)

    def test_save_table_layout_returns_row(self):
        """The table layout gets the row cells back."""
        response = self._save(self.movies[0], filters={"layout": "table"}, score=7)

        self.assertTemplateUsed(response, "app/components/media_table_row.html")
        self.assertTemplateNotUsed(response, "app/components/media_card.html")

    def test_save_out_of_filter_removes_card(self):
        """An item edited out of the active status filter is removed."""
        response = self._save(
            self.movies[0],
            filters={"layout": "grid", "status": Status.IN_PROGRESS.value},
            status=Status.COMPLETED.value,
            progress=1,
        )

        self.assertEqual(response["HX-Reswap"], "delete")
        self.assertContains(response, "2 items")
        self.assertTemplateNotUsed(response, "app/components/media_card.html")

    def test_invalid_save_keeps_modal_open(self):
        """A validation error swaps nothing and reports the error."""
        response = self._save(self.movies[0], score=999)

        self.assertEqual(response["HX-Reswap"], "none")
        self.assertContains(response, 'id="messages-list"')
        self.movies[0].refresh_from_db()
        self.assertEqual(self.movies[0].score, 1)

    def test_delete_removes_card(self):
        """Deleting from the library removes the card in place."""
        movie = self.movies[0]
        response = self.client.post(
            reverse("media_delete"),
            {
                "instance_id": movie.id,
                "media_type": MediaTypes.MOVIE.value,
                "library_item": "1",
                "library_filters": "layout=grid",
            },
            headers={"hx-request": "true"},
        )

        self.assertEqual(response["HX-Reswap"], "delete")
        self.assertContains(response, "2 items")
        self.assertFalse(Movie.objects.filter(id=movie.id).exists())

    def test_delete_repeat_keeps_card_with_previous_entry(self):
        """Deleting one repeat leaves the card showing the remaining entry."""
        movie = self.movies[0]
        repeat = Movie.objects.create(
            item=movie.item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
            progress=0,
            score=4,
        )
        response = self.client.post(
            reverse("media_delete"),
            {
                "instance_id": repeat.id,
                "media_type": MediaTypes.MOVIE.value,
                "library_item": "1",
                "library_filters": "layout=grid",
            },
            headers={"hx-request": "true"},
        )

        self.assertNotIn("HX-Reswap", response)
        self.assertEqual(response.context["media"].id, movie.id)

    def test_regular_save_still_redirects(self):
        """Saves from outside the library keep the full-page redirect."""
        movie = self.movies[0]
        response = self.client.post(
            reverse("media_save"),
            {
                "instance_id": movie.id,
                "media_id": movie.item.media_id,
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.MOVIE.value,
                "status": movie.status,
                "progress": 0,
                "score": 5,
            },
        )

        self.assertEqual(response.status_code, 302)

    def test_track_modal_library_buttons(self):
        """The track modal opened from the library posts via htmx."""
        movie = self.movies[0]
        url = reverse(
            "track_modal",
            args=[Sources.TMDB.value, MediaTypes.MOVIE.value, movie.item.media_id],
        )
        response = self.client.get(
            url,
            {"return_url": "/", "instance_id": movie.id, "library_item": "1"},
        )
        self.assertContains(response, f'hx-target="#library-item-{movie.item_id}"')
        self.assertNotContains(response, "formaction=")

        response = self.client.get(url, {"return_url": "/", "instance_id": movie.id})
        self.assertNotContains(response, "hx-post=")
        self.assertContains(response, "formaction=")


class LibraryRemovedOffsetTests(TestCase):
    """Test that removed cards shift later library pages back."""

    def setUp(self):
        """Create enough movies for more than one page."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)
        for i in range(40):
            item = Item.objects.create(
                media_id=str(i),
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
                title=f"Movie {i:02d}",
                image="http://example.com/image.jpg",
            )
            Movie.objects.create(
                item=item, user=self.user, status=Status.PLANNING.value
            )

    def _page(self, **params):
        return self.client.get(
            reverse("medialist", args=[self.user.username, MediaTypes.MOVIE.value]),
            {"layout": "grid", "sort": "title", **params},
            headers={"hx-request": "true"},
        )

    def test_removed_pulls_next_page_back(self):
        """Page 2 starts earlier by the number of cards removed client-side."""
        first_page = list(self._page().context["media_list"])
        # simulate removing the first loaded card
        first_page[0].delete()

        response = self._page(page=2, removed=1)
        titles = [media.item.title for media in response.context["media_list"]]

        # "Movie 32" moved into page 1's range and must not be skipped
        self.assertEqual(titles[0], "Movie 32")
        self.assertEqual(len(titles), 8)
        self.assertFalse(response.context["media_list"].has_next())

    def test_removed_carried_to_next_sentinel(self):
        """The next-page sentinel keeps the cumulative removed count."""
        for i in range(40, 70):
            item = Item.objects.create(
                media_id=str(i),
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
                title=f"Movie {i:02d}",
                image="http://example.com/image.jpg",
            )
            Movie.objects.create(
                item=item, user=self.user, status=Status.PLANNING.value
            )

        response = self._page(page=2, removed=2)
        self.assertContains(response, "page=3&removed=2")

    def test_invalid_removed_ignored(self):
        """A malformed removed count falls back to normal pagination."""
        response = self._page(page=2, removed="abc")
        self.assertEqual(len(response.context["media_list"]), 8)
