from unittest.mock import patch

from django.contrib import auth
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class Profile(TestCase):
    """Test profile page."""

    def setUp(self):
        """Create user for the tests."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    def test_change_username(self):
        """Test changing username."""
        self.assertEqual(auth.get_user(self.client).username, "test")
        self.client.post(
            reverse("account"),
            {
                "username": "new_test",
            },
        )
        self.assertEqual(auth.get_user(self.client).username, "new_test")

    def test_change_password(self):
        """Test changing password."""
        self.assertEqual(auth.get_user(self.client).check_password("12345"), True)
        self.client.post(
            reverse("account"),
            {
                "old_password": "12345",
                "new_password1": "*FNoZN64",
                "new_password2": "*FNoZN64",
            },
        )
        self.assertEqual(auth.get_user(self.client).check_password("*FNoZN64"), True)

    def test_invalid_password_change(self):
        """Test password change with incorrect old password."""
        response = self.client.post(
            reverse("account"),
            {
                "old_password": "wrongpass",
                "new_password1": "newpass123",
                "new_password2": "newpass123",
            },
        )
        self.assertTrue(auth.get_user(self.client).check_password("12345"))
        self.assertContains(response, "Your old password was entered incorrectly")

    def test_invalid_crop_does_not_save_account(self):
        """Reject malformed crop values before saving or fetching an image."""
        for crop in ["0,0,nan", "inf,0,0.5", "1,0,0.5", "0,1,0.5", "0,0,0", "bad"]:
            with (
                self.subTest(crop=crop),
                patch("users.views.cache_profile_image") as fetch,
            ):
                response = self.client.post(
                    reverse("account"),
                    {
                        "username": "changed",
                        "image": "https://images.example/avatar.png",
                        "image_crop": crop,
                    },
                )
                self.assertEqual(response.status_code, 200)
                self.assertIn("image_crop", response.context["user_form"].errors)
                self.assertContains(response, "Enter a valid image crop.")
                self.user.refresh_from_db()
                self.assertEqual(self.user.username, "test")
                self.assertEqual(self.user.image_crop, "")
                fetch.assert_not_called()
