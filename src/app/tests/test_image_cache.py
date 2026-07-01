import tempfile
from io import BytesIO
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from PIL import Image

from app.models import Item, MediaTypes, Sources
from app.tasks import cache_item_image, cleanup_orphaned_item_images


def _jpeg_bytes():
    """Return the raw bytes of a tiny valid JPEG image."""
    buffer = BytesIO()
    Image.new("RGB", (8, 8)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _mock_response(status_code=200, content_type="image/jpeg", chunks=None):
    response = MagicMock()
    response.status_code = status_code
    response.headers = {"Content-Type": content_type}
    response.iter_content.return_value = iter(chunks or [_jpeg_bytes()])
    return response


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class CacheItemImageTaskTests(TestCase):
    """Test the cache_item_image Celery task."""

    def setUp(self):
        """Create an Item with an external image URL."""
        self.item = Item.objects.create(
            media_id="1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image="https://image.tmdb.org/t/p/w500/test.jpg",
        )

    @patch("app.tasks._is_safe_image_host", return_value=True)
    @patch("app.tasks.requests.get")
    def test_successful_download_marks_item_cached(self, mock_get, _mock_safe):
        """A valid image response flips image_cached to True."""
        mock_get.return_value = _mock_response()

        result = cache_item_image(self.item.id, self.item.image)

        self.assertTrue(result)
        self.item.refresh_from_db()
        self.assertTrue(self.item.image_cached)

    @patch("app.tasks._is_safe_image_host", return_value=True)
    @patch("app.tasks.requests.get")
    def test_rejects_disallowed_content_type(self, mock_get, _mock_safe):
        """A non-image Content-Type is refused."""
        mock_get.return_value = _mock_response(content_type="text/html")

        result = cache_item_image(self.item.id, self.item.image)

        self.assertFalse(result)
        self.item.refresh_from_db()
        self.assertFalse(self.item.image_cached)

    @patch("app.tasks._is_safe_image_host", return_value=True)
    @patch("app.tasks.requests.get")
    def test_rejects_response_exceeding_size_cap(self, mock_get, _mock_safe):
        """A response exceeding IMAGE_DOWNLOAD_MAX_BYTES is refused."""
        oversized_chunk = b"a" * (5 * 1024 * 1024 + 1)
        mock_get.return_value = _mock_response(chunks=[oversized_chunk])

        result = cache_item_image(self.item.id, self.item.image)

        self.assertFalse(result)
        self.item.refresh_from_db()
        self.assertFalse(self.item.image_cached)

    @patch("app.tasks._is_safe_image_host", return_value=True)
    @patch("app.tasks.requests.get")
    def test_rejects_invalid_image_bytes(self, mock_get, _mock_safe):
        """Bytes that don't parse as a real image are refused, even with a spoofed type."""
        mock_get.return_value = _mock_response(chunks=[b"not an image"])

        result = cache_item_image(self.item.id, self.item.image)

        self.assertFalse(result)
        self.item.refresh_from_db()
        self.assertFalse(self.item.image_cached)

    @patch("app.tasks._is_safe_image_host", return_value=True)
    @patch("app.tasks.requests.get")
    def test_rejects_non_200_status(self, mock_get, _mock_safe):
        """A redirect or error response is refused (redirects are never followed)."""
        mock_get.return_value = _mock_response(status_code=302)

        result = cache_item_image(self.item.id, self.item.image)

        self.assertFalse(result)
        mock_get.assert_called_once()
        self.assertFalse(mock_get.call_args.kwargs.get("allow_redirects", True))

    def test_rejects_unsafe_host_without_making_a_request(self):
        """A URL resolving to a private/loopback address is refused before any request."""
        with patch("app.tasks.requests.get") as mock_get:
            result = cache_item_image(self.item.id, "http://127.0.0.1/image.jpg")

        self.assertFalse(result)
        mock_get.assert_not_called()

    @patch("app.tasks._is_safe_image_host", return_value=True)
    @patch("app.tasks.requests.get")
    def test_stale_url_race_does_not_mark_cached(self, mock_get, _mock_safe):
        """If the Item's image changed since the task was queued, skip flipping the flag."""
        mock_get.return_value = _mock_response()
        original_image = self.item.image

        # Suppress the auto-triggered task from this save itself; only the
        # explicit call below (simulating the original, now-stale task) matters.
        with patch("app.tasks.cache_item_image.delay"):
            self.item.image = "https://image.tmdb.org/t/p/w500/changed.jpg"
            self.item.save()

        result = cache_item_image(self.item.id, original_image)

        self.assertTrue(result)
        self.item.refresh_from_db()
        self.assertFalse(self.item.image_cached)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class CleanupOrphanedItemImagesTaskTests(TestCase):
    """Test the cleanup_orphaned_item_images Celery task."""

    def test_deletes_files_not_referenced_by_any_item(self):
        """Files with no matching Item are deleted; files for live Items survive."""
        # Item.save() always resets image_cached on creation (a brand-new row's
        # image always counts as "changed"), so suppress the auto-triggered
        # task here and cache the images explicitly afterward, under control.
        with patch("app.tasks.cache_item_image.delay"):
            live_item = Item.objects.create(
                media_id="1",
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
                title="Live Movie",
                image="https://image.tmdb.org/t/p/w500/live.jpg",
            )
            orphan_item = Item.objects.create(
                media_id="2",
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
                title="Orphan Movie",
                image="https://image.tmdb.org/t/p/w500/orphan.jpg",
            )

        with (
            patch("app.tasks._is_safe_image_host", return_value=True),
            patch("app.tasks.requests.get") as mock_get,
        ):
            mock_get.side_effect = [_mock_response(), _mock_response()]
            cache_item_image(live_item.id, live_item.image)
            cache_item_image(orphan_item.id, orphan_item.image)

        live_item.refresh_from_db()
        orphan_item.refresh_from_db()
        self.assertTrue(live_item.image_cached)
        self.assertTrue(orphan_item.image_cached)
        live_url = live_item.cached_image_url

        # The orphan's URL changes (e.g. the provider reshuffled its CDN path),
        # leaving its previously cached file with no live Item referencing it.
        with patch("app.tasks.cache_item_image.delay"):
            orphan_item.image = "https://image.tmdb.org/t/p/w500/changed.jpg"
            orphan_item.save()

        deleted_count = cleanup_orphaned_item_images()

        self.assertEqual(deleted_count, 1)
        self.assertTrue(_relative_media_path_exists(live_url))


def _relative_media_path_exists(media_url):
    from django.conf import settings
    from django.core.files.storage import default_storage

    relpath = media_url.removeprefix(settings.MEDIA_URL)
    return default_storage.exists(relpath)
