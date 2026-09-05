import tempfile
from io import BytesIO
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
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
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    def test_advanced_page_shows_empty_cache_stats(self):
        """With nothing cached, the page reports zero images."""
        response = self.client.get(reverse("advanced"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["image_cache_file_count"], 0)
        self.assertEqual(response.context["image_cache_size_bytes"], 0)

    def test_advanced_page_confirms_destructive_cache_actions(self):
        """Cache actions are submitted only from their confirmation dialogs."""
        response = self.client.get(reverse("advanced"))

        self.assertContains(response, 'role="dialog"', count=2)
        self.assertContains(response, "Clear search cache?")
        self.assertContains(response, "Purge image cache?")
        self.assertContains(response, "Cancel", count=2)
        self.assertContains(response, "Proceed", count=2)
        self.assertContains(response, f'action="{reverse("clear_search_cache")}"')
        self.assertContains(response, f'action="{reverse("purge_image_cache")}"')

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

        self.assertRedirects(response, reverse("advanced"))
        item.refresh_from_db()
        self.assertFalse(item.image_cached)
        self.assertFalse(default_storage.exists(cached_relpath))

        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages), 1)
        self.assertIn("Successfully purged 1 cached image", str(messages[0]))

    def test_purge_image_cache_noop_when_empty(self):
        """Purging with nothing cached is a harmless no-op."""
        response = self.client.post(reverse("purge_image_cache"))

        self.assertRedirects(response, reverse("advanced"))
        messages = list(get_messages(response.wsgi_request))
        self.assertIn("Successfully purged 0 cached images", str(messages[0]))
