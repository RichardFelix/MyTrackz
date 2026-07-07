from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from app.models import Item, MediaTypes, Sources

CUSTOM_IMAGE = "http://example.com/custom-poster.jpg"
PROVIDER_IMAGE = "http://example.com/provider-poster.jpg"


class ItemImage(TestCase):
    """Tests for the custom item image feature."""

    def setUp(self):
        """Prepare a user and a provider-backed item."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

        self.item = Item.objects.create(
            media_id="1",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Cowboy Bebop",
            image=PROVIDER_IMAGE,
        )
        self.url_kwargs = {
            "source": self.item.source,
            "media_type": self.item.media_type,
            "media_id": self.item.media_id,
        }

    def test_image_modal_renders(self):
        """The modal returns the form with the current image."""
        response = self.client.get(
            reverse("image_modal", kwargs=self.url_kwargs),
            {"return_url": "/"},
        )
        self.assertContains(response, "Change Image")
        self.assertContains(response, "image_save/mal/anime/1")

    @patch("app.tasks.cache_item_image.delay")
    def test_image_save_sets_custom_image(self, mock_cache):
        """Saving a custom URL updates, locks, and re-caches the image."""
        self.client.post(
            reverse("image_save", kwargs=self.url_kwargs) + "?next=/",
            {"image": CUSTOM_IMAGE},
        )

        self.item.refresh_from_db()
        self.assertEqual(self.item.image, CUSTOM_IMAGE)
        self.assertTrue(self.item.image_locked)
        self.assertFalse(self.item.image_cached)
        mock_cache.assert_called_once_with(self.item.id, CUSTOM_IMAGE)

    @patch("app.tasks.cache_item_image.delay")
    @patch("app.views.services.get_media_metadata")
    def test_image_save_revert(self, mock_metadata, _mock_cache):
        """Reverting restores the provider image and clears the lock."""
        self.item.image = CUSTOM_IMAGE
        self.item.image_locked = True
        self.item.save()
        mock_metadata.return_value = {
            "title": "Cowboy Bebop",
            "image": PROVIDER_IMAGE,
        }

        self.client.post(
            reverse("image_save", kwargs=self.url_kwargs) + "?next=/",
            {"operation": "revert"},
        )

        self.item.refresh_from_db()
        self.assertEqual(self.item.image, PROVIDER_IMAGE)
        self.assertFalse(self.item.image_locked)

    @patch("app.tasks.cache_item_image.delay")
    @patch("events.tasks.reload_calendar")
    @patch("app.views.services.get_media_metadata")
    def test_sync_preserves_locked_image(
        self,
        mock_metadata,
        _mock_reload,
        _mock_cache,
    ):
        """Metadata sync must not overwrite a user-set custom image."""
        self.item.image = CUSTOM_IMAGE
        self.item.image_locked = True
        self.item.save()
        mock_metadata.return_value = {
            "title": "Cowboy Bebop",
            "image": PROVIDER_IMAGE,
        }

        self.client.post(
            reverse("sync_metadata", kwargs=self.url_kwargs) + "?next=/",
            {"next": "/"},
        )

        self.item.refresh_from_db()
        self.assertEqual(self.item.image, CUSTOM_IMAGE)
        self.assertTrue(self.item.image_locked)

    @patch("app.tasks.cache_item_image.delay")
    @patch("events.tasks.reload_calendar")
    @patch("app.views.services.get_media_metadata")
    def test_sync_updates_unlocked_image(
        self,
        mock_metadata,
        _mock_reload,
        _mock_cache,
    ):
        """Metadata sync still refreshes images that aren't locked."""
        new_provider_image = "http://example.com/new-provider-poster.jpg"
        mock_metadata.return_value = {
            "title": "Cowboy Bebop",
            "image": new_provider_image,
        }

        self.client.post(
            reverse("sync_metadata", kwargs=self.url_kwargs) + "?next=/",
            {"next": "/"},
        )

        self.item.refresh_from_db()
        self.assertEqual(self.item.image, new_provider_image)
