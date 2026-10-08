"""Rebrickable v3 official LEGO set search and metadata.

API documentation: https://rebrickable.com/api/v3/docs/
"""

from urllib.parse import quote

import requests
from django.conf import settings
from django.core.cache import cache

from app import helpers
from app.models import MediaTypes, Sources
from app.providers import services

BASE_URL = "https://rebrickable.com/api/v3/lego"
RESULTS_PER_PAGE = 20


def _request(path, params=None):
    """Use the shared rate-limited client with header-only authentication."""
    if not settings.REBRICKABLE:
        raise services.ProviderAPIError(
            Sources.REBRICKABLE.value,
            ValueError("Rebrickable API key is not configured"),
            "Configure REBRICKABLE to search LEGO sets",
        )
    try:
        return services.api_request(
            Sources.REBRICKABLE.value,
            "GET",
            f"{BASE_URL}/{path}",
            params=params,
            headers={"Authorization": f"key {settings.REBRICKABLE}"},
        )
    except requests.exceptions.RequestException as error:
        raise services.ProviderAPIError(Sources.REBRICKABLE.value, error) from error


def _set_summary(data):
    """Preserve Rebrickable's full set number, including the variant suffix."""
    return {
        "media_id": data["set_num"],
        "source": Sources.REBRICKABLE.value,
        "media_type": MediaTypes.LEGO.value,
        "title": data["name"],
        "image": data.get("set_img_url") or settings.IMG_NONE,
    }


def search(query, page):
    """Search sets even when Rebrickable has not populated their inventory."""
    cache_key = f"search_rebrickable_lego_v2_{query}_{page}"
    result = cache.get(cache_key)
    if result is None:
        data = _request(
            "sets/",
            {
                "search": query,
                "page": page,
                "page_size": RESULTS_PER_PAGE,
            },
        )
        result = helpers.format_search_response(
            page,
            RESULTS_PER_PAGE,
            data["count"],
            [_set_summary(item) for item in data["results"]],
        )
        cache.set(cache_key, result)
    return result


def lego(media_id):
    """Return set metadata without turning piece count into build progress."""
    cache_key = f"rebrickable_lego_{media_id}"
    result = cache.get(cache_key)
    if result is None:
        data = _request(f"sets/{quote(str(media_id), safe='')}/")
        theme = _theme_name(data.get("theme_id"))
        result = {
            **_set_summary(data),
            "source_url": (
                f"https://rebrickable.com/sets/{quote(data['set_num'], safe='')}/"
            ),
            "max_progress": None,
            "synopsis": "No synopsis available.",
            "genres": [theme] if theme else [],
            "score": None,
            "score_count": None,
            "details": {
                "set_number": data["set_num"],
                "year": data.get("year"),
                "pieces": data.get("num_parts"),
                "theme": theme,
            },
            "related": {},
        }
        cache.set(cache_key, result)
    return result


def _theme_name(theme_id):
    """Cache theme names across sets; optional enrichment may fail independently."""
    if theme_id is None:
        return None
    cache_key = f"rebrickable_theme_{theme_id}"
    theme = cache.get(cache_key)
    if theme is None:
        try:
            theme = _request(f"themes/{theme_id}/").get("name")
        except services.ProviderAPIError:
            return None
        if theme:
            cache.set(cache_key, theme, timeout=60 * 60 * 24 * 7)
    return theme
