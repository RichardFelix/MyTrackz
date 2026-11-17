from unittest.mock import Mock, patch

import requests
from django.conf import settings
from django.core.cache import cache
from django.test import TestCase

from app.models import MediaTypes, Sources
from app.providers import comicvine, services


def http_error(status_code, payload=None):
    """Return the error the request helper raises for an HTTP status.

    Without a payload the response body is not valid JSON.
    """
    response = Mock(status_code=status_code, text="error")
    if payload is None:
        response.json.side_effect = requests.exceptions.JSONDecodeError("bad", "", 0)
    else:
        response.json.return_value = payload
    return requests.exceptions.HTTPError(response=response)


def volume(**overrides):
    """Return a Comic Vine volume as the API sends it."""
    data = {
        "name": "Saga",
        "site_detail_url": "https://comicvine.gamespot.com/saga/4050-46568/",
        "image": {"medium_url": "https://example.com/saga.jpg"},
        "description": "<p>An epic   <b>space opera</b>.</p>",
        "concepts": [{"name": f"Concept {number}"} for number in range(7)],
        "start_year": "2012",
        "count_of_issues": 66,
        "publisher": {"id": 513, "name": "Image"},
        "people": [{"name": f"Person {number}"} for number in range(7)],
        "last_issue": {"id": 987, "name": "Chapter 66", "issue_number": "66"},
        "date_last_updated": "2024-05-06 07:08:09",
    }
    data.update(overrides)
    return data


class ComicVineTests(TestCase):
    """Test the Comic Vine provider without calling the real API."""

    def setUp(self):
        """Start every test with an empty cache."""
        cache.clear()

    @patch("app.providers.services.api_request")
    def test_search_formats_and_caches_results(self, mock_api_request):
        """Test search results are normalized and the second call is cached."""
        mock_api_request.return_value = {
            "number_of_total_results": 30,
            "results": [
                {
                    "id": 46568,
                    "name": "Saga",
                    "image": {"medium_url": "https://example.com/saga.jpg"},
                },
                {"id": 2, "name": "No Cover"},
            ],
        }

        data = comicvine.search("saga", 1)
        cached = comicvine.search("saga", 1)

        self.assertEqual(data, cached)
        mock_api_request.assert_called_once()
        self.assertEqual(data["page"], 1)
        self.assertEqual(data["total_results"], 30)
        self.assertEqual(
            data["results"],
            [
                {
                    "media_id": "46568",
                    "source": Sources.COMICVINE.value,
                    "media_type": MediaTypes.COMIC.value,
                    "title": "Saga",
                    "image": "https://example.com/saga.jpg",
                },
                {
                    "media_id": "2",
                    "source": Sources.COMICVINE.value,
                    "media_type": MediaTypes.COMIC.value,
                    "title": "No Cover",
                    "image": settings.IMG_NONE,
                },
            ],
        )
        params = mock_api_request.call_args.kwargs["params"]
        self.assertEqual(params["query"], "saga")
        self.assertEqual(params["resources"], "volume")

    @patch("app.providers.services.api_request")
    def test_invalid_api_key_is_explained(self, mock_api_request):
        """Test a rejected API key surfaces Comic Vine's own explanation."""
        mock_api_request.side_effect = http_error(401, {"error": "Invalid API Key"})

        with self.assertRaises(services.ProviderAPIError) as context:
            comicvine.search("saga", 1)

        self.assertEqual(context.exception.status_code, 401)
        self.assertIn("Invalid API Key", str(context.exception))

    @patch("app.providers.services.api_request")
    def test_other_errors_become_provider_errors(self, mock_api_request):
        """Test server errors, with or without a JSON body, are wrapped."""
        for error in (http_error(500, {"error": "Oops"}), http_error(502)):
            with self.subTest(status_code=error.response.status_code):
                mock_api_request.side_effect = error

                with self.assertRaises(services.ProviderAPIError) as context:
                    comicvine.issue("1")

                self.assertEqual(
                    context.exception.status_code,
                    error.response.status_code,
                )
                self.assertNotIn("Oops", str(context.exception))

    @patch("app.providers.services.api_request")
    def test_comic_metadata(self, mock_api_request):
        """Test a volume is normalized, with same-publisher recommendations."""
        mock_api_request.side_effect = [
            {"results": volume()},
            {
                "results": [
                    {"id": 46568, "name": "Saga"},
                    {
                        "id": 111,
                        "name": "Monstress",
                        "image": {"medium_url": "https://example.com/m.jpg"},
                    },
                ],
            },
        ]

        data = comicvine.comic("46568")
        cached = comicvine.comic("46568")

        self.assertEqual(data, cached)
        self.assertEqual(mock_api_request.call_count, 2)
        self.assertEqual(
            mock_api_request.call_args_list[0].args[2],
            "https://comicvine.gamespot.com/api/volume/4050-46568/",
        )
        self.assertEqual(data["title"], "Saga")
        self.assertEqual(data["media_type"], MediaTypes.COMIC.value)
        self.assertEqual(data["image"], "https://example.com/saga.jpg")
        self.assertEqual(data["synopsis"], "An epic space opera .")
        self.assertEqual(data["genres"], [f"Concept {number}" for number in range(5)])
        self.assertEqual(data["max_issue_number"], 66)
        self.assertEqual(data["last_issue_id"], 987)
        self.assertEqual(
            data["details"],
            {
                "start_date": "2012",
                "publisher": "Image",
                "issues_count": 66,
                "last_issue_name": "Chapter 66",
                "last_issue_number": "66",
                "people": [f"Person {number}" for number in range(5)],
                "last_updated": "2024-05-06",
            },
        )
        # The volume itself is not recommended back to the reader.
        self.assertEqual(
            data["related"]["recommendations"],
            [
                {
                    "media_id": "111",
                    "source": Sources.COMICVINE.value,
                    "media_type": MediaTypes.COMIC.value,
                    "title": "Monstress",
                    "image": "https://example.com/m.jpg",
                },
            ],
        )

    @patch("app.providers.services.api_request")
    def test_comic_without_publisher_or_description(self, mock_api_request):
        """Test a sparse volume needs no publisher lookup and still renders."""
        mock_api_request.return_value = {
            "results": {
                "name": "Sparse",
                "site_detail_url": "https://comicvine.gamespot.com/sparse/",
                "publisher": {},
                "last_issue": {"id": 5, "name": None, "issue_number": "463-464"},
                "date_last_updated": "2024-05-06 07:08:09",
            },
        }

        data = comicvine.comic("5")

        mock_api_request.assert_called_once()
        self.assertEqual(data["image"], settings.IMG_NONE)
        self.assertEqual(data["synopsis"], "No synopsis available")
        self.assertIsNone(data["genres"])
        self.assertEqual(data["max_issue_number"], 464)
        self.assertIsNone(data["details"]["publisher"])
        self.assertEqual(data["details"]["people"], [])
        self.assertEqual(data["related"]["recommendations"], [])

    @patch("app.providers.services.api_request")
    def test_unknown_comic_is_not_found(self, mock_api_request):
        """Test an ID Comic Vine has no volume for is a 404, not a crash."""
        mock_api_request.return_value = {"results": []}

        with self.assertRaises(services.ProviderAPIError) as context:
            comicvine.comic("0")

        self.assertEqual(context.exception.status_code, 404)

    @patch("app.providers.services.api_request")
    def test_publisher_comics_respect_limit(self, mock_api_request):
        """Test the recommendation list is capped after dropping the current volume."""
        mock_api_request.return_value = {
            "results": [
                {"id": number, "name": f"Volume {number}"} for number in range(6)
            ],
        }

        data = comicvine.get_publisher_comics(513, "0", limit=3)

        self.assertEqual([item["media_id"] for item in data], ["1", "2", "3"])
        params = mock_api_request.call_args.kwargs["params"]
        self.assertEqual(params["filter"], "publisher:513")
        self.assertEqual(params["limit"], 4)

    @patch("app.providers.services.api_request")
    def test_issue_dates(self, mock_api_request):
        """Test an issue returns its release dates and is cached."""
        mock_api_request.return_value = {
            "results": {"cover_date": "2024-06-01", "store_date": "2024-05-15"},
        }

        data = comicvine.issue("987")
        comicvine.issue("987")

        self.assertEqual(
            data,
            {"cover_date": "2024-06-01", "store_date": "2024-05-15"},
        )
        mock_api_request.assert_called_once()

    def test_issue_number_parsing(self):
        """Test issue numbers are read from plain, compound and odd values."""
        self.assertEqual(comicvine.get_issue_number("12"), 12)
        self.assertEqual(comicvine.get_issue_number("463-464"), 464)
        self.assertIsNone(comicvine.get_issue_number("Annual"))
        self.assertIsNone(comicvine.get_issue_number("1.5-A"))
