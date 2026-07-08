from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from app.models import MediaTypes


class PreferencesHomeOrderTests(TestCase):
    """Test saving the home screen row order on the preferences page."""

    def setUp(self):
        """Create a user and log in."""
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
