from unittest.mock import Mock, patch

import requests
from django.test import RequestFactory, TestCase, override_settings

from integrations.imports import anilist
from integrations.imports.helpers import MediaImportError


def http_error(status_code):
    """Return the error the request helper raises for an HTTP status."""
    response = Mock(status_code=status_code, text="error")
    return requests.exceptions.HTTPError(response=response)


class AniListOAuthTests(TestCase):
    """Test the AniList token exchange without calling the real API."""

    def setUp(self):
        """Create the callback request AniList redirects to."""
        self.request = RequestFactory().get(
            "/import/anilist/private",
            {"code": "abc"},
        )

    @override_settings(ANILIST_ID="client", ANILIST_SECRET="secret")  # noqa: S106
    @patch("app.providers.services.api_request")
    def test_get_token(self, mock_api_request):
        """Test the code is exchanged and the username looked up."""
        mock_api_request.side_effect = [
            {"access_token": "access"},
            {"data": {"Viewer": {"name": "anilist-user"}}},
        ]

        result = anilist.get_token(self.request)

        self.assertEqual(
            result,
            {"access_token": "access", "username": "anilist-user"},
        )
        token_call, user_call = mock_api_request.call_args_list
        params = token_call.kwargs["params"]
        self.assertEqual(params["client_id"], "client")
        self.assertEqual(params["client_secret"], "secret")
        self.assertEqual(params["code"], "abc")
        self.assertTrue(params["redirect_uri"].endswith("/import/anilist/private"))
        self.assertEqual(user_call.kwargs["headers"]["Authorization"], "Bearer access")

    @patch("app.providers.services.api_request")
    def test_rejected_credentials_are_explained(self, mock_api_request):
        """Test a 401 from AniList is reported in plain words."""
        mock_api_request.side_effect = http_error(401)

        with self.assertRaisesMessage(MediaImportError, "Invalid Anilist secret key."):
            anilist.get_token(self.request)
        with self.assertRaisesMessage(
            MediaImportError,
            "Invalid AniList access token.",
        ):
            anilist.get_username_from_oauth("access")

    @patch("app.providers.services.api_request")
    def test_other_oauth_errors_propagate(self, mock_api_request):
        """Test an AniList outage is not blamed on the credentials."""
        mock_api_request.side_effect = http_error(503)

        with self.assertRaises(requests.exceptions.HTTPError):
            anilist.get_token(self.request)
        with self.assertRaises(requests.exceptions.HTTPError):
            anilist.get_username_from_oauth("access")
