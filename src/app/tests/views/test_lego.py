"""LEGO tracking integration across forms, views, history and preferences."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from app.forms import LegoForm
from app.history_processor import format_description
from app.models import BasicMedia, Item, Lego, Status
from app.statistics import get_user_media
from events.calendar.main import process_items
from events.calendar.selectors import get_items_to_process

METADATA = {
    "media_id": "75192-1",
    "source": "rebrickable",
    "source_url": "https://rebrickable.com/sets/75192-1/",
    "media_type": "lego",
    "title": "Millennium Falcon",
    "image": "https://example.com/falcon.jpg",
    "max_progress": None,
    "synopsis": "No synopsis available.",
    "genres": ["Star Wars"],
    "score": None,
    "score_count": None,
    "details": {"set_number": "75192-1", "year": 2017, "pieces": 7541},
    "related": {},
}


class LegoTrackingTests(TestCase):
    """Exercise the new category using isolated data and mocked providers."""

    def setUp(self):
        """Create a user and prevent provider/image tasks from making HTTP calls."""
        self.user = get_user_model().objects.create_user(username="builder")
        self.client.force_login(self.user)
        for target, value in [
            ("app.providers.services.get_media_metadata", METADATA),
            ("app.models.Item.fetch_releases", None),
            ("app.tasks.cache_item_image.delay", None),
            ("users.views.tmdb.watch_provider_regions", [("UNSET", "Disabled")]),
        ]:
            patcher = patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.item = Item.objects.create(
            media_id="75192-1",
            source="rebrickable",
            media_type="lego",
            title="Millennium Falcon",
            image=METADATA["image"],
        )

    def create_build(self, **kwargs):
        """Create a tracked build without using real providers."""
        return Lego.objects.create(
            user=self.user,
            item=self.item,
            status=Status.IN_PROGRESS,
            **kwargs,
        )

    def test_time_progress_completion_and_history(self):
        """Time changes persist history; completion preserves recorded build time."""
        build = self.create_build(progress=15)
        build.increase_progress()
        self.assertEqual(build.progress, 45)
        self.assertEqual(build.formatted_progress, "45min")
        build.decrease_progress()
        build.decrease_progress()
        self.assertEqual(build.progress, 0)
        build.progress = 95
        build.status = Status.COMPLETED
        build.save()
        build.refresh_from_db()
        self.assertEqual(build.progress, 95)
        self.assertEqual(build.history.first().progress, 95)
        self.assertEqual(build.formatted_progress, "1h 35min")
        self.assertIn("historicallego", BasicMedia.objects.get_historical_models())
        self.assertEqual(
            format_description("progress", 0, 95, "lego"),
            "Added 1h 35min of build time",
        )

    def test_new_form_defaults_to_wishlist_and_parses_duration(self):
        """Build time uses the existing duration parser and standard status flow."""
        self.assertEqual(LegoForm(user=self.user).initial["status"], Status.PLANNING)
        form = LegoForm(
            data={
                "media_type": "lego",
                "source": "rebrickable",
                "media_id": "75192-1",
                "status": Status.IN_PROGRESS,
                "progress": "1:30",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["progress"], 90)

    def test_save_edit_and_delete_build(self):
        """The standard tracking routes support LEGO and protect other users."""
        payload = {
            "media_type": "lego",
            "source": "rebrickable",
            "media_id": "75192-1",
            "status": Status.IN_PROGRESS,
            "progress": "2:15",
            "return_url": "/",
        }
        self.assertEqual(
            self.client.post(reverse("media_save"), payload).status_code, 302
        )
        build = Lego.objects.get(user=self.user)
        self.assertEqual(build.progress, 135)
        response = self.client.get(
            reverse("track_modal", args=["rebrickable", "lego", "75192-1"]),
            {"instance_id": build.pk, "return_url": "/"},
        )
        self.assertContains(response, 'value="2h 15min"')
        self.assertContains(response, "Build time (hours:minutes)")
        other = get_user_model().objects.create_user(username="other-builder")
        self.client.force_login(other)
        self.assertEqual(
            self.client.post(
                reverse("progress_edit", args=["lego", build.pk]),
                {"operation": "increase"},
            ).status_code,
            404,
        )
        self.client.force_login(self.user)
        self.assertEqual(
            self.client.post(
                reverse("media_delete"),
                {
                    "media_type": "lego",
                    "instance_id": build.pk,
                    "return_url": "/",
                },
            ).status_code,
            302,
        )
        self.assertFalse(Lego.objects.exists())

    def test_library_home_details_and_preferences_render(self):
        """Navigation and templates expose LEGO without leaking template syntax."""
        build = self.create_build(progress=90)
        paths = [
            reverse("medialist", args=[self.user.username, "lego"]),
            reverse("media_details", args=["rebrickable", "lego", "75192-1", "falcon"]),
            reverse("home"),
            reverse("preferences"),
        ]
        for path in paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Lego")
                for token in ["{%", "{{", "{#"]:
                    self.assertNotContains(response, token)
        response = self.client.post(
            reverse("progress_edit", args=["lego", build.pk]),
            {"operation": "increase", "home_section": "1"},
        )
        self.assertContains(response, "2h 00min")
        self.assertNotContains(response, "watched")
        counts = get_user_media(self.user, None, None)[1]
        self.assertEqual(counts["lego"], 1)

    def test_lego_has_no_fabricated_release_or_followup_feed(self):
        """Catalog years do not become calendar dates or invented recommendations."""
        self.create_build()
        self.assertNotIn(self.item, get_items_to_process(self.user))
        self.assertEqual(process_items([self.item]), [])

    @patch("app.providers.services.search")
    def test_search_category_routes_to_rebrickable(self, search):
        """Search results retain full variant IDs and remember the category."""
        search.return_value = {
            "page": 1,
            "total_pages": 1,
            "total_results": 1,
            "results": [METADATA],
        }
        response = self.client.get(
            reverse("search"), {"media_type": "lego", "q": "75192"}
        )
        self.assertContains(response, "Millennium Falcon")
        self.user.refresh_from_db()
        self.assertEqual(self.user.last_search_type, "lego")
        search.assert_called_once_with("lego", "75192", 1, "rebrickable")
