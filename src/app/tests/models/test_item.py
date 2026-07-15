from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.templatetags.static import static
from django.test import TestCase

from app.models import (
    Item,
    MediaTypes,
    Sources,
)

mock_path = Path(__file__).resolve().parent.parent / "mock_data"


class ItemModel(TestCase):
    """Test case for the Item model."""

    def setUp(self):
        """Set up test data for Item model."""
        self.item = Item.objects.create(
            media_id="1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Test Movie",
            image="http://example.com/image.jpg",
        )

    def test_item_creation(self):
        """Test the creation of an Item instance."""
        self.assertEqual(self.item.media_id, "1")
        self.assertEqual(self.item.media_type, MediaTypes.MOVIE.value)
        self.assertEqual(self.item.title, "Test Movie")
        self.assertEqual(self.item.image, "http://example.com/image.jpg")

    def test_item_str_representation(self):
        """Test the string representation of an Item."""
        self.assertEqual(str(self.item), "Test Movie")

    def test_item_with_season_and_episode(self):
        """Test the string representation of an Item with season and episode."""
        item = Item.objects.create(
            media_id="2",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Test Show",
            image="http://example.com/image2.jpg",
            season_number=1,
            episode_number=2,
        )
        self.assertEqual(str(item), "Test Show S1E2")

    @patch("app.tasks.cache_item_image.delay")
    def test_save_with_new_image_queues_cache_task(self, mock_delay):
        """Creating an Item with a real image URL queues the cache task."""
        item = Item.objects.create(
            media_id="3",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Another Movie",
            image="http://example.com/image3.jpg",
        )

        mock_delay.assert_called_once_with(item.id, "http://example.com/image3.jpg")

    @patch("app.tasks.cache_item_image.delay")
    def test_save_without_image_change_does_not_queue(self, mock_delay):
        """Saving an Item without changing its image does not requeue the cache task."""
        mock_delay.reset_mock()

        self.item.title = "Updated Title"
        self.item.save()

        mock_delay.assert_not_called()

    @patch("app.tasks.cache_item_image.delay")
    def test_save_with_changed_image_resets_cached_flag_and_requeues(self, mock_delay):
        """Changing an Item's image resets image_cached and requeues the cache task."""
        self.item.image_cached = True
        self.item.save()
        mock_delay.reset_mock()

        self.item.image = "http://example.com/new-image.jpg"
        self.item.save()

        self.item.refresh_from_db()
        self.assertFalse(self.item.image_cached)
        mock_delay.assert_called_once_with(
            self.item.id, "http://example.com/new-image.jpg"
        )

    @patch("app.tasks.cache_item_image.delay")
    def test_save_with_img_none_does_not_queue(self, mock_delay):
        """Do not cache the IMG_NONE placeholder."""
        item = Item.objects.create(
            media_id="4",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="No Image Movie",
            image=settings.IMG_NONE,
        )

        mock_delay.assert_not_called()
        self.assertFalse(item.image_cached)

    def test_cached_image_url_returns_original_when_not_cached(self):
        """Return the original image URL when image_cached is False."""
        self.assertEqual(self.item.cached_image_url, self.item.image)

    def test_cached_image_url_returns_local_path_when_cached(self):
        """Return a local /media/ path derived from the image URL when cached."""
        self.item.image_cached = True
        self.item.save()

        cached_url = self.item.cached_image_url

        self.assertTrue(cached_url.startswith(settings.MEDIA_URL))
        self.assertTrue(cached_url.endswith(".jpg"))
        self.assertNotEqual(cached_url, self.item.image)

    def test_cached_image_url_falls_back_to_local_placeholder(self):
        """Return the local placeholder when the item has no image at all."""
        item = Item.objects.create(
            media_id="5",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Empty Image Movie",
            image="",
        )

        self.assertEqual(item.cached_image_url, static("img/no-image.svg"))

    def test_cached_image_url_resolves_img_none_to_local_placeholder(self):
        """Return the local placeholder instead of the remote IMG_NONE sentinel."""
        item = Item.objects.create(
            media_id="6",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Sentinel Image Movie",
            image=settings.IMG_NONE,
        )

        self.assertEqual(item.cached_image_url, static("img/no-image.svg"))
