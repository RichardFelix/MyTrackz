import hashlib
import os
import tempfile
import time
from io import BytesIO
from unittest.mock import MagicMock, patch

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase, override_settings
from PIL import Image

from app.models import ImageCacheFormat, Item, MediaTypes, Sources
from app.tasks import (
    cache_item_image,
    cleanup_orphaned_item_images,
    evict_oversized_image_cache,
    transcode_cached_image_to_webp,
)


def _jpeg_bytes():
    """Return the raw bytes of a tiny valid JPEG image."""
    buffer = BytesIO()
    Image.new("RGB", (8, 8)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _mock_response(status_code=200, content_type="image/jpeg", chunks=None):
    response = MagicMock()
    response.status_code = status_code
    response.is_redirect = False
    response.headers = {"Content-Type": content_type}
    response.iter_content.return_value = iter(chunks or [_jpeg_bytes()])
    return response


def _mock_redirect(location, status_code=302):
    """A redirect response, as _safe_image_get sees a hop it must follow."""
    response = MagicMock()
    response.status_code = status_code
    response.is_redirect = True
    response.headers = {"Location": location}
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
        """A non-redirect error response is refused."""
        mock_get.return_value = _mock_response(status_code=404)

        result = cache_item_image(self.item.id, self.item.image)

        self.assertFalse(result)
        self.item.refresh_from_db()
        self.assertFalse(self.item.image_cached)

    @patch("app.tasks._is_safe_image_host", return_value=True)
    @patch("app.tasks.requests.get")
    def test_follows_redirect_to_final_image(self, mock_get, _mock_safe):
        """A cover served via redirect (OpenLibrary -> archive.org) is followed and cached."""
        mock_get.side_effect = [
            _mock_redirect("https://archive.org/download/covers/final.jpg"),
            _mock_response(),
        ]

        result = cache_item_image(self.item.id, self.item.image)

        self.assertTrue(result)
        self.item.refresh_from_db()
        self.assertTrue(self.item.image_cached)
        self.assertEqual(mock_get.call_count, 2)
        # Every hop must be requested with automatic following disabled so each
        # target host can be revalidated before we connect to it.
        for call in mock_get.call_args_list:
            self.assertFalse(call.kwargs.get("allow_redirects", True))

    @patch("app.tasks.requests.get")
    def test_rejects_redirect_to_unsafe_host(self, mock_get):
        """A redirect pointing at a private/internal host is refused, not followed."""
        mock_get.return_value = _mock_redirect("http://169.254.169.254/latest/meta-data")

        # Only the internal redirect target resolves unsafe; the original URL is fine.
        def is_safe(url):
            return "169.254.169.254" not in url

        with patch("app.tasks._is_safe_image_host", side_effect=is_safe):
            result = cache_item_image(self.item.id, self.item.image)

        self.assertFalse(result)
        self.item.refresh_from_db()
        self.assertFalse(self.item.image_cached)

    @patch("app.tasks._is_safe_image_host", return_value=True)
    @patch("app.tasks.requests.get")
    def test_rejects_overlong_redirect_chain(self, mock_get, _mock_safe):
        """A redirect loop/chain longer than the cap is refused instead of looping forever."""
        mock_get.return_value = _mock_redirect("https://example.com/again.jpg")

        result = cache_item_image(self.item.id, self.item.image)

        self.assertFalse(result)
        self.item.refresh_from_db()
        self.assertFalse(self.item.image_cached)

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


class ItemSaveUpdateFieldsTests(TestCase):
    """Test that Item.save() persists image_cached alongside partial saves."""

    @patch("app.tasks.cache_item_image.delay")
    def test_update_fields_image_also_persists_image_cached(self, _mock_delay):
        """save(update_fields=["image"]) must not leave a stale True flag in the DB."""
        item = Item.objects.create(
            media_id="1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image="https://image.tmdb.org/t/p/w500/old.jpg",
        )
        Item.objects.filter(id=item.id).update(image_cached=True)
        item.refresh_from_db()

        item.image = "https://image.tmdb.org/t/p/w500/new.jpg"
        item.save(update_fields=["image"])

        item.refresh_from_db()
        self.assertFalse(item.image_cached)


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


class EvictOversizedImageCacheTaskTests(TestCase):
    """Test the evict_oversized_image_cache Celery task."""

    def setUp(self):
        """Use a fresh MEDIA_ROOT per test.

        Byte-precise assertions here need a cache directory with only the
        files this test itself creates.
        """
        self.enterContext(override_settings(MEDIA_ROOT=tempfile.mkdtemp()))

    def _cache_item(self, media_id, image_url, age_seconds):
        """Cache an image for a new Item and backdate the cached file's mtime."""
        with patch("app.tasks.cache_item_image.delay"):
            item = Item.objects.create(
                media_id=media_id,
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
                title=f"Movie {media_id}",
                image=image_url,
            )
        with (
            patch("app.tasks._is_safe_image_host", return_value=True),
            patch("app.tasks.requests.get", return_value=_mock_response()),
        ):
            cache_item_image(item.id, item.image)
        item.refresh_from_db()

        relpath = _relative_media_path(item.cached_image_url)
        backdated = time.time() - age_seconds
        os.utime(default_storage.path(relpath), (backdated, backdated))

        return item, relpath

    def test_evicts_oldest_files_first_until_under_cap(self):
        """Oldest cached files are evicted first, until the cache fits under the cap."""
        oldest, oldest_path = self._cache_item(
            "1", "https://image.tmdb.org/t/p/w500/evict-a.jpg", age_seconds=300
        )
        middle, middle_path = self._cache_item(
            "2", "https://image.tmdb.org/t/p/w500/evict-b.jpg", age_seconds=200
        )
        newest, newest_path = self._cache_item(
            "3", "https://image.tmdb.org/t/p/w500/evict-c.jpg", age_seconds=100
        )
        one_file_size = default_storage.size(newest_path)

        with override_settings(IMAGE_CACHE_MAX_BYTES=one_file_size + 1):
            deleted_count = evict_oversized_image_cache()

        self.assertEqual(deleted_count, 2)
        oldest.refresh_from_db()
        middle.refresh_from_db()
        newest.refresh_from_db()
        self.assertFalse(oldest.image_cached)
        self.assertFalse(middle.image_cached)
        self.assertTrue(newest.image_cached)
        self.assertFalse(default_storage.exists(oldest_path))
        self.assertFalse(default_storage.exists(middle_path))
        self.assertTrue(default_storage.exists(newest_path))

    def test_noop_when_already_under_cap(self):
        """No files are deleted when the cache is already within budget."""
        item, relpath = self._cache_item(
            "4", "https://image.tmdb.org/t/p/w500/evict-noop.jpg", age_seconds=0
        )

        with override_settings(IMAGE_CACHE_MAX_BYTES=10 * 1024 * 1024):
            deleted_count = evict_oversized_image_cache()

        self.assertEqual(deleted_count, 0)
        item.refresh_from_db()
        self.assertTrue(item.image_cached)
        self.assertTrue(default_storage.exists(relpath))

    def test_disabled_when_cap_is_non_positive(self):
        """A zero or negative cap disables eviction entirely."""
        item, relpath = self._cache_item(
            "5", "https://image.tmdb.org/t/p/w500/evict-disabled.jpg", age_seconds=0
        )

        with override_settings(IMAGE_CACHE_MAX_BYTES=0):
            deleted_count = evict_oversized_image_cache()

        self.assertEqual(deleted_count, 0)
        item.refresh_from_db()
        self.assertTrue(item.image_cached)
        self.assertTrue(default_storage.exists(relpath))


class TranscodeCachedImageToWebpTaskTests(TestCase):
    """Test the transcode_cached_image_to_webp Celery task."""

    def setUp(self):
        """Use a fresh MEDIA_ROOT per test to avoid cross-test file collisions."""
        self.enterContext(override_settings(MEDIA_ROOT=tempfile.mkdtemp()))

    def _legacy_jpeg_item(self, media_id, image_url):
        """Simulate a pre-WebP Item with an already-cached JPEG file on disk."""
        with patch("app.tasks.cache_item_image.delay"):
            item = Item.objects.create(
                media_id=media_id,
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
                title=f"Movie {media_id}",
                image=image_url,
            )
        digest = hashlib.sha256(image_url.encode()).hexdigest()
        jpg_path = f"items/{digest[:2]}/{digest}.jpg"
        default_storage.save(jpg_path, ContentFile(_jpeg_bytes()))
        Item.objects.filter(id=item.id).update(
            image_cached=True,
            image_cache_format=ImageCacheFormat.JPEG.value,
        )
        item.refresh_from_db()
        return item, jpg_path, digest

    def test_transcodes_jpeg_to_webp_and_removes_old_file(self):
        """A legacy JPEG-cached item is re-encoded to WebP and the old file removed."""
        item, jpg_path, digest = self._legacy_jpeg_item(
            "1", "https://image.tmdb.org/t/p/w500/legacy.jpg"
        )

        result = transcode_cached_image_to_webp(item.id)

        self.assertTrue(result)
        item.refresh_from_db()
        self.assertEqual(item.image_cache_format, ImageCacheFormat.WEBP.value)
        self.assertFalse(default_storage.exists(jpg_path))
        webp_path = f"items/{digest[:2]}/{digest}.webp"
        self.assertTrue(default_storage.exists(webp_path))
        self.assertTrue(item.cached_image_url.endswith(f"{digest}.webp"))

    def test_noop_when_already_webp(self):
        """An item already tagged as WebP is left untouched."""
        item, jpg_path, _digest = self._legacy_jpeg_item(
            "2", "https://image.tmdb.org/t/p/w500/already.jpg"
        )
        Item.objects.filter(id=item.id).update(
            image_cache_format=ImageCacheFormat.WEBP.value,
        )

        result = transcode_cached_image_to_webp(item.id)

        self.assertFalse(result)
        self.assertTrue(default_storage.exists(jpg_path))

    def test_noop_when_source_file_missing(self):
        """Nothing breaks if the on-disk file is missing despite the DB flag."""
        item, jpg_path, _digest = self._legacy_jpeg_item(
            "3", "https://image.tmdb.org/t/p/w500/missing.jpg"
        )
        default_storage.delete(jpg_path)

        result = transcode_cached_image_to_webp(item.id)

        self.assertFalse(result)
        item.refresh_from_db()
        self.assertEqual(item.image_cache_format, ImageCacheFormat.JPEG.value)


def _relative_media_path(media_url):
    from django.conf import settings

    return media_url.removeprefix(settings.MEDIA_URL)


def _relative_media_path_exists(media_url):
    return default_storage.exists(_relative_media_path(media_url))
