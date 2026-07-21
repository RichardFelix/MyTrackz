from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from app.models import MediaTypes
from users.models import HomeLayoutChoices


class PreferencesHomeOrderTests(TestCase):
    """Test saving the home screen row order on the preferences page."""

    def setUp(self):
        """Create a user and log in."""
        regions_patcher = patch(
            "users.views.tmdb.watch_provider_regions",
            return_value=[("UNSET", "Disabled")],
        )
        regions_patcher.start()
        self.addCleanup(regions_patcher.stop)
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    def test_get_renders_order_section(self):
        """The preferences page renders the reorder UI seeded with saved order."""
        self.user.home_media_order = f"{MediaTypes.GAME.value},{MediaTypes.MOVIE.value}"
        self.user.save()
        response = self.client.get(reverse("preferences"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Home Screen Row Order")
        self.assertContains(response, 'name="home_media_order"')
        # Saved order leads the seeded Alpine array; tv is excluded (home uses season).
        home_types = response.context["home_media_types"]
        self.assertEqual(home_types[0], MediaTypes.GAME.value)
        self.assertEqual(home_types[1], MediaTypes.MOVIE.value)
        self.assertNotIn(MediaTypes.TV.value, home_types)

    def test_valid_order_persists(self):
        """A valid submitted order is saved verbatim."""
        order = f"{MediaTypes.GAME.value},{MediaTypes.MOVIE.value}"
        self.client.post(reverse("preferences"), {"home_media_order": order})
        self.user.refresh_from_db()
        self.assertEqual(self.user.home_media_order, order)

    def test_compact_home_list_preference_defaults_on_and_can_be_disabled(self):
        """The compact list is the default and an unchecked switch restores cards."""
        response = self.client.get(reverse("preferences"))
        self.assertEqual(self.user.home_layout, HomeLayoutChoices.LIST)
        self.assertContains(response, 'name="compact_home_list"')

        self.client.post(reverse("preferences"), {})
        self.user.refresh_from_db()
        self.assertEqual(self.user.home_layout, HomeLayoutChoices.GRID)

    def test_compact_home_list_preference_can_be_enabled(self):
        """Checking the switch saves the compact list choice."""
        self.user.home_layout = HomeLayoutChoices.GRID
        self.user.save(update_fields=["home_layout"])

        self.client.post(reverse("preferences"), {"compact_home_list": "on"})

        self.user.refresh_from_db()
        self.assertEqual(self.user.home_layout, HomeLayoutChoices.LIST)

    def test_colored_grid_progress_buttons_default_on_and_can_be_disabled(self):
        """The colored grid controls are enabled by default and can be disabled."""
        response = self.client.get(reverse("preferences"))
        self.assertTrue(self.user.colored_grid_progress_buttons)
        self.assertContains(response, 'name="colored_grid_progress_buttons"')

        self.client.post(reverse("preferences"), {})

        self.user.refresh_from_db()
        self.assertFalse(self.user.colored_grid_progress_buttons)

    def test_colored_grid_progress_buttons_can_be_enabled(self):
        """Checking the switch saves the colored grid control preference."""
        self.user.colored_grid_progress_buttons = False
        self.user.save(update_fields=["colored_grid_progress_buttons"])

        self.client.post(
            reverse("preferences"),
            {"colored_grid_progress_buttons": "on"},
        )

        self.user.refresh_from_db()
        self.assertTrue(self.user.colored_grid_progress_buttons)

    def test_invalid_and_duplicate_slugs_stripped(self):
        """Unknown/episode/duplicate slugs are removed, order preserved."""
        submitted = (
            f"bogus,{MediaTypes.EPISODE.value},{MediaTypes.GAME.value},"
            f"{MediaTypes.GAME.value},{MediaTypes.MOVIE.value}"
        )
        self.client.post(reverse("preferences"), {"home_media_order": submitted})
        self.user.refresh_from_db()
        self.assertEqual(
            self.user.home_media_order,
            f"{MediaTypes.GAME.value},{MediaTypes.MOVIE.value}",
        )

    def test_missing_field_clears_order(self):
        """Posting without the field results in an empty saved order."""
        self.user.home_media_order = MediaTypes.GAME.value
        self.user.save()
        self.client.post(reverse("preferences"), {})
        self.user.refresh_from_db()
        self.assertEqual(self.user.home_media_order, "")

    def test_demo_user_cannot_change_order(self):
        """Demo accounts are blocked from updating preferences."""
        self.user.is_demo = True
        self.user.save()
        self.client.post(
            reverse("preferences"),
            {"home_media_order": MediaTypes.GAME.value},
        )
        self.user.refresh_from_db()
        self.assertEqual(self.user.home_media_order, "")
