"""Replay recorded HTTP responses so the test suite does not need the network.

Many tests exercise real provider code paths (TMDB, MyAnimeList, IGDB, ...).
Calling the live services made the suite slow to trust: a provider outage or
rate limit turned the gate red. This module patches the ``requests`` transport
so every outgoing request is answered from a file under
``config/tests/http_recordings``.

Modes, chosen with the ``HTTP_REPLAY`` environment variable:

- unset or ``replay``: answer from recordings; an unrecorded request fails
  with a message naming it.
- ``record``: call the real service and save any response that is not
  recorded yet. Run the suite once in this mode after adding a test that
  needs a new request.
- ``live``: do not patch anything; talk to the real services.

Recordings never contain credentials: API keys are dropped from the request
before it is matched or saved, and tokens in responses are replaced.
"""

import base64
import hashlib
import io
import json
import os
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import aiohttp
import requests
from requests.adapters import HTTPAdapter
from requests.structures import CaseInsensitiveDict
from requests.utils import get_encoding_from_headers
from urllib3 import HTTPResponse

RECORDINGS_DIR = Path(__file__).resolve().parent / "tests" / "http_recordings"

# Request fields that carry credentials. They are ignored when matching a
# request to a recording and are never written to disk.
SECRET_FIELDS = frozenset(
    {
        "access_token",
        "api_key",
        "api_token",
        "apikey",
        "client_id",
        "client_secret",
        "key",
        "pin",
        "refresh_token",
        "token",
    },
)

# Response fields whose values are credentials issued by the service.
TOKEN_FIELDS = frozenset({"access_token", "refresh_token", "token"})
RECORDED_TOKEN = "recorded-token"  # noqa: S105

KEPT_HEADERS = ("Content-Type", "Location", "Retry-After")
SLUG_LENGTH = 48

_real_send = HTTPAdapter.send


class UnrecordedRequestError(requests.exceptions.ConnectionError):
    """A test made an HTTP request that has no recording."""


def mode():
    """Return the active mode: replay, record or live."""
    return os.environ.get("HTTP_REPLAY", "replay").lower()


def install():
    """Patch the HTTP clients according to the active mode."""
    if mode() != "live":
        HTTPAdapter.send = _send
        aiohttp.ClientSession.get = _aiohttp_get
        aiohttp.ClientSession._request = _aiohttp_unsupported


def _send(adapter, request, **kwargs):
    """Answer a request from its recording, recording it first if allowed."""
    url, body = _normalise(request)
    path = _recording_path(request.method, url, body)

    recording = _read(path)

    if mode() == "record":
        if recording is None:
            response = _real_send(adapter, request, **kwargs)
            if _is_transient(response):
                # A rate limit or outage says nothing about the request;
                # recording it would make the test fail forever.
                return response
            _save(path, request.method, url, body, response)
            if response.ok:
                # Hand back the real response: it may carry a real token that
                # the requests recorded after it depend on.
                return response
            recording = _read(path)
        elif recording.get("tokens_replaced"):
            # The saved token is a placeholder, which the real service would
            # reject on the requests that follow. Fetch a real one.
            return _real_send(adapter, request, **kwargs)

    if recording is None:
        msg = (
            f"No recorded response for {request.method} {url}. Record it with "
            "HTTP_REPLAY=record, or mock the request in the test."
        )
        raise UnrecordedRequestError(msg, request=request)

    return _build_response(recording, request)


def _is_transient(response):
    """Return whether a response reflects the service's state, not the request."""
    return (
        response.status_code == requests.codes.too_many_requests
        or response.status_code >= requests.codes.internal_server_error
    )


def _read(path):
    """Return a recording, or None when the request has not been recorded."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def _normalise(request):
    """Return the request's URL and body without credentials."""
    parts = urlsplit(request.url)
    query = sorted(
        (name, value)
        for name, value in parse_qsl(parts.query, keep_blank_values=True)
        if name.lower() not in SECRET_FIELDS
    )
    url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))
    return url, _normalise_body(request.body)


def _normalise_body(body):
    """Return a request body as text with credentials removed."""
    if not body:
        return ""
    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="replace")

    try:
        data = json.loads(body)
    except ValueError:
        pass
    else:
        return json.dumps(_without_secrets(data), sort_keys=True)

    if "=" in body and "\n" not in body and " " not in body:
        fields = [
            (name, value)
            for name, value in parse_qsl(body, keep_blank_values=True)
            if name.lower() not in SECRET_FIELDS
        ]
        return urlencode(sorted(fields))

    return body


def _without_secrets(data):
    """Return JSON data with credential fields removed."""
    if isinstance(data, dict):
        return {
            name: _without_secrets(value)
            for name, value in data.items()
            if name.lower() not in SECRET_FIELDS
        }
    if isinstance(data, list):
        return [_without_secrets(item) for item in data]
    return data


def _with_recorded_tokens(data):
    """Return JSON data with issued tokens replaced by a fixed value."""
    if isinstance(data, dict):
        return {
            name: (
                RECORDED_TOKEN
                if name.lower() in TOKEN_FIELDS and isinstance(value, str)
                else _with_recorded_tokens(value)
            )
            for name, value in data.items()
        }
    if isinstance(data, list):
        return [_with_recorded_tokens(item) for item in data]
    return data


def _recording_path(method, url, body):
    """Return the file that holds the recording for a request."""
    parts = urlsplit(url)
    digest = hashlib.sha256(f"{method} {url}\n{body}".encode()).hexdigest()[:16]
    slug = re.sub(r"[^A-Za-z0-9]+", "-", parts.path).strip("-")[-SLUG_LENGTH:]
    name = f"{method}_{slug or 'root'}_{digest}.json"
    return RECORDINGS_DIR / parts.netloc.replace(":", "_") / name


def _save(path, method, url, body, response):
    """Write a response to its recording file."""
    recording = {
        "request": {"method": method, "url": url, "body": body},
        "status": response.status_code,
        "headers": {
            name: response.headers[name]
            for name in KEPT_HEADERS
            if name in response.headers
        },
    }

    content = response.content
    try:
        data = json.loads(content)
        recording["json"] = _with_recorded_tokens(data)
        if recording["json"] != data:
            recording["tokens_replaced"] = True
    except ValueError:
        try:
            recording["text"] = content.decode("utf-8")
        except UnicodeDecodeError:
            recording["base64"] = base64.b64encode(content).decode("ascii")

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(recording, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(path)


def _build_response(recording, request):
    """Build a response from a recording."""
    if "json" in recording:
        content = json.dumps(recording["json"]).encode("utf-8")
    elif "text" in recording:
        content = recording["text"].encode("utf-8")
    else:
        content = base64.b64decode(recording["base64"])

    headers = CaseInsensitiveDict(recording["headers"])
    response = requests.Response()
    response.status_code = recording["status"]
    response.headers = headers
    response.encoding = get_encoding_from_headers(headers)
    response.reason = requests.status_codes._codes.get(
        recording["status"],
        [""],
    )[0].upper()
    response.url = request.url
    response.request = request
    response.raw = HTTPResponse(
        body=io.BytesIO(content),
        headers=dict(headers),
        status=recording["status"],
        preload_content=False,
    )
    return response


class _AiohttpResponse:
    """The parts of an aiohttp response the application reads."""

    def __init__(self, response):
        self._response = response
        self.status = response.status_code
        self.headers = response.headers

    async def json(self, **_kwargs):
        return self._response.json()

    async def text(self, **_kwargs):
        return self._response.text

    async def read(self):
        return self._response.content

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc_info):
        return False

    def __await__(self):
        async def _self():
            return self

        return _self().__await__()


def _aiohttp_get(session, url, *, params=None, headers=None, **_kwargs):
    """Answer an aiohttp GET from the same recordings as requests."""
    request = requests.Request(
        "GET",
        str(url),
        params=params,
        headers={**dict(session.headers), **dict(headers or {})},
    ).prepare()
    return _AiohttpResponse(_send(HTTPAdapter(), request))


async def _aiohttp_unsupported(_session, method, url, **_kwargs):
    """Refuse aiohttp requests the replay layer cannot answer."""
    msg = (
        f"aiohttp {method} {url} cannot be replayed: only GET is recorded. "
        "Mock the request in the test."
    )
    raise aiohttp.ClientConnectionError(msg)
