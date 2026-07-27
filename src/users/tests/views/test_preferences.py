from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from app.models import MediaTypes
from users.models import (
    HomeLayoutChoices,
    PlanningTermChoices,
    QuickWatchDateChoices,
    ThemeChoices,
)


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
        self.assertContains(response, "Home and navigation order")
        self.assertContains(response, 'name="home_media_order"')
        # Saved order leads the seeded Alpine array; tv is excluded (home uses season).
        home_types = response.context["home_media_types"]
        self.assertEqual(home_types[0], MediaTypes.GAME.value)
        self.assertEqual(home_types[1], MediaTypes.MOVIE.value)
        self.assertNotIn(MediaTypes.TV.value, home_types)

    def test_preferences_are_grouped_with_repeated_global_save_buttons(self):
        """Categories share one form and expose a convenient Save button each."""
        response = self.client.get(reverse("preferences"))

        self.assertContains(response, "Appearance")
        self.assertContains(response, "Tracking")
        self.assertContains(response, "Home &amp; Recommendations")
        self.assertContains(response, "Region &amp; Formats")
        self.assertContains(response, "Library &amp; Navigation")
        self.assertContains(response, "Any Save button applies all changes")
        self.assertEqual(response.content.count(b'name="planning_term"'), 1)
        self.assertGreaterEqual(response.content.count(b"Save"), 5)
        self.assertNotContains(response, "{#")
        self.assertNotContains(response, "{%")
        self.assertNotContains(response, "{{")

    def test_planning_term_defaults_to_wishlist_and_can_use_backlog(self):
        """Users can opt into Backlog without changing the stored status value."""
        self.assertEqual(self.user.planning_term, PlanningTermChoices.WISHLIST)

        self.client.post(
            reverse("preferences"),
            {"planning_term": PlanningTermChoices.BACKLOG},
        )

        self.user.refresh_from_db()
        self.assertEqual(self.user.planning_term, PlanningTermChoices.BACKLOG)

    def test_new_user_defaults_match_recommended_preferences(self):
        """New accounts receive the application's recommended preference set."""
        expected_defaults = {
            "theme": ThemeChoices.DARK,
            "home_layout": HomeLayoutChoices.GRID,
            "colored_grid_progress_buttons": False,
            "clickable_media_cards": True,
            "progress_bar": True,
            "hide_zero_rating": False,
            "planning_term": PlanningTermChoices.WISHLIST,
            "quick_watch_date": QuickWatchDateChoices.CURRENT_DATE,
            "auto_mark_previous_episodes": True,
            "obfuscate_unseen_episodes": True,
            "show_all_home_items": False,
            "show_continue_story": True,
            "hide_completed_recommendations": True,
        }

        for field_name, expected in expected_defaults.items():
            with self.subTest(field_name=field_name):
                self.assertEqual(getattr(self.user, field_name), expected)

    def test_valid_order_persists(self):
        """A valid submitted order is saved verbatim."""
        order = f"{MediaTypes.GAME.value},{MediaTypes.MOVIE.value}"
        self.client.post(reverse("preferences"), {"home_media_order": order})
        self.user.refresh_from_db()
        self.assertEqual(self.user.home_media_order, order)

    def test_compact_home_list_defaults_off_and_can_be_enabled(self):
        """Cards are the default and checking the switch enables the compact list."""
        response = self.client.get(reverse("preferences"))
        self.assertEqual(self.user.home_layout, HomeLayoutChoices.GRID)
        self.assertContains(response, 'name="compact_home_list"')

        self.client.post(reverse("preferences"), {"compact_home_list": "on"})
        self.user.refresh_from_db()
        self.assertEqual(self.user.home_layout, HomeLayoutChoices.LIST)

    def test_compact_home_list_preference_can_be_enabled(self):
        """Checking the switch saves the compact list choice."""
        self.user.home_layout = HomeLayoutChoices.GRID
        self.user.save(update_fields=["home_layout"])

        self.client.post(reverse("preferences"), {"compact_home_list": "on"})

        self.user.refresh_from_db()
        self.assertEqual(self.user.home_layout, HomeLayoutChoices.LIST)

    def test_colored_grid_progress_buttons_default_off_and_can_be_enabled(self):
        """Colored grid controls are disabled by default and can be enabled."""
        response = self.client.get(reverse("preferences"))
        self.assertFalse(self.user.colored_grid_progress_buttons)
        self.assertContains(response, 'name="colored_grid_progress_buttons"')

        self.client.post(
            reverse("preferences"),
            {"colored_grid_progress_buttons": "on"},
        )

        self.user.refresh_from_db()
        self.assertTrue(self.user.colored_grid_progress_buttons)

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

    def test_auto_mark_previous_episodes_defaults_on_and_can_be_disabled(self):
        """Earlier TV episodes are filled by default and can be disabled."""
        response = self.client.get(reverse("preferences"))

        self.assertTrue(self.user.auto_mark_previous_episodes)
        self.assertContains(response, 'name="auto_mark_previous_episodes"')

        self.client.post(reverse("preferences"), {})

        self.user.refresh_from_db()
        self.assertFalse(self.user.auto_mark_previous_episodes)

    def test_auto_mark_previous_episodes_can_be_disabled(self):
        """An unchecked tracking preference disables automatic filling."""
        self.user.auto_mark_previous_episodes = True
        self.user.save(update_fields=["auto_mark_previous_episodes"])

        self.client.post(reverse("preferences"), {})

        self.user.refresh_from_db()
        self.assertFalse(self.user.auto_mark_previous_episodes)

    def test_show_all_home_items_defaults_off_and_can_be_enabled(self):
        """Home rows use a load button by default and can load every item."""
        response = self.client.get(reverse("preferences"))

        self.assertFalse(self.user.show_all_home_items)
        self.assertContains(response, 'name="show_all_home_items"')
        self.client.post(reverse("preferences"), {"show_all_home_items": "on"})

        self.user.refresh_from_db()
        self.assertTrue(self.user.show_all_home_items)

    def test_show_all_home_items_can_be_enabled(self):
        """Checking the switch saves automatic loading for all home items."""
        self.user.show_all_home_items = False
        self.user.save(update_fields=["show_all_home_items"])

        self.client.post(reverse("preferences"), {"show_all_home_items": "on"})

        self.user.refresh_from_db()
        self.assertTrue(self.user.show_all_home_items)

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
