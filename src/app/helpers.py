import logging
from datetime import date, datetime
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse

import requests
from django.apps import apps
from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Q
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import redirect
from django.utils import timezone
from django.utils.encoding import iri_to_uri
from django.utils.http import url_has_allowed_host_and_scheme

from app.models import BasicMedia, Item, MediaTypes, Sources, Status
from app.tasks import cache_item_image

logger = logging.getLogger(__name__)

YEAR_ONLY_PARTS = 1
YEAR_MONTH_PARTS = 2

# Media types whose provider metadata includes direct follow-ups (collections,
# related anime/manga, book series, game expansions) beyond the generic
# "recommendations" section.
UNFINISHED_COLLECTION_MEDIA_TYPES = (
    MediaTypes.MOVIE.value,
    MediaTypes.ANIME.value,
    MediaTypes.MANGA.value,
    MediaTypes.GAME.value,
    MediaTypes.BOOK.value,
    MediaTypes.BOARDGAME.value,
)
UNFINISHED_COLLECTION_SEEDS_PER_TYPE = 3
UNFINISHED_COLLECTION_MAX_ITEMS = 8
# Related sections that aren't genuine follow-ups: generic suggestions, or (for
# OpenLibrary) other editions of the same book rather than a different book.
IGNORED_RELATED_SECTIONS = {"recommendations", "other_editions"}

# All media types can appear in Discover. Movies/TV/Anime/Manga/Games/Comics have a
# real "recommendations" section; Books (Hardcover) and Boardgames (BGG) don't, so
# they fall back to their direct-relation data (series siblings, expansions) instead
# -- see _get_discover_recommendation_items.
DISCOVER_MEDIA_TYPES = (
    MediaTypes.MOVIE.value,
    MediaTypes.TV.value,
    MediaTypes.ANIME.value,
    MediaTypes.MANGA.value,
    MediaTypes.GAME.value,
    MediaTypes.COMIC.value,
    MediaTypes.BOOK.value,
    MediaTypes.BOARDGAME.value,
)
# Seed from both finished and currently-being-watched/read/played media.
DISCOVER_SEED_STATUSES = (Status.COMPLETED.value, Status.IN_PROGRESS.value)
DISCOVER_SEEDS_PER_TYPE = 5
DISCOVER_MAX_ITEMS_PER_TYPE = 50
# The computed feed is cached per user and refreshed daily by the "Refresh
# discover feeds" beat task; 48h TTL so the feed survives one missed run.
DISCOVER_FEED_TTL = 60 * 60 * 48
# While a feed build is queued/running, a short-lived pending marker stops the
# polling page from queueing duplicate builds; its expiry doubles as the retry
# window if a build crashes without cleaning up.
DISCOVER_FEED_PENDING_TTL = 600

_DISCOVER_FEED_MISSING = object()


def discover_feed_cache_key(user_id):
    """Return the cache key holding a user's precomputed discover feed."""
    return f"discover_feed_v1_{user_id}"


def discover_feed_pending_key(user_id):
    """Return the cache key marking a user's discover feed build as in flight."""
    return f"discover_feed_pending_v1_{user_id}"


def unfinished_collections_cache_key(user_id):
    """Return the cache key holding a user's precomputed home widget feed."""
    return f"unfinished_collections_v1_{user_id}"


def unfinished_collections_pending_key(user_id):
    """Return the cache key marking a user's home widget build as in flight."""
    return f"unfinished_collections_pending_v1_{user_id}"


# Media types with a usable trending/popularity source. Comics are excluded
# (ComicVine has no trending endpoint and a strict hourly rate limit) and so
# are books (neither Hardcover nor OpenLibrary exposes a reliable one).
TRENDING_MEDIA_TYPES = (
    MediaTypes.MOVIE.value,
    MediaTypes.TV.value,
    MediaTypes.ANIME.value,
    MediaTypes.MANGA.value,
    MediaTypes.GAME.value,
    MediaTypes.BOARDGAME.value,
)


def trending_feed_cache_key():
    """Return the cache key holding the global precomputed trending feed."""
    return "trending_feed_v1"


def trending_feed_pending_key():
    """Return the cache key marking the trending feed build as in flight."""
    return "trending_feed_pending_v1"


def get_owned_media_or_404(request, media_type, instance_id, *, prefetch=False):
    """Return media owned by the current user or raise 404."""
    try:
        if prefetch:
            return BasicMedia.objects.get_media_prefetch(
                request.user,
                media_type,
                instance_id,
            )
        return BasicMedia.objects.get_media(
            request.user,
            media_type,
            instance_id,
        )
    except ObjectDoesNotExist as exc:
        msg = "Media not found"
        raise Http404(msg) from exc


def get_configured_app_url():
    """Return the configured public application origin, if one is available."""
    for url in getattr(settings, "URLS", []):
        if url:
            return url.rstrip("/")

    base_url = getattr(settings, "BASE_URL", None)
    parsed_base_url = urlparse(base_url or "")
    if parsed_base_url.scheme and parsed_base_url.netloc:
        return base_url.rstrip("/")

    return None


def build_absolute_app_url(request, path):
    """Build an absolute URL using the configured public origin when possible."""
    parsed_path = urlparse(path)
    if parsed_path.scheme and parsed_path.netloc:
        return path

    configured_app_url = get_configured_app_url()
    if configured_app_url:
        return urljoin(f"{configured_app_url}/", path.lstrip("/"))

    if request is None:
        return None

    return request.build_absolute_uri(path)


def current_page_path(request):
    """Return the path+query of the page the browser is actually displaying.

    Components rendered as an htmx fragment target (e.g. Discover, the home
    "load more" grid, the "Continue the Story" widget) see their own fragment
    endpoint in request.get_full_path(), not the page the user is looking at.
    htmx sends the real page URL via the HX-Current-URL header, so prefer
    that when present.
    """
    hx_current_url = request.headers.get("HX-Current-URL")
    if not hx_current_url:
        return request.get_full_path()

    parsed = urlparse(hx_current_url)
    return f"{parsed.path}?{parsed.query}" if parsed.query else parsed.path


def minutes_to_hhmm(total_minutes):
    """Convert total minutes to HH:MM format."""
    hours = int(total_minutes / 60)
    minutes = int(total_minutes % 60)
    if hours == 0:
        return f"{minutes}min"
    return f"{hours}h {minutes:02d}min"


def redirect_back(request):
    """Redirect to the previous page, removing the 'page' parameter if present."""
    if url_has_allowed_host_and_scheme(request.GET.get("next"), None):
        next_url = request.GET["next"]

        # Parse the URL
        parsed_url = urlparse(next_url)

        # Get the query parameters and remove params we don't want
        query_params = dict(parse_qsl(parsed_url.query, keep_blank_values=True))
        query_params.pop("page", None)
        query_params.pop("load_media_type", None)

        # Reconstruct the URL
        new_query = urlencode(query_params)
        new_parts = list(parsed_url)
        new_parts[4] = new_query  # index 4 is the query part

        # Convert back to a URL string
        clean_url = iri_to_uri(parsed_url._replace(query=new_query).geturl())

        return HttpResponseRedirect(clean_url)

    return redirect("home")


def form_error_messages(form, request):
    """Display form errors as messages."""
    for field, errors in form.errors.items():
        for error in errors:
            messages.error(
                request,
                f"{field.replace('_', ' ').title()}: {error}",
            )


def format_search_response(page, per_page, total_results, results):
    """Format the search response for pagination."""
    return {
        "page": page,
        "total_results": total_results,
        "total_pages": total_results // per_page + 1,
        "results": results,
    }


def is_released_date(air_date, current_date=None):
    """Return whether the supplied air date has already passed."""
    current_date = current_date or timezone.localdate()
    normalized_air_date = None

    if isinstance(air_date, datetime):
        if timezone.is_naive(air_date):
            normalized_air_date = air_date.date()
        else:
            normalized_air_date = timezone.localtime(air_date).date()
    elif isinstance(air_date, date):
        normalized_air_date = air_date
    elif isinstance(air_date, str):
        parts = air_date.split("-")
        if len(parts) == YEAR_ONLY_PARTS:
            air_date = f"{air_date}-01-01"
        elif len(parts) == YEAR_MONTH_PARTS:
            air_date = f"{air_date}-01"

        try:
            normalized_air_date = date.fromisoformat(air_date)
        except ValueError:
            return False
    else:
        return False

    return normalized_air_date <= current_date


def _needs_image_refresh(item, new_image):
    """Return True when ``item.image`` should be replaced with ``new_image``."""
    if item is None or not new_image or new_image == settings.IMG_NONE:
        return False
    return not item.image or item.image == settings.IMG_NONE


def refresh_item_image_if_missing(item, new_image):
    """Update an Item's stored image when it's missing and a real one is available."""
    if not _needs_image_refresh(item, new_image):
        return
    item.image = new_image
    item.save(update_fields=["image"])


def _get_or_create_related_item(item, media_type):
    """Get or create the Item row for an untracked related/recommended entry.

    Related, recommended, and search-result items aren't necessarily tracked by
    anyone, but we still want their images cached locally like a tracked item's
    would be -- so give every one of them a backing Item row.
    """
    filter_kwargs = {
        "media_id": item["media_id"],
        "source": item["source"],
        "media_type": media_type,
    }
    if media_type == MediaTypes.SEASON.value:
        filter_kwargs["season_number"] = item.get("season_number")

    related_item, _created = Item.objects.get_or_create(
        **filter_kwargs,
        defaults={
            "title": item.get("title") or "",
            "image": item.get("image") or "",
        },
    )
    return related_item


def ensure_item_cached(item_dict, media_type):
    """Ensure a metadata dict's own image is cached locally, tracked or not.

    Item rows (and their cache) are shared across all users, so a page being
    viewed by someone who hasn't tracked it shouldn't skip the local cache just
    because *this* viewer has no tracking row for it. Mutates ``item_dict``'s
    "image" key to the local cache path when available.
    """
    cache_item = _get_or_create_related_item(item_dict, media_type)
    _localize_item_image(cache_item, item_dict)
    return cache_item


def _localize_item_image(cache_item, item_dict):
    """Point ``item_dict``'s image at the local cache, refreshing if missing."""
    if _needs_image_refresh(cache_item, item_dict.get("image")):
        cache_item.image = item_dict["image"]
        cache_item.image_cached = False
        cache_item.save(update_fields=["image", "image_cached"])
        cache_item_image.delay(cache_item.id, cache_item.image)

    item_dict["image"] = cache_item.cached_image_url


def _related_item_key(item, media_type):
    """Return the identity key used to match item dicts to Item rows."""
    key = (str(item["media_id"]), item["source"])
    if media_type == MediaTypes.SEASON.value:
        return (*key, item.get("season_number"))
    return key


def _partition_enrichable_items(user, items, media_type, section_name, media_lookup):
    """Split usable item dicts into (kept, untracked) for bulk enrichment.

    Drops entries with a blank title (some providers' crowdsourced data, e.g.
    Hardcover series entries, would otherwise produce an empty URL slug and a
    broken link) and completed recommendations the user chose to hide. The
    untracked subset gets its backing Item rows fetched/created in bulk rather
    than one get_or_create query per recommendation.
    """
    kept = []
    untracked = []
    for item in items:
        if not item.get("title"):
            continue

        key = _related_item_key(item, media_type)
        media_item = media_lookup.get(key)
        if _should_skip_completed_recommendation(user, section_name, media_item):
            continue

        kept.append((item, key, media_item))
        if media_item is None:
            untracked.append(item)

    return kept, untracked


def _bulk_get_or_create_related_items(items, media_type):
    """Return a {key: Item} map for item dicts, creating missing rows in bulk.

    Bulk counterpart of _get_or_create_related_item for the feed/search
    enrichment paths, which would otherwise run one get_or_create query per
    recommendation. Newly created rows get their image download queued
    explicitly, since bulk_create bypasses Item.save()'s tracker logic.
    """
    is_season = media_type == MediaTypes.SEASON.value

    wanted = {}
    for item in items:
        wanted.setdefault(_related_item_key(item, media_type), item)
    if not wanted:
        return {}

    def fetch_existing():
        rows = Item.objects.filter(
            media_type=media_type,
            media_id__in={key[0] for key in wanted},
        )
        found = {}
        for row in rows:
            key = (row.media_id, row.source)
            if is_season:
                key = (*key, row.season_number)
            if key in wanted:
                found[key] = row
        return found

    found = fetch_existing()
    missing = [
        Item(
            media_id=item["media_id"],
            source=item["source"],
            media_type=media_type,
            season_number=item.get("season_number") if is_season else None,
            title=item.get("title") or "",
            image=item.get("image") or "",
        )
        for key, item in wanted.items()
        if key not in found
    ]
    if missing:
        Item.objects.bulk_create(missing, ignore_conflicts=True)
        already_found = set(found)
        found = fetch_existing()
        for key, row in found.items():
            if (
                key not in already_found
                and row.image
                and row.image != settings.IMG_NONE
            ):
                cache_item_image.delay(row.id, row.image)

    return found


def enrich_items_with_user_data(user, items, section_name):
    """Enrich a list of items with user tracking data.

    Also ensures every item's image is cached locally (via a backing Item row),
    whether or not the item itself is tracked by anyone.
    """
    if not items:
        return []

    # All items are the same media type
    media_type = items[0]["media_type"]
    media_lookup = _build_user_media_lookup(user, items, media_type)

    kept, untracked = _partition_enrichable_items(
        user, items, media_type, section_name, media_lookup
    )
    related_lookup = _bulk_get_or_create_related_items(untracked, media_type)

    # Second pass: enrich with matched media
    enriched_items = []
    items_to_refresh = []
    for item, key, media_item in kept:
        cache_item = (
            media_item.item if media_item is not None else related_lookup[key]
        )

        if _needs_image_refresh(cache_item, item.get("image")):
            cache_item.image = item["image"]
            cache_item.image_cached = False
            items_to_refresh.append(cache_item)

        if media_item is None:
            item["image"] = cache_item.cached_image_url

        enriched_items.append({"item": item, "media": media_item})

    if items_to_refresh:
        Item.objects.bulk_update(items_to_refresh, ["image", "image_cached"])
        for refreshed_item in items_to_refresh:
            cache_item_image.delay(refreshed_item.id, refreshed_item.image)

    return enriched_items


def _build_user_media_lookup(user, items, media_type):
    """Fetch the user's media for ``items`` and return a {key: media} lookup."""
    source = items[0]["source"]

    q_objects = Q()
    for item in items:
        filter_params = {
            "item__media_id": item["media_id"],
            "item__media_type": media_type,
            "item__source": source,
        }
        if media_type == MediaTypes.SEASON.value:
            filter_params["item__season_number"] = item.get("season_number")
        q_objects |= Q(**filter_params)

    q_objects &= Q(user=user)

    model = apps.get_model(app_label="app", model_name=media_type)
    media_queryset = model.objects.filter(q_objects).select_related("item")
    media_queryset = BasicMedia.objects._apply_prefetch_related(
        media_queryset,
        media_type,
    )
    BasicMedia.objects.annotate_max_progress(media_queryset, media_type)

    media_lookup = {}
    for media in media_queryset:
        if media_type == MediaTypes.SEASON.value:
            key = (media.item.media_id, media.item.source, media.item.season_number)
        else:
            key = (media.item.media_id, media.item.source)
        media_lookup[key] = media

    return media_lookup


def _should_skip_completed_recommendation(user, section_name, media_item):
    """Return True when a completed recommendation should be hidden."""
    return (
        user.hide_completed_recommendations
        and section_name == "recommendations"
        and media_item is not None
        and media_item.status == Status.COMPLETED.value
    )


def _iter_untracked_related_entries(user, metadata, seen):
    """Yield unseen, untracked entries from an item's genuine follow-up sections."""
    for section_name, related_items in metadata.get("related", {}).items():
        if section_name in IGNORED_RELATED_SECTIONS or not related_items:
            continue

        enriched = enrich_items_with_user_data(user, related_items, section_name)
        for entry in enriched:
            if entry["media"] is not None:
                continue
            key = (entry["item"]["media_id"], entry["item"]["source"])
            if key in seen:
                continue
            seen.add(key)
            yield entry


def _get_related_metadata(services, media_type, item):
    """Fetch related metadata for an item, or None if the provider call fails."""
    try:
        return services.get_media_metadata(media_type, item.media_id, item.source)
    except (services.ProviderAPIError, requests.RequestException):
        logger.warning("Skipping related-metadata lookup for %s", item, exc_info=True)
        return None


def get_unfinished_collection_items(user):
    """Return untracked direct follow-ups to the user's recently completed media.

    Reuses each provider's cached "related" metadata (the same data already shown
    on an item's detail page) rather than a new recommendation source, so this only
    surfaces direct follow-ups (collections, related anime/manga, game expansions)
    and explicitly skips the generic "recommendations" section.
    """
    from app.providers import services  # noqa: PLC0415 avoid import cycle

    active_types = [
        media_type
        for media_type in UNFINISHED_COLLECTION_MEDIA_TYPES
        if media_type in user.get_active_media_types()
    ]

    suggestions = []
    seen = set()
    for media_type in active_types:
        seeds = BasicMedia.objects.get_media_list(
            user=user,
            media_type=media_type,
            status_filter=Status.COMPLETED.value,
            sort_filter="end_date",
        )[:UNFINISHED_COLLECTION_SEEDS_PER_TYPE]

        for seed in seeds:
            if seed.item.source == Sources.MANUAL.value:
                continue

            metadata = _get_related_metadata(services, media_type, seed.item)
            if metadata is None:
                continue

            for entry in _iter_untracked_related_entries(user, metadata, seen):
                suggestions.append(entry)
                if len(suggestions) >= UNFINISHED_COLLECTION_MAX_ITEMS:
                    return suggestions

    return suggestions


def _iter_discover_seeds(user, media_type):
    """Yield seed media for discovery: the user's completed and in-progress items."""
    for status in DISCOVER_SEED_STATUSES:
        seeds = BasicMedia.objects.get_media_list(
            user=user,
            media_type=media_type,
            status_filter=status,
            sort_filter="score",
        )[:DISCOVER_SEEDS_PER_TYPE]
        yield from seeds


def _get_discover_recommendation_items(related):
    """Return the items to treat as "recommendations" from a related dict.

    Most providers (TMDB, MAL, IGDB, MangaUpdates, ComicVine) have a real generic
    "recommendations" section. Hardcover and BGG don't -- for those, fall back to
    combining their direct-relation sections (series siblings, expansions) instead,
    so books and boardgames aren't excluded from Discover entirely.
    """
    if "recommendations" in related:
        return related["recommendations"]

    return [
        item
        for section_name, section_items in related.items()
        if section_name not in IGNORED_RELATED_SECTIONS
        for item in section_items
    ]


def _rank_discover_entries_for_type(services, user, media_type):
    """Return one media type's untracked recommendations, ranked by frequency."""
    counts = {}
    order = []
    for seed in _iter_discover_seeds(user, media_type):
        if seed.item.source == Sources.MANUAL.value:
            continue

        metadata = _get_related_metadata(services, media_type, seed.item)
        if metadata is None:
            continue

        recommendation_items = _get_discover_recommendation_items(
            metadata.get("related", {})
        )
        if not recommendation_items:
            continue

        enriched = enrich_items_with_user_data(
            user, recommendation_items, "recommendations"
        )
        for entry in enriched:
            if entry["media"] is not None:
                continue
            key = (entry["item"]["media_id"], entry["item"]["source"])
            if key not in counts:
                counts[key] = {"entry": entry, "count": 0}
                order.append(key)
            counts[key]["count"] += 1

    ranked_keys = sorted(order, key=lambda key: counts[key]["count"], reverse=True)
    return [counts[key]["entry"] for key in ranked_keys[:DISCOVER_MAX_ITEMS_PER_TYPE]]


def _interleave_discover_suggestions(per_type_suggestions):
    """Round-robin across media types so no type gets crowded out by another.

    Each type's own suggestions stay ranked by frequency (and already capped to
    DISCOVER_MAX_ITEMS_PER_TYPE), but a type with many overlapping recommendations
    (e.g. TV) can no longer push a lightly-tracked type (e.g. movies) out of the
    results entirely -- every type's items all eventually appear.
    """
    results = []
    indices = dict.fromkeys(per_type_suggestions, 0)
    remaining = True
    while remaining:
        remaining = False
        for media_type, suggestions in per_type_suggestions.items():
            index = indices[media_type]
            if index < len(suggestions):
                results.append(suggestions[index])
                indices[media_type] = index + 1
                remaining = True
    return results


def get_discover_recommendations(user):
    """Return a ranked, deduped, cross-type-balanced feed of untracked recommendations.

    Unlike get_unfinished_collection_items() (direct follow-ups), this aggregates
    each provider's generic "recommendations" section across the user's completed
    and in-progress, highest-scored media. Within a media type, suggestions are
    ranked by how many seed items recommend the same title; across types, results
    are interleaved round-robin (see _interleave_discover_suggestions) so every
    active type with any suggestions is represented in the final list.
    """
    from app.providers import services  # noqa: PLC0415 avoid import cycle

    active_types = [
        media_type
        for media_type in DISCOVER_MEDIA_TYPES
        if media_type in user.get_active_media_types()
    ]

    per_type_suggestions = {
        media_type: _rank_discover_entries_for_type(services, user, media_type)
        for media_type in active_types
    }

    return _interleave_discover_suggestions(per_type_suggestions)


def get_cached_discover_feed(user):
    """Return the user's precomputed discover feed, or None when absent.

    A computed-but-empty feed is a valid cache hit (it renders the empty
    state); only a genuinely missing key returns None. Cached entries are
    re-checked against the database -- no provider calls on this path.
    """
    return _get_cached_feed(user, discover_feed_cache_key(user.id))


def get_cached_unfinished_collections(user):
    """Return the user's precomputed "Continue the Story" feed, or None when absent.

    Same contract as get_cached_discover_feed: empty list is a valid hit (the
    widget renders nothing), None means not computed yet.
    """
    return _get_cached_feed(user, unfinished_collections_cache_key(user.id))


def _get_cached_feed(user, cache_key):
    """Fetch a cached feed and re-check it against the database, DB-only."""
    item_dicts = cache.get(cache_key, _DISCOVER_FEED_MISSING)
    if item_dicts is _DISCOVER_FEED_MISSING:
        return None

    kept = _refresh_cached_feed_items(user, item_dicts)
    return [{"item": item_dict, "media": None} for item_dict in kept]


def get_trending():
    """Compute the global trending feed: popular items across media types.

    The result is user-independent (the same for everyone); per-user concerns
    (hiding tracked items, disabled media types) are applied at serve time by
    get_cached_trending_feed. One provider being down only drops its own type.
    """
    from app.providers import services  # noqa: PLC0415 avoid import cycle

    per_type = {}
    for media_type in TRENDING_MEDIA_TYPES:
        try:
            items = services.trending(media_type)
        except (services.ProviderAPIError, requests.RequestException):
            logger.warning(
                "Skipping trending lookup for %s", media_type, exc_info=True
            )
            continue

        kept = [item for item in items if item.get("title")]
        cache_items = _bulk_get_or_create_related_items(kept, media_type)
        for item in kept:
            _localize_item_image(
                cache_items[_related_item_key(item, media_type)], item
            )
        if kept:
            per_type[media_type] = kept

    return _interleave_discover_suggestions(per_type)


def get_cached_trending_feed(user):
    """Return the trending feed filtered for this user, or None when absent.

    Drops media types the user has disabled and anything they already track;
    DB-only, no provider calls.
    """
    item_dicts = cache.get(trending_feed_cache_key(), _DISCOVER_FEED_MISSING)
    if item_dicts is _DISCOVER_FEED_MISSING:
        return None

    active_types = set(user.get_active_media_types())
    item_dicts = [
        item_dict
        for item_dict in item_dicts
        if item_dict["media_type"] in active_types
    ]
    kept = _refresh_cached_feed_items(user, item_dicts)
    return [{"item": item_dict, "media": None} for item_dict in kept]


def _refresh_cached_feed_items(user, item_dicts):
    """Drop since-tracked entries and re-resolve image URLs, DB-only.

    Between recomputes the user may track a suggestion (it must disappear from
    the feed) and the image-cache evictor may drop a poster (its local URL must
    fall back to the provider's), so both are re-derived on every render while
    preserving the feed's interleaved order.

    Tracked matching is by exact (media_type, media_id, source) plus a
    same-type title fallback: the same real-world item can be tracked under a
    different source than the feed suggests it from (a manually-added game vs
    its IGDB trending entry, a MangaUpdates manga vs MAL trending), and exact
    keys can never match across sources.
    """
    by_type = {}
    for item_dict in item_dicts:
        by_type.setdefault(item_dict["media_type"], []).append(item_dict)

    tracked = set()
    tracked_titles = set()
    images = {}
    for media_type, type_items in by_type.items():
        media_ids = [str(item_dict["media_id"]) for item_dict in type_items]
        model = apps.get_model("app", media_type)
        tracked.update(
            (media_type, media_id, source)
            for media_id, source in model.objects.filter(
                user=user,
                item__media_id__in=media_ids,
            ).values_list("item__media_id", "item__source")
        )
        tracked_titles.update(
            (media_type, title.casefold())
            for title in model.objects.filter(user=user).values_list(
                "item__title",
                flat=True,
            )
            if title
        )
        for row in Item.objects.filter(
            media_type=media_type,
            media_id__in=media_ids,
        ):
            images[(media_type, row.media_id, row.source)] = row.cached_image_url

    kept = []
    for item_dict in item_dicts:
        key = (
            item_dict["media_type"],
            str(item_dict["media_id"]),
            item_dict["source"],
        )
        if key in tracked:
            continue
        title = item_dict.get("title") or ""
        if (item_dict["media_type"], title.casefold()) in tracked_titles:
            continue
        if key in images:
            item_dict["image"] = images[key]
        kept.append(item_dict)
    return kept
