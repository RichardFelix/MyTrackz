"""API keys for the metadata providers, saved from the app.

Every key still has an environment setting (``settings.TMDB_API`` and so on).
A key saved here wins over that setting and is stored encrypted in
``ProviderKey``; removing it falls back to the environment again. Provider
code asks ``get("TMDB_API")`` instead of reading the setting, so a key saved
in one process reaches the web workers and the Celery worker alike.
"""

import base64
import hashlib
import logging
from dataclasses import dataclass

import requests
from cryptography.fernet import Fernet, InvalidToken
from decouple import config
from defusedxml import ElementTree
from django.apps import apps
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

CACHE_KEY = "provider_keys_v1"
CACHE_TIMEOUT = 60 * 60
TEST_TIMEOUT = 15  # seconds; a button press should not hang for minutes
HINT_LENGTH = 4

SOURCE_SAVED = "saved"
SOURCE_ENVIRONMENT = "environment"
SOURCE_BUILT_IN = "built_in"
SOURCE_NONE = "none"

SOURCE_LABELS = {
    SOURCE_SAVED: "Your key, saved here",
    SOURCE_ENVIRONMENT: "Set in the server environment",
    SOURCE_BUILT_IN: "Built-in shared key",
    SOURCE_NONE: "Not set",
}


@dataclass(frozen=True)
class Field:
    """One value a provider needs, named after its setting."""

    name: str
    label: str


@dataclass(frozen=True)
class Provider:
    """A provider shown on the API keys step."""

    slug: str
    name: str
    used_for: str
    fields: tuple
    required: bool
    signup_url: str
    color: str
    # token caches to drop when the key changes
    cache_keys: tuple = ()

    @property
    def initials(self):
        """Return the letters shown in the provider's tile."""
        return self.name[:2].upper()


PROVIDERS = (
    Provider(
        slug="tmdb",
        name="TMDB",
        used_for="Movies and TV shows: search, details, episodes and trending.",
        fields=(Field("TMDB_API", "API key"),),
        required=True,
        signup_url="https://www.themoviedb.org/settings/api",
        color="#01b4e4",
    ),
    Provider(
        slug="mal",
        name="MyAnimeList",
        used_for="Anime and manga: search, details and rankings.",
        fields=(Field("MAL_API", "Client ID"),),
        required=True,
        signup_url="https://myanimelist.net/apiconfig",
        color="#2e51a2",
    ),
    Provider(
        slug="igdb",
        name="IGDB",
        used_for="Video games: search, details and popular games.",
        fields=(
            Field("IGDB_ID", "Twitch client ID"),
            Field("IGDB_SECRET", "Twitch client secret"),
        ),
        required=True,
        signup_url="https://api-docs.igdb.com/#account-creation",
        color="#9147ff",
        cache_keys=("igdb_access_token",),
    ),
    Provider(
        slug="hardcover",
        name="Hardcover",
        used_for="Books: search, details and series.",
        fields=(Field("HARDCOVER_API", "API token"),),
        required=True,
        signup_url="https://hardcover.app/account/api",
        color="#6366f1",
    ),
    Provider(
        slug="comicvine",
        name="Comic Vine",
        used_for="Comics: search, details and new issues.",
        fields=(Field("COMICVINE_API", "API key"),),
        required=True,
        signup_url="https://comicvine.gamespot.com/api/",
        color="#3fa34d",
    ),
    Provider(
        slug="bgg",
        name="BoardGameGeek",
        used_for="Board games: search, details and the hot list.",
        fields=(Field("BGG_API_TOKEN", "API token"),),
        required=True,
        signup_url="https://boardgamegeek.com/using_the_xml_api",
        color="#ff5100",
    ),
    Provider(
        slug="rebrickable",
        name="Rebrickable",
        used_for="LEGO sets: search and details.",
        fields=(Field("REBRICKABLE", "API key"),),
        required=False,
        signup_url="https://rebrickable.com/api/",
        color="#0a5ea8",
    ),
    Provider(
        slug="kicksdb",
        name="KicksDB",
        used_for="Sneakers: searching for new pairs. Saved pairs work without it.",
        fields=(Field("KICKSDB_API", "API key"),),
        required=False,
        signup_url="https://kicks.dev/",
        color="#d6336c",
    ),
    Provider(
        slug="tvdb",
        name="TVDB",
        used_for="Matching shows sent by Plex, Jellyfin and Emby webhooks.",
        fields=(Field("TVDB_API", "API key"),),
        required=False,
        signup_url="https://thetvdb.com/api-information",
        color="#6cd591",
        cache_keys=("tvdb_access_token",),
    ),
    Provider(
        slug="steam",
        name="Steam",
        used_for="Importing your Steam library and playtime.",
        fields=(Field("STEAM_API_KEY", "Web API key"),),
        required=False,
        signup_url="https://steamcommunity.com/dev/apikey",
        color="#1b2838",
    ),
)

PROVIDERS_BY_SLUG = {provider.slug: provider for provider in PROVIDERS}
FIELD_NAMES = frozenset(
    field.name for provider in PROVIDERS for field in provider.fields
)


def _fernet():
    """Return the cipher for saved keys, derived from the instance secret."""
    digest = hashlib.sha256(
        b"mytrackz.provider_keys:" + settings.SECRET_KEY.encode(),
    ).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _model():
    """Return the ProviderKey model (looked up late: models import providers)."""
    return apps.get_model("app", "ProviderKey")


def _saved():
    """Return the saved keys as ``{name: encrypted text}``."""
    saved = cache.get(CACHE_KEY)
    if saved is None:
        saved = dict(_model().objects.values_list("name", "value"))
        cache.set(CACHE_KEY, saved, CACHE_TIMEOUT)
    return saved


def _decrypt(name, token):
    """Return the saved key, or ``""`` when it can no longer be read."""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        # SECRET changed since the key was saved
        logger.warning(
            "Saved %s can't be decrypted with the current SECRET; "
            "falling back to the environment value",
            name,
        )
        return ""


def normalize(name, value):
    """Return the value in the form the provider code expects."""
    value = (value or "").strip()
    if name == "HARDCOVER_API" and value and not value.startswith("Bearer "):
        value = f"Bearer {value}"
    return value


def get(name):
    """Return the key in effect: the saved one, else the environment setting."""
    token = _saved().get(name)
    if token:
        value = _decrypt(name, token)
        if value:
            return value
    return getattr(settings, name)


def source(name):
    """Return where the key in effect comes from."""
    token = _saved().get(name)
    if token and _decrypt(name, token):
        return SOURCE_SAVED
    if not getattr(settings, name):
        return SOURCE_NONE
    if config(name, default="") or config(f"{name}_FILE", default=""):
        return SOURCE_ENVIRONMENT
    return SOURCE_BUILT_IN


def hint(name):
    """Return the tail of a saved key, enough to recognise it by."""
    token = _saved().get(name)
    value = _decrypt(name, token) if token else ""
    if len(value) <= HINT_LENGTH * 2:
        return ""
    return value[-HINT_LENGTH:]


def _drop_caches(provider):
    cache.delete(CACHE_KEY)
    for key in provider.cache_keys:
        cache.delete(key)


def save(provider, values):
    """Save the non-empty values given for a provider's fields."""
    changed = False
    for field in provider.fields:
        value = normalize(field.name, values.get(field.name))
        if not value:
            continue
        _model().objects.update_or_create(
            name=field.name,
            defaults={"value": _fernet().encrypt(value.encode()).decode()},
        )
        changed = True
    if changed:
        _drop_caches(provider)
    return changed


def clear(provider):
    """Remove a provider's saved keys, going back to the environment."""
    names = [field.name for field in provider.fields]
    deleted, _ = _model().objects.filter(name__in=names).delete()
    _drop_caches(provider)
    return bool(deleted)


def status(provider):
    """Return what the API keys step shows for a provider."""
    fields = [
        {
            "name": field.name,
            "label": field.label,
            "source": source(field.name),
            "hint": hint(field.name),
        }
        for field in provider.fields
    ]
    sources = {field["source"] for field in fields}
    if SOURCE_NONE in sources:
        overall = SOURCE_NONE
    elif SOURCE_SAVED in sources:
        overall = SOURCE_SAVED
    elif SOURCE_ENVIRONMENT in sources:
        overall = SOURCE_ENVIRONMENT
    else:
        overall = SOURCE_BUILT_IN
    return {
        "provider": provider,
        "fields": fields,
        "source": overall,
        "source_label": SOURCE_LABELS[overall],
        "has_saved": SOURCE_SAVED in sources,
    }


# Connection tests. Each sends one cheap request with the given values and
# raises on a rejected key.


def _send(method, url, **kwargs):
    # the shared session carries the per-host rate limits
    from app.providers import services  # noqa: PLC0415

    response = services.session.request(
        method,
        url,
        timeout=TEST_TIMEOUT,
        **kwargs,
    )
    response.raise_for_status()
    return response


def _test_tmdb(values):
    _send(
        "GET",
        "https://api.themoviedb.org/3/configuration",
        params={"api_key": values["TMDB_API"]},
    )


def _test_mal(values):
    _send(
        "GET",
        "https://api.myanimelist.net/v2/anime",
        params={"q": "one", "limit": 1},
        headers={"X-MAL-CLIENT-ID": values["MAL_API"]},
    )


def _test_igdb(values):
    _send(
        "POST",
        "https://id.twitch.tv/oauth2/token",
        json={
            "client_id": values["IGDB_ID"],
            "client_secret": values["IGDB_SECRET"],
            "grant_type": "client_credentials",
        },
    )


def _test_hardcover(values):
    response = _send(
        "POST",
        "https://api.hardcover.app/v1/graphql",
        json={"query": "query { me { id } }"},
        headers={"Authorization": values["HARDCOVER_API"]},
    )
    if response.json().get("errors"):
        msg = "Hardcover did not accept the token"
        raise ValueError(msg)


def _test_comicvine(values):
    response = _send(
        "GET",
        "https://comicvine.gamespot.com/api/types/",
        params={"api_key": values["COMICVINE_API"], "format": "json"},
        headers={"User-Agent": "Mozilla/5.0"},
    )
    if response.json().get("status_code") != 1:
        msg = "Comic Vine did not accept the key"
        raise ValueError(msg)


def _test_bgg(values):
    response = _send(
        "GET",
        "https://boardgamegeek.com/xmlapi2/search",
        params={"query": "catan", "type": "boardgame"},
        headers={"Authorization": f"Bearer {values['BGG_API_TOKEN']}"},
    )
    ElementTree.fromstring(response.text)


def _test_rebrickable(values):
    _send(
        "GET",
        "https://rebrickable.com/api/v3/lego/themes/",
        params={"page_size": 1},
        headers={"Authorization": f"key {values['REBRICKABLE']}"},
    )


def _test_kicksdb(values):
    _send(
        "GET",
        "https://api.kicks.dev/v3/goat/products",
        params={"query": "jordan", "page": 1, "limit": 1, "market": "US"},
        headers={"Authorization": f"Bearer {values['KICKSDB_API']}"},
    )


def _test_tvdb(values):
    _send(
        "POST",
        "https://api4.thetvdb.com/v4/login",
        json={"apikey": values["TVDB_API"]},
    )


def _test_steam(values):
    _send(
        "GET",
        "https://api.steampowered.com/ISteamWebAPIUtil/GetSupportedAPIList/v1/",
        params={"key": values["STEAM_API_KEY"]},
    )


TESTS = {
    "tmdb": _test_tmdb,
    "mal": _test_mal,
    "igdb": _test_igdb,
    "hardcover": _test_hardcover,
    "comicvine": _test_comicvine,
    "bgg": _test_bgg,
    "rebrickable": _test_rebrickable,
    "kicksdb": _test_kicksdb,
    "tvdb": _test_tvdb,
    "steam": _test_steam,
}

REJECTED_STATUS_CODES = {
    requests.codes.bad_request,
    requests.codes.unauthorized,
    requests.codes.forbidden,
}


def _failure_message(provider, error):
    """Say why a connection test failed, without echoing the key."""
    if isinstance(error, requests.exceptions.HTTPError):
        status_code = getattr(error.response, "status_code", None)
        if status_code in REJECTED_STATUS_CODES:
            return f"{provider.name} rejected the key (HTTP {status_code})."
        if status_code == requests.codes.too_many_requests:
            return f"{provider.name} is rate limiting; try again shortly."
        return f"{provider.name} answered with HTTP {status_code}."
    if isinstance(error, requests.exceptions.RequestException):
        return f"Could not reach {provider.name}."
    return f"{provider.name} did not accept the key."


def test_connection(provider, values):
    """Try a provider's keys and return ``(ok, message)``.

    A field left empty in ``values`` is tested with the key now in effect, so
    the button also answers "does what I already have work?".
    """
    candidate = {}
    for field in provider.fields:
        value = normalize(field.name, values.get(field.name)) or get(field.name)
        if not value:
            return False, f"Enter the {field.label.lower()} first."
        candidate[field.name] = value

    try:
        TESTS[provider.slug](candidate)
    except (
        requests.exceptions.RequestException,
        ValueError,
        ElementTree.ParseError,
    ) as error:
        return False, _failure_message(provider, error)
    return True, f"Connected to {provider.name}."
