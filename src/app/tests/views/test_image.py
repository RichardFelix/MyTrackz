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
        self.assertEqual(self.item.image_original, PROVIDER_IMAGE)
        self.assertTrue(self.item.image_locked)
        self.assertFalse(self.item.image_cached)
        mock_cache.assert_called_once_with(self.item.id, CUSTOM_IMAGE)

    @patch("app.tasks.cache_item_image.delay")
    def test_image_save_revert(self, _mock_cache):
        """Reverting restores the exact stored original, not a re-fetch."""
        self.client.post(
            reverse("image_save", kwargs=self.url_kwargs) + "?next=/",
            {"image": CUSTOM_IMAGE},
        )

        self.client.post(
            reverse("image_save", kwargs=self.url_kwargs) + "?next=/",
            {"operation": "revert"},
        )

        self.item.refresh_from_db()
        self.assertEqual(self.item.image, PROVIDER_IMAGE)
        self.assertEqual(self.item.image_original, "")
        self.assertFalse(self.item.image_locked)

    @patch("app.tasks.cache_item_image.delay")
    def test_repeated_saves_keep_first_original(self, _mock_cache):
        """Consecutive custom saves preserve the first original for revert."""
        for url in (CUSTOM_IMAGE, "http://example.com/second-custom.jpg"):
            self.client.post(
                reverse("image_save", kwargs=self.url_kwargs) + "?next=/",
                {"image": url},
            )

        self.client.post(
            reverse("image_save", kwargs=self.url_kwargs) + "?next=/",
            {"operation": "revert"},
        )

        self.item.refresh_from_db()
        self.assertEqual(self.item.image, PROVIDER_IMAGE)

    @patch("app.tasks.cache_item_image.delay")
    @patch("app.views.services.get_media_metadata")
    def test_revert_falls_back_to_provider(self, mock_metadata, _mock_cache):
        """Items locked before image_original existed re-fetch the provider's."""
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
    def test_manual_item_revert(self, _mock_cache):
        """Manual items revert to their stored original without a provider."""
        manual_item = Item.objects.create(
            media_id=Item.generate_manual_id(),
            source=Sources.MANUAL.value,
            media_type=MediaTypes.BOOK.value,
            title="My Book",
            image=PROVIDER_IMAGE,
        )
        kwargs = {
            "source": manual_item.source,
            "media_type": manual_item.media_type,
            "media_id": manual_item.media_id,
        }

        self.client.post(
            reverse("image_save", kwargs=kwargs) + "?next=/",
            {"image": CUSTOM_IMAGE},
        )
        self.client.post(
            reverse("image_save", kwargs=kwargs) + "?next=/",
            {"operation": "revert"},
        )

        manual_item.refresh_from_db()
        self.assertEqual(manual_item.image, PROVIDER_IMAGE)
        self.assertFalse(manual_item.image_locked)

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
