import tempfile
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from app.tasks import cache_user_profile_image
from users.helpers import cache_profile_image, profile_image_relpath

FIRST_IMAGE = "https://images.example/first.png"
SECOND_IMAGE = "https://images.example/second.png"


def _png_bytes():
    buffer = BytesIO()
    Image.new("RGB", (16, 16)).save(buffer, format="PNG")
    return buffer.getvalue()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class ProfileImageTests(TestCase):
    """Profile images are fetched by a worker and cleaned up when replaced."""

    def setUp(self):
        """Create a user, log in, and serve a valid image for every download."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

        download_patcher = patch(
            "users.helpers._download_image_bytes",
            return_value=_png_bytes(),
        )
        self.mock_download = download_patcher.start()
        self.addCleanup(download_patcher.stop)

    def _save_image(self, image_url):
        return self.client.post(
            reverse("account"),
            {"username": "test", "image": image_url, "image_crop": ""},
        )

    def test_saving_an_image_caches_it_through_the_task(self):
        """The save queues the download; once it lands the local copy is used."""
        self._save_image(FIRST_IMAGE)

        self.user.refresh_from_db()
        self.assertTrue(self.user.image_cached)
        self.assertTrue(
            default_storage.exists(profile_image_relpath(self.user.id, FIRST_IMAGE)),
        )

    def test_save_request_does_not_download_inline(self):
        """The request only queues work, so a slow image host can't stall it."""
        with patch("users.views.cache_user_profile_image.delay") as queued:
            self._save_image(FIRST_IMAGE)

        queued.assert_called_once_with(self.user.id, FIRST_IMAGE, "")
        self.mock_download.assert_not_called()
        self.user.refresh_from_db()
        self.assertFalse(self.user.image_cached)

    def test_replacing_an_image_deletes_the_old_file(self):
        """The previous image's cached file doesn't linger on disk."""
        self._save_image(FIRST_IMAGE)
        old_relpath = profile_image_relpath(self.user.id, FIRST_IMAGE)
        self.assertTrue(default_storage.exists(old_relpath))

        self._save_image(SECOND_IMAGE)

        self.assertFalse(default_storage.exists(old_relpath))
        self.assertTrue(
            default_storage.exists(profile_image_relpath(self.user.id, SECOND_IMAGE)),
        )

    def test_removing_an_image_deletes_its_file(self):
        """Clearing the profile image removes the cached copy too."""
        self._save_image(FIRST_IMAGE)

        self._save_image("")

        self.user.refresh_from_db()
        self.assertFalse(self.user.image_cached)
        self.assertFalse(
            default_storage.exists(profile_image_relpath(self.user.id, FIRST_IMAGE)),
        )

    def test_failed_download_leaves_the_raw_link(self):
        """When the fetch fails the profile keeps showing the pasted URL."""
        self.mock_download.return_value = None

        self._save_image(FIRST_IMAGE)

        self.user.refresh_from_db()
        self.assertFalse(self.user.image_cached)
        self.assertEqual(self.user.cached_image_url, FIRST_IMAGE)

    def test_stale_download_does_not_mark_a_newer_image_cached(self):
        """A download finishing after the user changed image again is ignored."""
        self.user.image = SECOND_IMAGE
        self.user.save(update_fields=["image"])

        result = cache_user_profile_image(self.user.id, FIRST_IMAGE, "")

        self.assertFalse(result)
        self.user.refresh_from_db()
        self.assertFalse(self.user.image_cached)

    def test_oversized_image_is_refused_not_raised(self):
        """An image over Pillow's pixel limit fails the cache step quietly."""
        with patch("app.tasks.Image.MAX_IMAGE_PIXELS", 10):
            self.assertFalse(cache_profile_image(self.user.id, FIRST_IMAGE))
