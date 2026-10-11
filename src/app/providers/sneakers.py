"""KicksDB discovery with permanent, one-time local sneaker snapshots."""

import hashlib
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

import requests
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db.models import Q
from django.http import Http404

from app.models import MediaTypes, SneakerProduct, Sources
from app.providers import services

BASE_URL = "https://api.kicks.dev/v3"
RESULTS_PER_PAGE = 20
SEARCH_CACHE_SECONDS = 60 * 60


def _results(page, total, results):
    """Round up pagination without a spurious page at exact multiples."""
    return {
        "page": page,
        "total_results": total,
        "total_pages": max(1, (total + RESULTS_PER_PAGE - 1) // RESULTS_PER_PAGE),
        "results": results,
    }


def _request(path, params=None):
    """Send the secret only in the shared provider client's Authorization header."""
    if not settings.KICKSDB_API:
        raise services.ProviderAPIError(
            Sources.KICKSDB,
            ValueError("Missing catalog key"),
            "Configure KICKSDB_API to search for new sneakers",
        )
    try:
        return services.api_request(
            Sources.KICKSDB,
            "GET",
            f"{BASE_URL}/{path}",
            params=params,
            headers={"Authorization": f"Bearer {settings.KICKSDB_API}"},
            retry_on_rate_limit=False,
        )
    except requests.RequestException as error:
        raise services.ProviderAPIError(Sources.KICKSDB, error) from error


def _sku(value):
    """Match manufacturer codes despite marketplace spacing and punctuation."""
    return re.sub(r"[^A-Z0-9]", "", str(value).upper())


def _url(value):
    """Drop malformed provider URLs instead of persisting executable schemes."""
    if not value:
        return ""
    try:
        URLValidator(schemes=["https"])(value)
    except ValidationError:
        return ""
    return (
        value
        if len(value) <= SneakerProduct._meta.get_field("image").max_length
        else ""
    )


def _summary(data, platform):
    """Normalize search identities without confusing GOAT and StockX IDs."""
    media_id = f"goat-{data['id']}" if platform == "goat" else str(data["id"])
    return {
        "media_id": media_id,
        "source": Sources.KICKSDB.value,
        "media_type": MediaTypes.SNEAKER.value,
        "title": data.get("name") or data.get("title") or "Unnamed sneaker",
        "image": _url(data.get("image_url") or data.get("image")) or settings.IMG_NONE,
    }


def _local_search(query, page):
    """Allow offline browsing of previously captured sneakers."""
    products = SneakerProduct.objects.all()
    for word in query.split():
        products = products.filter(
            Q(name__icontains=word)
            | Q(brand__icontains=word)
            | Q(style_code__icontains=word)
            | Q(colorway__icontains=word),
        )
    start = (page - 1) * RESULTS_PER_PAGE
    return _results(
        page,
        products.count(),
        [
            {
                "media_id": p.pk,
                "source": Sources.KICKSDB.value,
                "media_type": MediaTypes.SNEAKER.value,
                "title": p.name,
                "image": p.image or settings.IMG_NONE,
            }
            for p in products[start : start + RESULTS_PER_PAGE]
        ],
    )


def _search_page(platform, query, page):
    """Retry a transient server failure once, but never retry exhausted quotas."""
    path = f"{platform}/products"
    params = {"query": query, "page": page, "limit": RESULTS_PER_PAGE, "market": "US"}
    try:
        return _request(path, params)
    except services.ProviderAPIError as error:
        if error.status_code is None or not 500 <= error.status_code < 600:  # noqa: PLR2004
            raise
        return _request(path, params)


def _catalog_page(query, page, digest):
    """Keep later pages on the same marketplace as the first page."""
    first_page = cache.get(f"kicksdb_search_v1_{digest}_1")
    if page > 1:
        first_results = first_page.get("results", []) if first_page else []
        platform = (
            "stockx"
            if first_results and not first_results[0]["media_id"].startswith("goat-")
            else "goat"
        )
        return _search_page(platform, query, page), platform
    try:
        return _search_page("goat", query, page), "goat"
    except services.ProviderAPIError:
        return _search_page("stockx", query, page), "stockx"


def search(query, page):
    """Search GOAT first; cache discovery without changing saved snapshots."""
    page = max(1, int(page))
    if not query.strip() or not settings.KICKSDB_API:
        if query.strip() and not SneakerProduct.objects.exists():
            _request("goat/products")
        return _local_search(query, page)
    digest = hashlib.sha256(query.strip().casefold().encode()).hexdigest()
    key = f"kicksdb_search_v1_{digest}_{page}"
    result = cache.get(key)
    if result is not None:
        return result
    try:
        data, platform = _catalog_page(query, page, digest)
    except services.ProviderAPIError:
        local = _local_search(query, page)
        local["search_unavailable"] = True
        # Do not cache failures: the retry link must perform a fresh lookup.
        return local
    products = [
        p
        for p in (data.get("data") or [])
        if p.get("id") and p.get("product_type") in {None, "sneakers", "shoes"}
    ]
    results = []
    for product in products:
        summary = _summary(product, platform)
        results.append(summary)
        cache.set(
            f"kicksdb_discovery_{summary['media_id']}",
            {"platform": platform, "product": product},
            SEARCH_CACHE_SECONDS,
        )
    meta = data.get("meta") or {}
    total = meta.get("total")
    if total is None:
        total = (page - 1) * RESULTS_PER_PAGE + len(results)
        if len(results) == RESULTS_PER_PAGE:
            total += 1
    result = _results(page, total, results)
    cache.set(key, result, SEARCH_CACHE_SECONDS)
    return result


def _release_date(value):
    """Preserve a date's literal day and reject missing/sentinel timestamps."""
    try:
        result = date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
    return result if result.year >= 1900 else None  # noqa: PLR2004


def _retail_price(traits):
    """Keep a provider claim separate from a user's verified original MSRP."""
    try:
        value = Decimal(traits.get("Retail Price", ""))
        if value.is_finite() and 0 < value < Decimal(100000000):
            return value.quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        pass
    return None


def _stockx_traits(data):
    """Use included traits or request them once if this was a search result."""
    if data.get("traits") is not None:
        return {t["trait"]: t.get("value") for t in data["traits"]}
    try:
        product = (
            _request(
                f"stockx/products/{quote(str(data['id']), safe='')}",
                {"display[traits]": "true", "market": "US"},
            ).get("data")
            or {}
        )
        return {t["trait"]: t.get("value") for t in product.get("traits") or []}
    except services.ProviderAPIError:
        return {}


def _retail_traits(data, platform):
    """Make optional retail enrichment once, tolerating missing free-tier fields."""
    if platform == "stockx":
        return _stockx_traits(data)
    if not data.get("sku"):
        return {}
    try:
        products = (
            _request(
                "stockx/products", {"query": data["sku"], "limit": 20, "market": "US"}
            ).get("data")
            or []
        )
        match = next(
            (p for p in products if _sku(p.get("sku")) == _sku(data["sku"])), None
        )
        if match is None:
            return {}
        product = (
            _request(
                f"stockx/products/{quote(str(match['id']), safe='')}",
                {"display[traits]": "true", "market": "US"},
            ).get("data")
            or {}
        )
        return {t["trait"]: t.get("value") for t in product.get("traits") or []}
    except services.ProviderAPIError:
        return {}


def _capture(media_id):
    """Capture the first catalog result; never update a previously saved shoe."""
    if len(media_id) > SneakerProduct._meta.get_field("catalog_id").max_length:
        msg = "Invalid sneaker ID"
        raise Http404(msg)
    discovered = cache.get(f"kicksdb_discovery_{media_id}")
    if discovered:
        platform, data = discovered["platform"], discovered["product"]
    else:
        platform = "goat" if media_id.startswith("goat-") else "stockx"
        provider_id = media_id.removeprefix("goat-")
        params = {"market": "US"}
        if platform == "stockx":
            params["display[traits]"] = "true"
        response = _request(
            f"{platform}/products/{quote(provider_id, safe='')}", params
        )
        data = response.get("data")
        if not isinstance(data, dict) or str(data.get("id")) != provider_id:
            msg = "Sneaker not found"
            raise Http404(msg)
    traits = _retail_traits(data, platform)
    image = _summary(data, platform)["image"]
    source_url = (
        f"https://www.goat.com/sneakers/{quote(data.get('slug') or '', safe='')}"
    )
    if platform == "stockx":
        source_url = f"https://stockx.com/{quote(data.get('slug') or '', safe='')}"
    retail_price = _retail_price(traits)
    defaults = {
        "name": (data.get("name") or data.get("title") or "Unnamed sneaker")[:250],
        "brand": (data.get("brand") or "")[:100],
        "style_code": (data.get("sku") or traits.get("Style") or "")[:100],
        "colorway": (data.get("colorway") or traits.get("Colorway") or "")[:250],
        "image": "" if image == settings.IMG_NONE else image,
        "source_url": source_url,
        "release_date": _release_date(
            data.get("release_date") or traits.get("Release Date")
        ),
        "reported_retail_price": retail_price,
        "reported_currency": "USD" if retail_price is not None else "",
    }
    product, _created = SneakerProduct.objects.get_or_create(
        pk=media_id, defaults=defaults
    )
    return product


def sneaker(media_id):
    """Read saved metadata offline; the first lookup is the only API import."""
    product = SneakerProduct.objects.filter(pk=media_id).first()
    if product is None:
        product = _capture(str(media_id))
    return {
        "media_id": product.pk,
        "source": Sources.KICKSDB.value,
        "media_type": MediaTypes.SNEAKER.value,
        "title": product.name,
        "image": product.image or settings.IMG_NONE,
        "source_url": product.source_url,
        "max_progress": None,
        "synopsis": "",
        "genres": [product.brand] if product.brand else [],
        "score": None,
        "score_count": None,
        "related": {},
        "details": {
            "brand": product.brand,
            "style_code": product.style_code,
            "colorway": product.colorway,
            "release_date": product.release_date.isoformat()
            if product.release_date
            else None,
        },
        "catalog_product": product,
    }
