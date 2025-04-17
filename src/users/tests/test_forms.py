from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from users.forms import NotificationSettingsForm


class NotificationSettingsFormTests(TestCase):
    """Tests for the NotificationSettingsForm."""

    def setUp(self):
        """Set up test data."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.valid_discord_url = "discord://webhook_id/webhook_token"
        self.valid_telegram_url = "tgram://bot_token/chat_id"
        self.invalid_url = "invalid://not_a_real_url"

    def test_form_fields(self):
        """Test that the form has the correct fields."""
        form = NotificationSettingsForm()
        self.assertEqual(
            list(form.fields.keys()),
            [
                "notification_urls",
                "daily_digest_enabled",
                "release_notifications_enabled",
            ],
        )

    def test_form_widget(self):
        """Test that the form uses the correct widget."""
        form = NotificationSettingsForm()
        self.assertEqual(form.fields["notification_urls"].widget.attrs["rows"], 5)
        self.assertEqual(form.fields["notification_urls"].widget.attrs["wrap"], "off")
        self.assertIn("placeholder", form.fields["notification_urls"].widget.attrs)

    @patch("apprise.Apprise.add")
    def test_valid_single_url(self, mock_add):
        """Test form with a single valid URL."""
        mock_add.return_value = True

        form_data = {
            "notification_urls": self.valid_discord_url,
            "daily_digest_enabled": True,
            "release_notifications_enabled": True,
        }
        form = NotificationSettingsForm(data=form_data, instance=self.user)

        self.assertTrue(form.is_valid())
        mock_add.assert_called_once_with(self.valid_discord_url)

    @patch("apprise.Apprise.add")
    def test_valid_multiple_urls(self, mock_add):
        """Test form with multiple valid URLs."""
        mock_add.return_value = True

        form_data = {
            "notification_urls": f"{self.valid_discord_url}\n{self.valid_telegram_url}",
            "daily_digest_enabled": True,
            "release_notifications_enabled": True,
        }
        form = NotificationSettingsForm(data=form_data, instance=self.user)

        self.assertTrue(form.is_valid())
        self.assertEqual(mock_add.call_count, 2)
        mock_add.assert_any_call(self.valid_discord_url)
        mock_add.assert_any_call(self.valid_telegram_url)

    @patch("apprise.Apprise.add")
    def test_empty_urls(self, mock_add):
        """Test form with empty URLs."""
        form_data = {
            "notification_urls": "",
            "daily_digest_enabled": True,
            "release_notifications_enabled": True,
        }
        form = NotificationSettingsForm(data=form_data, instance=self.user)

        self.assertTrue(form.is_valid())
        mock_add.assert_not_called()

    @patch("apprise.Apprise.add")
    def test_whitespace_only_urls(self, mock_add):
        """Test form with whitespace-only URLs."""
        form_data = {
            "notification_urls": "   \n  \t  ",
            "daily_digest_enabled": True,
            "release_notifications_enabled": True,
        }
        form = NotificationSettingsForm(data=form_data, instance=self.user)

        self.assertTrue(form.is_valid())
        mock_add.assert_not_called()

    @patch("apprise.Apprise.add")
    def test_invalid_url(self, mock_add):
        """Test form with an invalid URL."""
        mock_add.return_value = False

        form_data = {
            "notification_urls": self.invalid_url,
            "daily_digest_enabled": True,
            "release_notifications_enabled": True,
        }
        form = NotificationSettingsForm(data=form_data, instance=self.user)

        self.assertFalse(form.is_valid())
        self.assertIn("notification_urls", form.errors)
        self.assertIn(
            f"'{self.invalid_url}' is not a valid Apprise URL.",
            form.errors["notification_urls"],
        )
        mock_add.assert_called_once_with(self.invalid_url)

    @patch("apprise.Apprise.add")
    def test_mixed_valid_invalid_urls(self, mock_add):
        """Test form with a mix of valid and invalid URLs."""

        # Configure mock to return True for valid URL and False for invalid URL
        def side_effect(url):
            return url != self.invalid_url

        mock_add.side_effect = side_effect

        form_data = {
            "notification_urls": f"{self.valid_discord_url}\n{self.invalid_url}",
            "daily_digest_enabled": True,
            "release_notifications_enabled": True,
        }
        form = NotificationSettingsForm(data=form_data, instance=self.user)

        self.assertFalse(form.is_valid())
        self.assertIn("notification_urls", form.errors)
        self.assertIn(
            f"'{self.invalid_url}' is not a valid Apprise URL.",
            form.errors["notification_urls"],
        )
        self.assertEqual(mock_add.call_count, 2)

    @patch("apprise.Apprise.add")
    def test_urls_with_extra_whitespace(self, mock_add):
        """Test form with URLs that have extra whitespace."""
        mock_add.return_value = True

        form_data = {
            "notification_urls": (
                f"  {self.valid_discord_url}  \n\t{self.valid_telegram_url}\n"
            ),
            "daily_digest_enabled": True,
            "release_notifications_enabled": True,
        }
        form = NotificationSettingsForm(data=form_data, instance=self.user)

        self.assertTrue(form.is_valid())
        self.assertEqual(mock_add.call_count, 2)
        mock_add.assert_any_call(self.valid_discord_url)
        mock_add.assert_any_call(self.valid_telegram_url)

    @patch("apprise.Apprise.add")
    def test_form_saves_correctly(self, mock_add):
        """Test that the form saves the notification URLs correctly."""
        mock_add.return_value = True

        form_data = {
            "notification_urls": f"{self.valid_discord_url}\n{self.valid_telegram_url}",
            "daily_digest_enabled": True,
            "release_notifications_enabled": True,
        }
        form = NotificationSettingsForm(data=form_data, instance=self.user)

        self.assertTrue(form.is_valid())
        form.save()

        self.user.refresh_from_db()
        self.assertEqual(
            self.user.notification_urls,
            f"{self.valid_discord_url}\n{self.valid_telegram_url}",
        )
        self.assertTrue(self.user.daily_digest_enabled)
        self.assertTrue(self.user.release_notifications_enabled)

    @patch("apprise.Apprise.add")
    def test_empty_lines_are_ignored(self, mock_add):
        """Test that empty lines in the input are ignored."""
        mock_add.return_value = True

        form_data = {
            "notification_urls": (
                f"{self.valid_discord_url}\n\n{self.valid_telegram_url}\n\n"
            ),
            "daily_digest_enabled": True,
            "release_notifications_enabled": True,
        }
        form = NotificationSettingsForm(data=form_data, instance=self.user)

        self.assertTrue(form.is_valid())
        self.assertEqual(mock_add.call_count, 2)
        mock_add.assert_any_call(self.valid_discord_url)
        mock_add.assert_any_call(self.valid_telegram_url)

    def test_notification_preferences_save_correctly(self):
        """Test that notification preferences are saved correctly."""
        form_data = {
            "notification_urls": self.valid_discord_url,
            "daily_digest_enabled": False,
            "release_notifications_enabled": True,
        }

        with patch("apprise.Apprise.add", return_value=True):
            form = NotificationSettingsForm(data=form_data, instance=self.user)
            self.assertTrue(form.is_valid())
            form.save()

        self.user.refresh_from_db()
        self.assertFalse(self.user.daily_digest_enabled)
        self.assertTrue(self.user.release_notifications_enabled)
