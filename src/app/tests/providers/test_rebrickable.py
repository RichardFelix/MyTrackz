"""Deterministic tests of the Rebrickable catalog adapter."""

from unittest.mock import patch

import requests
from django.conf import settings
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from app.providers import rebrickable, services

SET = {
    "set_num": "75192-1",
    "name": "Millennium Falcon",
    "year": 2017,
    "num_parts": 7541,
    "theme_id": 158,
    "set_img_url": "https://example.com/falcon.jpg",
}


@override_settings(REBRICKABLE="test-key")
class RebrickableTests(SimpleTestCase):
    """Search, caching, metadata and errors do not require real API credentials."""

    def setUp(self):
        """Isolate provider caches."""
        cache.clear()

    @patch("app.providers.services.api_request")
    def test_search_preserves_set_variant_and_paginates(self, request):
        """Server-side pagination preserves set identifiers and hides the key."""
        request.return_value = {"count": 41, "results": [SET]}
        result = services.search("lego", "75192", 2, "rebrickable")
        self.assertEqual(result["total_pages"], 3)
        self.assertEqual(result["page"], 2)
        self.assertEqual(result["results"][0]["media_id"], "75192-1")
        self.assertEqual(result["results"][0]["media_type"], "lego")
        self.assertEqual(result["results"][0]["source"], "rebrickable")
        kwargs = request.call_args.kwargs
        self.assertEqual(kwargs["headers"], {"Authorization": "key test-key"})
        self.assertNotIn("min_parts", kwargs["params"])
        self.assertNotIn("key", kwargs["params"])
        self.assertEqual(services.search("lego", "75192", 2), result)
        request.assert_called_once()

    @patch("app.providers.services.api_request")
    def test_search_includes_sets_without_populated_inventories(self, request):
        """Upcoming sets with zero catalog parts remain searchable by name or ID."""
        joker = {
            **SET,
            "set_num": "67012-1",
            "name": "The Joker Wall Art Comic Cover",
            "num_parts": 0,
            "year": 2027,
        }
        request.return_value = {"count": 1, "results": [joker]}
        for query in ["The Joker", "67012"]:
            with self.subTest(query=query):
                # Old filtered search caches must not hide newly included sets.
                cache.set(f"search_rebrickable_lego_{query}_1", {"results": []})
                result = rebrickable.search(query, 1)
                self.assertEqual(result["results"][0]["media_id"], "67012-1")
                self.assertNotIn("min_parts", request.call_args.kwargs["params"])
                self.assertEqual(rebrickable.search(query, 1), result)
        self.assertEqual(request.call_count, 2)

    @patch("app.providers.services.api_request")
    def test_metadata_and_shared_theme_cache(self, request):
        """Piece count is descriptive; completing a build must not overwrite time."""
        request.side_effect = [
            SET,
            {"name": "Star Wars"},
            {**SET, "set_num": "75192-2"},
        ]
        result = services.get_media_metadata("lego", "75192-1", "rebrickable")
        self.assertEqual(result["details"]["pieces"], 7541)
        self.assertEqual(result["details"]["year"], 2017)
        self.assertEqual(result["details"]["set_number"], "75192-1")
        self.assertEqual(result["genres"], ["Star Wars"])
        self.assertIsNone(result["max_progress"])
        self.assertEqual(result["related"], {})
        self.assertEqual(rebrickable.lego("75192-1"), result)
        rebrickable.lego("75192-2")
        self.assertEqual(request.call_count, 3)

    @patch("app.providers.services.api_request")
    def test_missing_image_and_optional_theme_failure(self, request):
        """A missing image or failed theme lookup still leaves a usable set."""
        request.side_effect = [{**SET, "set_img_url": None}, requests.ConnectionError()]
        result = rebrickable.lego("75192-1")
        self.assertEqual(result["image"], settings.IMG_NONE)
        self.assertEqual(result["genres"], [])

    @patch("app.providers.services.api_request")
    def test_errors_are_provider_errors(self, request):
        """Connection and authorization errors use the existing provider UI."""
        response = requests.Response()
        response.status_code = 401
        for error in [
            requests.ConnectionError(),
            requests.HTTPError(response=response),
        ]:
            with self.subTest(error=type(error).__name__):
                request.side_effect = error
                with self.assertRaises(services.ProviderAPIError):
                    rebrickable.search("bad", 1)

    @override_settings(REBRICKABLE="")
    @patch("app.providers.services.api_request")
    def test_missing_key_is_actionable(self, request):
        """Missing configuration does not issue an unauthenticated HTTP request."""
        with self.assertRaisesMessage(
            services.ProviderAPIError, "Configure REBRICKABLE"
        ):
            rebrickable.search("Falcon", 1)
        request.assert_not_called()

    @patch("app.providers.services.api_request")
    def test_empty_search(self, request):
        """No matches yields the normal empty result shape."""
        request.return_value = {"count": 0, "results": []}
        self.assertEqual(rebrickable.search("unknown", 1)["results"], [])
