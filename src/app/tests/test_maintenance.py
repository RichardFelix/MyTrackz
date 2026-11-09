import tempfile
from io import BytesIO
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.storage import default_storage
from django.test import TestCase, override_settings
from PIL import Image

from app import helpers
from app.models import Anime, Genre, Item, MediaTypes, Movie, Sources, Status
from app.tasks import (
    backfill_item_formats,
    backfill_item_genres,
    cache_item_image,
    decode_image,
    prune_unreferenced_items,
)
from events.models import Event
from lists.models import CustomList, CustomListItem


def _image_bytes(size=(8, 8), image_format="JPEG"):
    """Return the raw bytes of a valid image of the given size."""
    buffer = BytesIO()
    Image.new("RGB", size).save(buffer, format=image_format)
    return buffer.getvalue()


def _mock_response(data):
    response = MagicMock()
    response.status_code = 200
    response.is_redirect = False
    response.headers = {"Content-Type": "image/jpeg"}
    response.iter_content.return_value = iter([data])
    return response


def _movie_item(media_id, **kwargs):
    """Create a TMDB movie Item without queueing an image download."""
    return Item.objects.create(
        media_id=str(media_id),
        source=Sources.TMDB.value,
        media_type=MediaTypes.MOVIE.value,
        title=kwargs.pop("title", f"Movie {media_id}"),
        image=kwargs.pop("image", f"https://image.tmdb.org/t/p/w500/{media_id}.jpg"),
        **kwargs,
    )


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class ImageDecodingTests(TestCase):
    """Bad or oversized image bytes are refused cleanly, never retried."""

    def setUp(self):
        """Create an Item with an external image URL."""
        self.item = _movie_item(1)

    def _cache(self, data):
        with (
            patch("app.tasks._is_safe_image_host", return_value=True),
            patch("app.tasks.get_image", return_value=_mock_response(data)),
        ):
            return cache_item_image(self.item.id, self.item.image)

    def test_truncated_image_is_refused_without_retrying(self):
        """A file cut off mid-download is not an image, not a transient error."""
        result = self._cache(_image_bytes((64, 64))[:-40])

        self.assertFalse(result)
        self.item.refresh_from_db()
        self.assertFalse(self.item.image_cached)

    def test_decompression_bomb_is_refused(self):
        """An image over Pillow's pixel limit is rejected, not raised."""
        with patch("app.tasks.Image.MAX_IMAGE_PIXELS", 10):
            self.assertIsNone(decode_image(_image_bytes((64, 64))))
            result = self._cache(_image_bytes((64, 64)))

        self.assertFalse(result)

    def test_large_images_are_shrunk_to_the_cache_size(self):
        """Provider originals are stored at display size, keeping their shape."""
        self._cache(_image_bytes((2000, 3000)))

        self.item.refresh_from_db()
        relpath = self.item.cached_image_url.removeprefix(settings.MEDIA_URL)
        with default_storage.open(relpath) as cached_file:
            width, height = Image.open(cached_file).size
        max_width, max_height = settings.IMAGE_CACHE_MAX_DIMENSIONS
        self.assertEqual((width, height), (max_width, max_height))

    def test_small_images_are_not_enlarged(self):
        """An image already under the cap keeps its dimensions."""
        self._cache(_image_bytes((120, 180)))

        self.item.refresh_from_db()
        relpath = self.item.cached_image_url.removeprefix(settings.MEDIA_URL)
        with default_storage.open(relpath) as cached_file:
            self.assertEqual(Image.open(cached_file).size, (120, 180))

    def test_recaching_a_url_does_not_leave_a_duplicate_file(self):
        """Two items sharing an image URL share one file, with no stray copy."""
        _movie_item(2, image=self.item.image)

        self._cache(_image_bytes())
        self._cache(_image_bytes())

        _dirs, shards = default_storage.listdir(settings.IMAGE_CACHE_DIR), None
        files = []
        for shard in _dirs[0]:
            shards = default_storage.listdir(f"{settings.IMAGE_CACHE_DIR}/{shard}")
            files.extend(shards[1])
        self.assertEqual(len(files), 1)


class PruneUnreferencedItemsTests(TestCase):
    """Only Item rows nothing uses are pruned."""

    def setUp(self):
        """Create a user and start from an empty feed cache."""
        cache.clear()
        self.user = get_user_model().objects.create_user(username="test")

    def test_prunes_only_rows_nothing_references(self):
        """Tracked, listed, scheduled, excluded and customised rows all survive."""
        orphan = _movie_item(1)
        tracked = _movie_item(2)
        Movie.objects.create(
            item=tracked,
            user=self.user,
            status=Status.PLANNING.value,
        )
        listed = _movie_item(3)
        custom_list = CustomList.objects.create(name="Favourites", owner=self.user)
        CustomListItem.objects.create(item=listed, custom_list=custom_list)
        scheduled = _movie_item(4)
        Event.objects.create(item=scheduled, datetime="2030-01-01T00:00:00Z")
        excluded = _movie_item(5)
        self.user.notification_excluded_items.add(excluded)
        customised = _movie_item(6, image_locked=True)

        deleted = prune_unreferenced_items()

        self.assertEqual(deleted, 1)
        self.assertFalse(Item.objects.filter(pk=orphan.pk).exists())
        for kept in (tracked, listed, scheduled, excluded, customised):
            self.assertTrue(Item.objects.filter(pk=kept.pk).exists(), kept)

    def test_keeps_rows_shown_in_a_cached_feed(self):
        """A suggestion's row must outlive the cached feed that displays it."""
        in_trending = _movie_item(1)
        in_discover = _movie_item(2)
        orphan = _movie_item(3)
        feed_entry = {
            "media_type": MediaTypes.MOVIE.value,
            "source": Sources.TMDB.value,
            "title": "Suggested",
        }
        cache.set(
            helpers.trending_feed_cache_key(),
            [{**feed_entry, "media_id": "1"}],
        )
        cache.set(
            helpers.discover_feed_cache_key(self.user.id),
            [{**feed_entry, "media_id": 2}],
        )

        prune_unreferenced_items()

        self.assertTrue(Item.objects.filter(pk=in_trending.pk).exists())
        self.assertTrue(Item.objects.filter(pk=in_discover.pk).exists())
        self.assertFalse(Item.objects.filter(pk=orphan.pk).exists())

    def test_pruned_genres_are_unlinked_not_deleted(self):
        """Pruning an item leaves the shared genre vocabulary alone."""
        orphan = _movie_item(1)
        orphan.genres.add(Genre.objects.create(name="Drama"))

        prune_unreferenced_items()

        self.assertFalse(Item.objects.filter(pk=orphan.pk).exists())
        self.assertTrue(Genre.objects.filter(name="Drama").exists())


class BackfillSkipTests(TestCase):
    """A backfill lookup that finds nothing is not repeated every week."""

    def setUp(self):
        """Track one movie and one anime that have no genres or format."""
        cache.clear()
        self.user = get_user_model().objects.create_user(username="test")
        self.movie_item = _movie_item(1)
        self.anime_item = Item.objects.create(
            media_id="1",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Anime",
            image="https://example.com/anime.jpg",
        )
        with patch("app.providers.services.get_media_metadata", return_value={}):
            Movie.objects.create(
                item=self.movie_item,
                user=self.user,
                status=Status.PLANNING.value,
            )
            Anime.objects.create(
                item=self.anime_item,
                user=self.user,
                status=Status.PLANNING.value,
            )

    @patch("app.providers.services.get_media_metadata")
    def test_genreless_item_is_looked_up_once(self, mock_metadata):
        """The second run skips items the provider had no genres for."""
        mock_metadata.return_value = {"genres": []}

        backfill_item_genres()
        calls_after_first_run = mock_metadata.call_count
        backfill_item_genres()

        self.assertGreater(calls_after_first_run, 0)
        self.assertEqual(mock_metadata.call_count, calls_after_first_run)

    @patch("app.providers.services.get_media_metadata")
    def test_failed_lookup_is_retried(self, mock_metadata):
        """A provider error is not remembered as 'nothing to capture'."""
        mock_metadata.side_effect = RuntimeError("provider down")

        backfill_item_genres()
        calls_after_first_run = mock_metadata.call_count
        backfill_item_genres()

        self.assertEqual(mock_metadata.call_count, calls_after_first_run * 2)

    @patch("app.providers.services.get_media_metadata")
    def test_formatless_anime_is_looked_up_once(self, mock_metadata):
        """The second run skips anime the provider had no format for."""
        mock_metadata.return_value = {"details": {}}

        backfill_item_formats()
        backfill_item_formats()

        self.assertEqual(mock_metadata.call_count, 1)
