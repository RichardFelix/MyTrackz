from itertools import pairwise
from unittest.mock import patch

from allauth.account.signals import user_signed_up
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from app import provider_keys
from app.models import ProviderKey
from users import onboarding

User = get_user_model()


def step_url(step, origin=""):
    """Return a walkthrough step's URL."""
    url = reverse("onboarding_step", args=[step])
    return f"{url}?from={origin}" if origin else url


class OnboardingTestCase(TestCase):
    """An admin (the first account) and an ordinary second account."""

    def setUp(self):
        """Create both accounts, flagged as new, and log in as the admin."""
        cache.clear()
        self.admin_credentials = {"username": "owner", "password": "12345"}
        self.admin = User.objects.create_user(
            **self.admin_credentials,
            needs_onboarding=True,
        )
        self.other_credentials = {"username": "second", "password": "12345"}
        self.other = User.objects.create_user(
            **self.other_credentials,
            needs_onboarding=True,
        )
        self.client.login(**self.admin_credentials)


class EntryTests(OnboardingTestCase):
    """Who is sent to the walkthrough, and how they leave it."""

    def test_signup_flags_account(self):
        """A fresh signup is flagged; accounts made otherwise are not."""
        user = User.objects.create_user(username="fresh", password="12345")  # noqa: S106
        self.assertFalse(user.needs_onboarding)

        user_signed_up.send(sender=User, request=None, user=user)

        user.refresh_from_db()
        self.assertTrue(user.needs_onboarding)

    def test_home_redirects_new_account(self):
        """Home sends a flagged account to the welcome step."""
        response = self.client.get(reverse("home"))
        self.assertRedirects(
            response,
            reverse("onboarding"),
            fetch_redirect_response=False,
        )

        response = self.client.get(reverse("onboarding"))
        self.assertRedirects(response, step_url("welcome"))

    def test_other_pages_stay_reachable(self):
        """Only Home redirects; a deep link still opens."""
        response = self.client.get(reverse("about"))
        self.assertEqual(response.status_code, 200)

    def test_welcome_offers_skip(self):
        """The first step explains setup and has a skip button."""
        response = self.client.get(step_url("welcome"))

        self.assertContains(response, "Skip walkthrough")
        self.assertContains(response, "API keys")
        self.assertContains(response, "Step 1 of 9")

    def test_skip_clears_flag(self):
        """Skipping lands on Home and is not asked again."""
        response = self.client.post(reverse("onboarding_skip"))

        self.assertRedirects(response, reverse("home"))
        self.admin.refresh_from_db()
        self.assertFalse(self.admin.needs_onboarding)

    def test_finish_clears_flag_and_points_at_settings(self):
        """Finishing drops the user on Home with the change-it-later note."""
        response = self.client.post(step_url("summary"))

        self.assertRedirects(response, reverse("home"))
        self.admin.refresh_from_db()
        self.assertFalse(self.admin.needs_onboarding)
        notes = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertTrue(any("later in Settings" in note for note in notes))

    def test_demo_account_kept_out(self):
        """Demo accounts can't change settings, so they get no walkthrough."""
        User.objects.create_user(username="demo", password="12345", is_demo=True)  # noqa: S106
        self.client.login(username="demo", password="12345")  # noqa: S106

        response = self.client.get(step_url("welcome"))
        self.assertRedirects(response, reverse("home"))

    def test_steps_walk_in_order(self):
        """Each step's Continue leads to the next one, ending at the summary."""
        steps = onboarding.get_steps(self.admin)
        for current, following in pairwise(steps):
            response = self.client.post(step_url(current))
            self.assertRedirects(
                response,
                step_url(following),
                fetch_redirect_response=False,
            )


class PreferenceStepTests(OnboardingTestCase):
    """Each preference step saves one setting."""

    def test_every_step_renders_with_current_answer(self):
        """All steps load, with the stored value preselected."""
        for step in onboarding.get_steps(self.admin):
            with self.subTest(step=step):
                response = self.client.get(step_url(step))
                self.assertEqual(response.status_code, 200)

        response = self.client.get(step_url("theme"))
        self.assertContains(response, 'value="dark"\n')
        self.assertRegex(
            response.content.decode(),
            r'value="dark"\s+class="[^"]*"\s+onchange="[^"]*"\s+checked',
        )

    def test_choice_saved(self):
        """A radio answer is stored on the user."""
        self.client.post(step_url("theme"), {"theme": "grey"})
        self.client.post(step_url("dates"), {"date_format": "d/m/Y"})

        self.admin.refresh_from_db()
        self.assertEqual(self.admin.theme, "grey")
        self.assertEqual(self.admin.date_format, "d/m/Y")
        self.assertEqual(self.admin.time_format, "H:i")

    def test_unknown_choice_ignored(self):
        """A value that is not one of the options changes nothing."""
        self.client.post(step_url("theme"), {"theme": "neon"})

        self.admin.refresh_from_db()
        self.assertEqual(self.admin.theme, "dark")

    def test_boolean_saved(self):
        """The on/off answer maps onto the boolean field."""
        self.client.post(step_url("aired-only"), {"home_aired_only": "on"})
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.home_aired_only)

        self.client.post(step_url("aired-only"), {"home_aired_only": "off"})
        self.admin.refresh_from_db()
        self.assertFalse(self.admin.home_aired_only)

    def test_media_types_saved_and_seasons_follow_tv(self):
        """Unchecked types are disabled; seasons go with TV."""
        self.client.post(
            step_url("media-types"),
            {"media_types": ["movie", "game"]},
        )

        self.admin.refresh_from_db()
        self.assertTrue(self.admin.movie_enabled)
        self.assertTrue(self.admin.game_enabled)
        self.assertFalse(self.admin.anime_enabled)
        self.assertFalse(self.admin.tv_enabled)
        self.assertFalse(self.admin.season_enabled)

    def test_edit_from_summary_returns_to_summary(self):
        """Editing a section from the review screen comes back to it."""
        response = self.client.get(step_url("summary"))
        self.assertContains(response, step_url("theme", "summary"))

        response = self.client.post(step_url("theme", "summary"), {"theme": "grey"})
        self.assertRedirects(response, step_url("summary"))

        response = self.client.get(step_url("summary"))
        self.assertContains(response, "Grey")

    def test_unknown_step_is_404(self):
        """A made-up step does not exist."""
        response = self.client.get(step_url("nope"))
        self.assertEqual(response.status_code, 404)


class ProviderStepTests(OnboardingTestCase):
    """The API keys step is for instance admins and never shows a key."""

    def test_non_admin_has_no_keys_step(self):
        """Ordinary accounts skip the step and can't open or test it."""
        self.client.login(**self.other_credentials)

        self.assertNotIn("providers", onboarding.get_steps(self.other))
        self.assertEqual(self.client.get(step_url("providers")).status_code, 404)
        response = self.client.post(
            reverse("onboarding_test_provider", args=["tmdb"]),
            {"TMDB_API": "typed"},
        )
        self.assertEqual(response.status_code, 403)
        response = self.client.post(step_url("providers"), {"TMDB_API": "typed"})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(ProviderKey.objects.exists())

    def test_lists_providers_marked_required_or_optional(self):
        """Every provider is shown with what it is for."""
        response = self.client.get(step_url("providers"))

        for provider in provider_keys.PROVIDERS:
            self.assertContains(response, provider.used_for)
        self.assertContains(response, "Required")
        self.assertContains(response, "Optional")
        self.assertContains(response, "Test connection")

    def test_save_stores_key_without_echoing_it(self):
        """A saved key takes effect and is not in the page afterwards."""
        response = self.client.post(
            step_url("providers"),
            {"TMDB_API": "my-own-tmdb-key", "MAL_API": ""},
        )
        self.assertRedirects(response, step_url("media-types"))
        self.assertEqual(provider_keys.get("TMDB_API"), "my-own-tmdb-key")
        self.assertFalse(ProviderKey.objects.filter(name="MAL_API").exists())

        response = self.client.get(step_url("providers"))
        self.assertNotContains(response, "my-own-tmdb-key")
        self.assertContains(response, "ends in -key")
        self.assertContains(response, "Remove saved key")

    def test_remove_saved_key(self):
        """Ticking Remove goes back to the environment value."""
        tmdb = provider_keys.PROVIDERS_BY_SLUG["tmdb"]
        provider_keys.save(tmdb, {"TMDB_API": "my-own-tmdb-key"})

        self.client.post(step_url("providers"), {"clear": ["tmdb"]})

        self.assertFalse(ProviderKey.objects.exists())

    def test_test_button_reports_result(self):
        """The test endpoint answers with the result and saves nothing."""
        with patch(
            "users.onboarding.provider_keys.test_connection",
            return_value=(True, "Connected to TMDB."),
        ) as test_connection:
            response = self.client.post(
                reverse("onboarding_test_provider", args=["tmdb"]),
                {"TMDB_API": "typed"},
            )

        self.assertContains(response, "Connected to TMDB.")
        self.assertEqual(test_connection.call_args.args[1]["TMDB_API"], "typed")
        self.assertFalse(ProviderKey.objects.exists())

    def test_opened_from_admin_returns_to_admin(self):
        """Managing keys from the Admin page goes back there on save."""
        response = self.client.post(step_url("providers", "admin"), {})
        self.assertRedirects(
            response,
            reverse("instance_admin"),
            fetch_redirect_response=False,
        )
