import asyncio
import json
import tempfile
from pathlib import Path
from unittest import mock

import aiohttp
import requests
from django.test import SimpleTestCase
from requests.adapters import HTTPAdapter

from config import http_replay


def prepared(method, url, **kwargs):
    """Return a prepared request, as the transport receives it."""
    return requests.Request(method, url, **kwargs).prepare()


def live_response(status=200, body=b'{"title": "Example"}', content_type=None):
    """Return a response as the real service would send it."""
    response = requests.Response()
    response.status_code = status
    response._content = body
    response.headers["Content-Type"] = content_type or "application/json"
    return response


class HTTPReplayTests(SimpleTestCase):
    """Test the layer that answers test HTTP requests from recordings."""

    def setUp(self):
        """Point the recordings at an empty folder and watch the real transport."""
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.recordings = Path(folder.name)
        patcher = mock.patch.object(http_replay, "RECORDINGS_DIR", self.recordings)
        patcher.start()
        self.addCleanup(patcher.stop)

        patcher = mock.patch.object(http_replay, "_real_send")
        self.real_send = patcher.start()
        self.addCleanup(patcher.stop)

    def send(self, request, replay_mode="replay"):
        """Send a request through the replay layer in the given mode."""
        with mock.patch.dict("os.environ", {"HTTP_REPLAY": replay_mode}):
            return http_replay._send(HTTPAdapter(), request)

    def recorded_files(self):
        """Return every recording file written so far."""
        return sorted(self.recordings.rglob("*.json"))

    def test_unrecorded_request_fails_without_touching_the_network(self):
        """Test a request with no recording is refused and named."""
        request = prepared("GET", "https://api.example.com/movie/1")

        with self.assertRaises(http_replay.UnrecordedRequestError) as context:
            self.send(request)

        self.assertIn("GET https://api.example.com/movie/1", str(context.exception))
        self.real_send.assert_not_called()

    def test_unrecorded_request_is_a_connection_error(self):
        """Test application code sees an unrecorded request as a network failure."""
        self.assertTrue(
            issubclass(
                http_replay.UnrecordedRequestError,
                requests.exceptions.ConnectionError,
            ),
        )

    def test_record_then_replay(self):
        """Test a recorded response is replayed without calling the service again."""
        self.real_send.return_value = live_response()
        request = prepared("GET", "https://api.example.com/movie/1")

        recorded = self.send(request, "record")
        replayed = self.send(request)

        self.real_send.assert_called_once()
        self.assertEqual(recorded.json(), {"title": "Example"})
        self.assertEqual(replayed.json(), {"title": "Example"})
        self.assertEqual(replayed.status_code, 200)
        self.assertEqual(replayed.url, "https://api.example.com/movie/1")
        self.assertEqual(len(self.recorded_files()), 1)
        self.assertEqual(self.recorded_files()[0].parent.name, "api.example.com")

    def test_record_mode_reuses_existing_recordings(self):
        """Test recording again does not call the service for known requests."""
        self.real_send.return_value = live_response()
        request = prepared("GET", "https://api.example.com/movie/1")

        self.send(request, "record")
        self.send(request, "record")

        self.real_send.assert_called_once()

    def test_api_keys_are_not_matched_or_saved(self):
        """Test requests that differ only in credentials share one clean recording."""
        self.real_send.return_value = live_response()

        self.send(
            prepared(
                "GET",
                "https://api.example.com/movie/1",
                params={"api_key": "first-secret", "language": "en"},
            ),
            "record",
        )
        response = self.send(
            prepared(
                "GET",
                "https://api.example.com/movie/1",
                params={"language": "en", "api_key": "second-secret"},
            ),
        )

        self.assertEqual(response.json(), {"title": "Example"})
        saved = self.recorded_files()[0].read_text()
        self.assertNotIn("secret", saved)
        self.assertIn("language=en", saved)

    def test_credentials_in_bodies_are_not_matched_or_saved(self):
        """Test JSON and form bodies are stored without their credentials."""
        self.real_send.return_value = live_response()
        cases = [
            {"json": {"apikey": "body-secret", "query": "matrix"}},
            {"data": {"client_secret": "body-secret", "grant_type": "code"}},
        ]

        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                self.send(
                    prepared("POST", "https://api.example.com/login", **kwargs),
                    "record",
                )

        self.assertEqual(len(self.recorded_files()), 2)
        for path in self.recorded_files():
            self.assertNotIn("body-secret", path.read_text())

    def test_different_bodies_are_different_recordings(self):
        """Test two queries to one endpoint do not answer for each other."""
        self.real_send.side_effect = [
            live_response(body=b'{"name": "first"}'),
            live_response(body=b'{"name": "second"}'),
        ]
        first = prepared("POST", "https://api.example.com/search", data="fields a;")
        second = prepared("POST", "https://api.example.com/search", data="fields b;")

        self.send(first, "record")
        self.send(second, "record")

        self.assertEqual(self.send(first).json(), {"name": "first"})
        self.assertEqual(self.send(second).json(), {"name": "second"})

    def test_issued_tokens_are_replaced(self):
        """Test a token issued by a service is never written to disk."""
        self.real_send.return_value = live_response(
            body=b'{"access_token": "real-token", "data": {"token": "real-token"}}',
        )
        request = prepared("POST", "https://id.example.com/oauth2/token")

        recorded = self.send(request, "record")
        replayed = self.send(request)

        # The recording run itself needs the real token for later requests.
        self.assertEqual(recorded.json()["access_token"], "real-token")
        self.assertEqual(
            replayed.json(),
            {"access_token": "recorded-token", "data": {"token": "recorded-token"}},
        )
        self.assertNotIn("real-token", self.recorded_files()[0].read_text())

    def test_recording_again_fetches_a_real_token(self):
        """Test a later recording run does not send the placeholder token."""
        self.real_send.return_value = live_response(
            body=b'{"access_token": "real-token"}',
        )
        request = prepared("POST", "https://id.example.com/oauth2/token")
        self.send(request, "record")

        response = self.send(request, "record")

        self.assertEqual(response.json()["access_token"], "real-token")
        self.assertEqual(self.real_send.call_count, 2)

    def test_error_responses_are_recorded(self):
        """Test a "not found" answer is part of the service's behaviour."""
        self.real_send.return_value = live_response(
            status=404,
            body=b'{"error": "nope"}',
        )
        request = prepared("GET", "https://api.example.com/movie/0")

        self.send(request, "record")
        response = self.send(request)

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.reason, "NOT_FOUND")
        with self.assertRaises(requests.exceptions.HTTPError):
            response.raise_for_status()

    def test_rate_limits_and_outages_are_not_recorded(self):
        """Test a transient failure while recording is passed on but not kept."""
        request = prepared("GET", "https://api.example.com/movie/1")

        for status in (429, 500, 503):
            with self.subTest(status=status):
                self.real_send.return_value = live_response(status=status)

                response = self.send(request, "record")

                self.assertEqual(response.status_code, status)
                self.assertEqual(self.recorded_files(), [])

    def test_text_and_binary_responses_round_trip(self):
        """Test XML and image responses are replayed byte for byte."""
        cases = [
            (b"<items><item id='1'/></items>", "text/xml; charset=utf-8"),
            (b"\x89PNG\r\n\x1a\n\x00\xff", "image/png"),
        ]

        for index, (body, content_type) in enumerate(cases):
            with self.subTest(content_type=content_type):
                self.real_send.return_value = live_response(
                    body=body,
                    content_type=content_type,
                )
                request = prepared("GET", f"https://api.example.com/file/{index}")

                self.send(request, "record")
                response = self.send(request)

                self.assertEqual(response.content, body)
                self.assertEqual(response.headers["content-type"], content_type)

    def test_streamed_responses_can_be_read_in_chunks(self):
        """Test code that streams a download can read a replayed response."""
        body = b"0123456789" * 10
        self.real_send.return_value = live_response(
            body=body,
            content_type="image/jpeg",
        )
        request = prepared("GET", "https://img.example.com/poster.jpg")
        self.send(request, "record")

        response = self.send(request)

        self.assertEqual(b"".join(response.iter_content(chunk_size=16)), body)

    def test_recordings_are_stable_json(self):
        """Test a recording file holds the cleaned request beside the response."""
        self.real_send.return_value = live_response()
        self.send(
            prepared("GET", "https://api.example.com/movie/1", params={"key": "x"}),
            "record",
        )

        recording = json.loads(self.recorded_files()[0].read_text())

        self.assertEqual(
            recording["request"],
            {"method": "GET", "url": "https://api.example.com/movie/1", "body": ""},
        )
        self.assertEqual(recording["status"], 200)
        self.assertEqual(recording["json"], {"title": "Example"})

    def test_aiohttp_get_uses_the_same_recordings(self):
        """Test an aiohttp GET is answered from a recording made with requests."""
        self.real_send.return_value = live_response()
        url = "https://api.example.com/series/1"
        self.send(prepared("GET", url), "record")

        async def fetch():
            async with (
                aiohttp.ClientSession(headers={"User-Agent": "test"}) as session,
                session.get(url) as response,
            ):
                return response.status, await response.json()

        with (
            mock.patch.dict("os.environ", {"HTTP_REPLAY": "replay"}),
            mock.patch.object(aiohttp.ClientSession, "get", http_replay._aiohttp_get),
        ):
            status, data = asyncio.run(fetch())

        self.assertEqual(status, 200)
        self.assertEqual(data, {"title": "Example"})
        self.real_send.assert_called_once()

    def test_other_aiohttp_requests_are_refused(self):
        """Test an aiohttp request that cannot be replayed never reaches the network."""

        async def post():
            async with aiohttp.ClientSession() as session:
                await http_replay._aiohttp_unsupported(
                    session,
                    "POST",
                    "https://api.example.com/series",
                )

        with self.assertRaisesMessage(
            aiohttp.ClientConnectionError,
            "cannot be replayed",
        ):
            asyncio.run(post())

    def test_live_mode_leaves_the_transport_alone(self):
        """Test live mode does not patch requests."""
        with (
            mock.patch.dict("os.environ", {"HTTP_REPLAY": "live"}),
            mock.patch.object(HTTPAdapter, "send", mock.sentinel.original),
        ):
            http_replay.install()

            self.assertIs(HTTPAdapter.send, mock.sentinel.original)
