import tempfile
from io import BytesIO
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.core.cache import cache
from django.core.files.storage import default_storage
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from app.models import Item, MediaTypes, Sources
from app.tasks import cache_item_image


def _jpeg_bytes():
    """Return the raw bytes of a tiny valid JPEG image."""
    buffer = BytesIO()
    Image.new("RGB", (8, 8)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _mock_response():
    response = MagicMock()
    response.status_code = 200
    response.is_redirect = False
    response.headers = {"Content-Type": "image/jpeg"}
    response.iter_content.return_value = iter([_jpeg_bytes()])
    return response


class AdvancedSettingsImageCacheTests(TestCase):
    """Tests for the image cache stats and purge on the advanced settings page."""

    def setUp(self):
        """Create a user, log in, and use a fresh MEDIA_ROOT for this test."""
        self.enterContext(override_settings(MEDIA_ROOT=tempfile.mkdtemp()))
        cache.clear()  # the page's size/count is cached between requests
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    def test_advanced_page_shows_empty_cache_stats(self):
        """With nothing cached, the page reports zero images."""
        response = self.client.get(reverse("advanced"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["image_cache_file_count"], 0)
        self.assertEqual(response.context["image_cache_size_bytes"], 0)

    def test_advanced_page_points_admins_at_the_admin_page(self):
        """The cache actions moved to the Admin page; Advanced only links there."""
        response = self.client.get(reverse("advanced"))

        self.assertContains(response, reverse("instance_admin"))
        self.assertNotContains(response, reverse("clear_search_cache"))
        self.assertNotContains(response, reverse("purge_image_cache"))

    def test_advanced_page_shows_cache_stats(self):
        """Cached images are reflected in the page's size/count context."""
        with patch("app.tasks.cache_item_image.delay"):
            item = Item.objects.create(
                media_id="1",
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
                title="Test Movie",
                image="https://image.tmdb.org/t/p/w500/test.jpg",
            )
        with (
            patch("app.tasks._is_safe_image_host", return_value=True),
            patch("app.tasks.get_image", return_value=_mock_response()),
        ):
            cache_item_image(item.id, item.image)

        response = self.client.get(reverse("advanced"))

        self.assertEqual(response.context["image_cache_file_count"], 1)
        self.assertGreater(response.context["image_cache_size_bytes"], 0)

    def test_purge_image_cache_deletes_files_and_resets_flag(self):
        """Purging removes cached files and flips image_cached back to False."""
        with patch("app.tasks.cache_item_image.delay"):
            item = Item.objects.create(
                media_id="1",
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
                title="Test Movie",
                image="https://image.tmdb.org/t/p/w500/test.jpg",
            )
        with (
            patch("app.tasks._is_safe_image_host", return_value=True),
            patch("app.tasks.get_image", return_value=_mock_response()),
        ):
            cache_item_image(item.id, item.image)
        item.refresh_from_db()
        cached_relpath = item.cached_image_url.removeprefix(settings.MEDIA_URL)
        self.assertTrue(default_storage.exists(cached_relpath))

        response = self.client.post(reverse("purge_image_cache"))

        self.assertRedirects(
            response,
            reverse("instance_admin"),
            fetch_redirect_response=False,
        )
        item.refresh_from_db()
        self.assertFalse(item.image_cached)
        self.assertFalse(default_storage.exists(cached_relpath))

        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages), 1)
        self.assertIn("Image cache purge started", str(messages[0]))

    def test_purge_image_cache_noop_when_empty(self):
        """Purging with nothing cached is a harmless no-op."""
        response = self.client.post(reverse("purge_image_cache"))

        self.assertRedirects(
            response,
            reverse("instance_admin"),
            fetch_redirect_response=False,
        )
        messages = list(get_messages(response.wsgi_request))
        self.assertIn("Image cache purge started", str(messages[0]))

    def test_cache_stats_refresh_after_a_purge(self):
        """The cached size/count is dropped by a purge instead of going stale."""
        with patch("app.tasks.cache_item_image.delay"):
            item = Item.objects.create(
                media_id="1",
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
                title="Test Movie",
                image="https://image.tmdb.org/t/p/w500/test.jpg",
            )
        with (
            patch("app.tasks._is_safe_image_host", return_value=True),
            patch("app.tasks.get_image", return_value=_mock_response()),
        ):
            cache_item_image(item.id, item.image)
        response = self.client.get(reverse("advanced"))
        self.assertEqual(response.context["image_cache_file_count"], 1)

        self.client.post(reverse("purge_image_cache"))

        response = self.client.get(reverse("advanced"))
        self.assertEqual(response.context["image_cache_file_count"], 0)


class AdvancedSettingsAccessTests(TestCase):
    """Instance-wide cache actions are limited to instance admins."""

    def setUp(self):
        """Create the owner account, then log in as a later, ordinary one."""
        cache.clear()
        user_model = get_user_model()
        self.owner_credentials = {"username": "owner", "password": "12345"}
        self.owner = user_model.objects.create_user(**self.owner_credentials)
        self.credentials = {"username": "second", "password": "12345"}
        self.user = user_model.objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    def test_page_hides_cache_actions(self):
        """An ordinary account sees the stats but no destructive controls."""
        response = self.client.get(reverse("advanced"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, reverse("purge_image_cache"))
        self.assertNotContains(response, reverse("clear_search_cache"))
        self.assertNotContains(response, reverse("instance_admin"))
        self.assertContains(response, "Only an instance admin")

    def test_purge_is_refused(self):
        """Posting the purge directly does not queue it."""
        with patch("users.views.purge_cached_images.delay") as purge:
            response = self.client.post(reverse("purge_image_cache"))

        self.assertRedirects(response, reverse("advanced"))
        purge.assert_not_called()
        messages = list(get_messages(response.wsgi_request))
        self.assertIn("Only an instance admin", str(messages[0]))

    def test_clear_search_cache_is_refused(self):
        """Posting the search-cache clear directly leaves the cache alone."""
        cache.set("search_tmdb_movie_test_1", {"results": []})

        response = self.client.post(reverse("clear_search_cache"))

        self.assertRedirects(response, reverse("advanced"))
        self.assertIsNotNone(cache.get("search_tmdb_movie_test_1"))

    def test_staff_account_is_allowed(self):
        """A staff flag grants access regardless of account age."""
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])

        with patch("users.views.purge_cached_images.delay") as purge:
            self.client.post(reverse("purge_image_cache"))

        purge.assert_called_once()

    def test_demo_owner_is_refused(self):
        """A demo account never manages the instance, even with the staff flag."""
        self.owner.is_demo = True
        self.owner.save(update_fields=["is_demo"])
        self.client.logout()
        self.client.login(**self.owner_credentials)

        with patch("users.views.purge_cached_images.delay") as purge:
            self.client.post(reverse("purge_image_cache"))

        purge.assert_not_called()
