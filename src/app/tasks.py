import hashlib
import logging
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlsplit

import requests
from celery import shared_task
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils import timezone
from PIL import Image, UnidentifiedImageError

from app.image_download import get_image, host_resolves_publicly
from app.models import UserMessage

logger = logging.getLogger(__name__)

# A browser-like UA plus a same-origin Referer gets past the hotlink blocks some
# image hosts apply to bare requests (e.g. HowLongToBeat 403s, pbs.twimg.com).
_BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def _browser_image_headers(url):
    """Return browser-like request headers for fetching an image from ``url``.

    We deliberately don't advertise AVIF in Accept — some CDNs would then serve
    AVIF, which our allowlist/PIL can't read.
    """
    parts = urlsplit(url)
    return {
        "User-Agent": _BROWSER_UA,
        "Accept": "image/webp,image/jpeg,image/png,image/gif,*/*;q=0.8",
        "Referer": f"{parts.scheme}://{parts.netloc}/",
    }


@shared_task(name="Cleanup user messages")
def cleanup_user_messages():
    """Delete shown user messages older than the configured retention window."""
    cutoff = timezone.now() - timedelta(days=settings.USER_MESSAGE_RETENTION_DAYS)
    deleted_count, _ = UserMessage.objects.filter(
        shown_at__isnull=False,
        shown_at__lt=cutoff,
    ).delete()

    logger.info("Deleted %s old shown user messages.", deleted_count)

    return deleted_count


def _is_safe_image_host(url):
    """Reject non-HTTP schemes and private or internal addresses."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    try:
        port = parsed.port
    except ValueError:
        return False
    return host_resolves_publicly(parsed.hostname, port)


# Errors Pillow raises for bytes that aren't a usable image: unknown formats,
# truncated/corrupt files (OSError, SyntaxError, ValueError) and images whose
# pixel count trips the decompression-bomb guard. None of them is transient,
# so callers treat them as "not an image" instead of retrying.
INVALID_IMAGE_ERRORS = (
    UnidentifiedImageError,
    Image.DecompressionBombError,
    OSError,
    SyntaxError,
    ValueError,
)


def decode_image(data):
    """Decode downloaded bytes into an RGB image, or None when they're unusable.

    ``verify()`` catches corruption cheaply but leaves the image unusable, so
    the bytes are opened a second time for the real decode.
    """
    try:
        Image.open(BytesIO(data)).verify()
        image = Image.open(BytesIO(data))
        image.load()
        if image.mode != "RGB":
            image = image.convert("RGB")
    except INVALID_IMAGE_ERRORS:
        return None
    return image


def encode_cached_image(image):
    """Shrink an item image to the cache's display size and encode it as WebP."""
    image.thumbnail(settings.IMAGE_CACHE_MAX_DIMENSIONS)
    out_buffer = BytesIO()
    image.save(out_buffer, format="WEBP", quality=85)
    return out_buffer.getvalue()


def save_cached_file(relpath, data):
    """Write a cache file at exactly ``relpath``, replacing any existing copy.

    Storage would otherwise append a random suffix to an existing name,
    leaving a duplicate that nothing references.
    """
    if default_storage.exists(relpath):
        default_storage.delete(relpath)
    default_storage.save(relpath, ContentFile(data))


IMAGE_CACHE_STATS_KEY = "image_cache_stats_v1"
IMAGE_CACHE_STATS_TTL = 60 * 10


def _invalidate_image_cache_stats():
    """Drop the cached size/count so Settings shows fresh numbers."""
    from django.core.cache import cache  # noqa: PLC0415 avoid import cycle

    cache.delete(IMAGE_CACHE_STATS_KEY)


def _safe_image_get(url, *, timeout, headers=None, max_redirects=5):
    """GET an image URL, following redirects while re-validating each hop's host.

    Some providers don't serve the image bytes directly: OpenLibrary answers a
    cover request with ``302 -> archive.org``, so redirects have to be followed or
    those images never download. But ``requests``' built-in redirect following would
    chase a hop into a private/internal host before we could vet it (SSRF). So we
    follow manually, running every URL in the chain (including the first) through
    ``_is_safe_image_host`` before requesting it.

    Returns the final, non-redirect streaming ``Response``, or ``None`` when a hop
    is unsafe, no ``Location`` is given, or the chain exceeds ``max_redirects``.
    """
    for _ in range(max_redirects + 1):
        if not _is_safe_image_host(url):
            return None
        response = get_image(
            url,
            timeout=timeout,
            stream=True,
            allow_redirects=False,
            headers=headers,
        )
        if not response.is_redirect:
            return response
        location = response.headers.get("Location")
        response.close()
        if not location:
            return None
        url = urljoin(url, location)
    return None


@shared_task(name="Cache item image", bind=True, max_retries=2, default_retry_delay=30)
def cache_item_image(self, item_id, image_url):
    """Download an Item's external image and store it locally as a normalized WebP."""
    from app.models import ImageCacheFormat, Item  # noqa: PLC0415 avoid import cycle

    try:
        response = _safe_image_get(
            image_url,
            timeout=settings.IMAGE_DOWNLOAD_TIMEOUT,
            headers=_browser_image_headers(image_url),
        )
        if response is None:
            logger.warning(
                "Refusing to cache image for item %s: unsafe URL or redirect chain",
                item_id,
            )
            return False

        if response.status_code != requests.codes.ok:
            logger.warning(
                "Refusing to cache image for item %s: status %s",
                item_id,
                response.status_code,
            )
            return False

        content_type = response.headers.get("Content-Type", "").split(";")[0].strip()
        if content_type not in settings.IMAGE_ALLOWED_CONTENT_TYPES:
            logger.warning(
                "Refusing to cache image for item %s: Content-Type %r",
                item_id,
                content_type,
            )
            return False

        buffer = BytesIO()
        downloaded = 0
        for chunk in response.iter_content(chunk_size=64 * 1024):
            downloaded += len(chunk)
            if downloaded > settings.IMAGE_DOWNLOAD_MAX_BYTES:
                logger.warning(
                    "Refusing to cache image for item %s: exceeded size cap",
                    item_id,
                )
                return False
            buffer.write(chunk)

        image = decode_image(buffer.getvalue())
        if image is None:
            logger.warning(
                "Refusing to cache image for item %s: not a valid image",
                item_id,
            )
            return False

        digest = hashlib.sha256(image_url.encode()).hexdigest()
        relpath = Path(settings.IMAGE_CACHE_DIR) / digest[:2] / f"{digest}.webp"
        save_cached_file(str(relpath), encode_cached_image(image))

    except (requests.exceptions.RequestException, OSError) as exc:
        logger.warning("Failed to cache image for item %s: %s", item_id, exc)
        raise self.retry(exc=exc) from exc
    else:
        Item.objects.filter(id=item_id, image=image_url).update(
            image_cached=True,
            image_cache_format=ImageCacheFormat.WEBP.value,
        )
        return True


@shared_task(name="Transcode cached image to WebP")
def transcode_cached_image_to_webp(item_id):
    """Locally re-encode an Item's already-cached JPEG as WebP; no network involved."""
    from app.models import ImageCacheFormat, Item  # noqa: PLC0415 avoid import cycle

    try:
        item = Item.objects.get(
            id=item_id,
            image_cached=True,
            image_cache_format=ImageCacheFormat.JPEG.value,
        )
    except Item.DoesNotExist:
        return False

    digest = hashlib.sha256(item.image.encode()).hexdigest()
    old_relpath = f"{settings.IMAGE_CACHE_DIR}/{digest[:2]}/{digest}.jpg"
    new_relpath = f"{settings.IMAGE_CACHE_DIR}/{digest[:2]}/{digest}.webp"

    if not default_storage.exists(old_relpath):
        return False

    try:
        with default_storage.open(old_relpath, "rb") as source_file:
            image = Image.open(source_file)
            image.load()
            if image.mode != "RGB":
                image = image.convert("RGB")
            out_buffer = BytesIO()
            image.save(out_buffer, format="WEBP", quality=85)
    except (OSError, UnidentifiedImageError):
        logger.warning(
            "Skipping WebP transcode for item %s: unreadable cached file", item_id
        )
        return False

    default_storage.save(new_relpath, ContentFile(out_buffer.getvalue()))
    default_storage.delete(old_relpath)
    Item.objects.filter(id=item_id).update(
        image_cache_format=ImageCacheFormat.WEBP.value
    )

    return True


def _iter_cached_image_files():
    """Yield (relpath, digest) for every file currently in the image cache."""
    try:
        shard_dirs, _ = default_storage.listdir(settings.IMAGE_CACHE_DIR)
    except FileNotFoundError:
        return

    for shard in shard_dirs:
        shard_path = f"{settings.IMAGE_CACHE_DIR}/{shard}"
        _, filenames = default_storage.listdir(shard_path)
        for filename in filenames:
            yield f"{shard_path}/{filename}", Path(filename).stem


def get_image_cache_stats():
    """Return (total_size_bytes, file_count) for the current image cache.

    Walking the cache stats every file, so the result is kept for a few
    minutes; purge and eviction drop it so their effect shows immediately.
    """
    from django.core.cache import cache  # noqa: PLC0415 avoid import cycle

    stats = cache.get(IMAGE_CACHE_STATS_KEY)
    if stats is not None:
        return tuple(stats)

    total_size = 0
    file_count = 0
    for relpath, _digest in _iter_cached_image_files():
        total_size += default_storage.size(relpath)
        file_count += 1
    cache.set(IMAGE_CACHE_STATS_KEY, (total_size, file_count), IMAGE_CACHE_STATS_TTL)
    return total_size, file_count


@shared_task(name="Purge cached images")
def purge_cached_images():
    """Delete every cached image file and reset image_cached on all Items."""
    from app.models import Item  # noqa: PLC0415 avoid import cycle

    deleted_count = 0
    for relpath, _digest in _iter_cached_image_files():
        default_storage.delete(relpath)
        deleted_count += 1

    Item.objects.filter(image_cached=True).update(image_cached=False)
    _invalidate_image_cache_stats()

    logger.info("Purged %s cached item images.", deleted_count)

    return deleted_count


@shared_task(name="Cleanup orphaned item images")
def cleanup_orphaned_item_images():
    """Delete cached image files that no longer match any Item's current URL."""
    from app.models import Item  # noqa: PLC0415 avoid import cycle

    live_digests = {
        hashlib.sha256(image.encode()).hexdigest()
        for image in Item.objects.exclude(image="").values_list("image", flat=True)
    }

    deleted_count = 0
    for relpath, digest in _iter_cached_image_files():
        if digest not in live_digests:
            default_storage.delete(relpath)
            deleted_count += 1

    logger.info("Deleted %s orphaned cached item images.", deleted_count)

    return deleted_count


@shared_task(name="Evict oversized image cache")
def evict_oversized_image_cache():
    """Delete the oldest cached images until the cache is back under its size cap."""
    from app.models import Item  # noqa: PLC0415 avoid import cycle

    max_bytes = settings.IMAGE_CACHE_MAX_BYTES
    if max_bytes <= 0:
        return 0

    files = []
    total_size = 0
    for relpath, digest in _iter_cached_image_files():
        size = default_storage.size(relpath)
        modified_time = default_storage.get_modified_time(relpath)
        files.append((modified_time, relpath, digest, size))
        total_size += size

    if total_size <= max_bytes:
        return 0

    digest_to_item_ids = {}
    for item_id, image_url in Item.objects.filter(image_cached=True).values_list(
        "id", "image"
    ):
        item_digest = hashlib.sha256(image_url.encode()).hexdigest()
        digest_to_item_ids.setdefault(item_digest, []).append(item_id)

    files.sort(key=lambda entry: entry[0])  # oldest cached first

    deleted_count = 0
    bytes_freed = 0
    for _modified_time, relpath, digest, size in files:
        if total_size <= max_bytes:
            break

        default_storage.delete(relpath)
        total_size -= size
        bytes_freed += size
        deleted_count += 1

        stale_item_ids = digest_to_item_ids.get(digest)
        if stale_item_ids:
            Item.objects.filter(id__in=stale_item_ids).update(image_cached=False)

    _invalidate_image_cache_stats()
    logger.info(
        "Evicted %s cached item images (%s bytes freed) to stay under the %s byte cap.",
        deleted_count,
        bytes_freed,
        max_bytes,
    )

    return deleted_count


def _clear_owned_pending_marker(pending_key, build_id):
    """Delete a feed's pending marker only when this build set it.

    A build queued without a build_id (the daily beat fan-outs) owns no
    marker, so it must never delete one -- otherwise it can wipe the marker
    of a user-triggered refresh that's still in flight and let polling queue
    duplicate builds. Unowned markers simply expire (polling checks the feed
    key first, so a lingering marker only suppresses duplicate queueing).
    """
    from django.core.cache import cache  # noqa: PLC0415 avoid import cycle

    if build_id is not None and cache.get(pending_key) == build_id:
        cache.delete(pending_key)


def _compute_user_feed(user_id, label, compute, cache_key, pending_key, ttl, build_id):
    """Compute one user's feed and cache it, clearing our own pending marker."""
    from django.contrib.auth import get_user_model  # noqa: PLC0415 avoid import cycle
    from django.core.cache import cache  # noqa: PLC0415 avoid import cycle

    user_model = get_user_model()
    try:
        user = user_model.objects.get(pk=user_id)
    except user_model.DoesNotExist:
        logger.info("Skipping %s for deleted user %s", label, user_id)
        _clear_owned_pending_marker(pending_key, build_id)
        return 0

    try:
        suggestions = compute(user)
        cache.set(
            cache_key,
            [suggestion["item"] for suggestion in suggestions],
            ttl,
        )
    finally:
        _clear_owned_pending_marker(pending_key, build_id)

    logger.info(
        "Cached %s for user %s (%s suggestions)",
        label,
        user_id,
        len(suggestions),
    )
    return len(suggestions)


def _fan_out_per_user(compute_task, **user_filters):
    """Queue a per-user feed rebuild for every active user matching the filters."""
    from django.contrib.auth import get_user_model  # noqa: PLC0415 avoid import cycle

    user_ids = list(
        get_user_model()
        .objects.filter(is_active=True, **user_filters)
        .values_list("id", flat=True)
    )
    for user_id in user_ids:
        compute_task.delay(user_id)

    return len(user_ids)


@shared_task(name="Compute discover feed")
def compute_discover_feed(user_id, build_id=None):
    """Compute one user's discover feed and cache it for instant page loads."""
    from app import helpers  # noqa: PLC0415 helpers imports app.tasks at module level

    return _compute_user_feed(
        user_id,
        "discover feed",
        helpers.get_discover_recommendations,
        helpers.discover_feed_cache_key(user_id),
        helpers.discover_feed_pending_key(user_id),
        helpers.DISCOVER_FEED_TTL,
        build_id,
    )


@shared_task(name="Refresh discover feeds")
def refresh_discover_feeds():
    """Queue a discover feed rebuild for every active user."""
    return _fan_out_per_user(compute_discover_feed)


@shared_task(name="Compute unfinished collections")
def compute_unfinished_collections(user_id, build_id=None):
    """Compute one user's "Continue the Story" feed and cache it."""
    from app import helpers  # noqa: PLC0415 helpers imports app.tasks at module level

    return _compute_user_feed(
        user_id,
        "unfinished collections",
        helpers.get_unfinished_collection_items,
        helpers.unfinished_collections_cache_key(user_id),
        helpers.unfinished_collections_pending_key(user_id),
        helpers.DISCOVER_FEED_TTL,
        build_id,
    )


@shared_task(name="Refresh unfinished collections")
def refresh_unfinished_collections():
    """Queue a "Continue the Story" rebuild for users who show the section."""
    return _fan_out_per_user(compute_unfinished_collections, show_continue_story=True)


@shared_task(name="Compute trending feed")
def compute_trending_feed(build_id=None):
    """Compute the global trending feed and cache it for instant page loads."""
    from django.core.cache import cache  # noqa: PLC0415 avoid import cycle

    from app import helpers  # noqa: PLC0415 helpers imports app.tasks at module level

    try:
        items = helpers.get_trending()
        cache.set(
            helpers.trending_feed_cache_key(),
            items,
            helpers.DISCOVER_FEED_TTL,
        )
    finally:
        _clear_owned_pending_marker(helpers.trending_feed_pending_key(), build_id)

    logger.info("Cached trending feed (%s items)", len(items))
    return len(items)


# A provider that has no genres/format for an item will keep having none, so
# an empty-handed backfill lookup is remembered and not repeated every week.
BACKFILL_RECHECK_SECONDS = 60 * 60 * 24 * 90


def _backfill_checked_key(kind, item_id):
    """Return the cache key marking an item's backfill lookup as come up empty."""
    return f"backfill_checked_v1_{kind}_{item_id}"


def _skip_recently_checked(kind, items):
    """Drop items whose last backfill lookup found nothing to capture."""
    from django.core.cache import cache  # noqa: PLC0415 avoid import cycle

    checked = cache.get_many(
        [_backfill_checked_key(kind, item.id) for item in items],
    )
    return [
        item for item in items if _backfill_checked_key(kind, item.id) not in checked
    ]


def _mark_backfill_checked(kind, item):
    """Remember that the provider had nothing to backfill for this item."""
    from django.core.cache import cache  # noqa: PLC0415 avoid import cycle

    cache.set(_backfill_checked_key(kind, item.id), 1, BACKFILL_RECHECK_SECONDS)


@shared_task(name="Backfill item genres")
def backfill_item_genres():
    """Populate genres for tracked items that don't have any yet.

    Genres normally get captured when a details page is visited; this fills
    the gaps (imports, items never opened). One metadata call per item,
    throttled by the shared rate-limited provider session. Seasons and
    episodes are skipped - genres live on the parent show's item.
    """
    from django.db.models import Q  # noqa: PLC0415 avoid import cycle

    from app.models import (  # noqa: PLC0415 avoid import cycle
        Item,
        MediaTypes,
        Sources,
    )
    from app.providers import services  # noqa: PLC0415 avoid import cycle

    media_types = [
        choice.value
        for choice in MediaTypes
        if choice not in [MediaTypes.SEASON, MediaTypes.EPISODE]
    ]
    query = Q()
    for media_type in media_types:
        query |= Q(**{f"{media_type}__isnull": False})
    query &= ~Q(source=Sources.MANUAL.value)

    items = _skip_recently_checked(
        "genres",
        list(Item.objects.filter(query, genres__isnull=True).distinct()),
    )

    filled = 0
    failed = 0
    for item in items:
        try:
            metadata = services.get_media_metadata(
                item.media_type,
                item.media_id,
                item.source,
                [item.season_number],
            )
            item.set_genres_from_metadata(metadata)
        except Exception:  # noqa: BLE001 one item's failure shouldn't kill the run
            failed += 1
            logger.warning("Genre backfill failed for %s", item, exc_info=True)
            continue
        if item.genres.exists():
            filled += 1
        else:
            _mark_backfill_checked("genres", item)

    msg = f"Genre backfill: {filled} filled, {failed} failed, {len(items)} candidates"
    logger.info(msg)
    return msg


@shared_task(name="Backfill item formats")
def backfill_item_formats():
    """Populate the release format for tracked anime that don't have one yet.

    Formats normally get captured when an item is tracked or its details page
    is visited; this fills the gaps (imports, items tracked before the field
    existed). Anime-only: the format only drives anime UI (movie checkmark,
    list-page filter), so other types aren't worth a metadata call. One call
    per item, throttled by the shared rate-limited provider session.
    """
    from django.db.models import Q  # noqa: PLC0415 avoid import cycle

    from app.models import Item, Sources  # noqa: PLC0415 avoid import cycle
    from app.providers import services  # noqa: PLC0415 avoid import cycle

    items = _skip_recently_checked(
        "format",
        list(
            Item.objects.filter(
                ~Q(source=Sources.MANUAL.value),
                anime__isnull=False,
                media_format="",
            ).distinct(),
        ),
    )

    filled = 0
    failed = 0
    for item in items:
        try:
            metadata = services.get_media_metadata(
                item.media_type,
                item.media_id,
                item.source,
                [item.season_number],
            )
            item.set_format_from_metadata(metadata)
        except Exception:  # noqa: BLE001 one item's failure shouldn't kill the run
            failed += 1
            logger.warning("Format backfill failed for %s", item, exc_info=True)
            continue
        if item.media_format:
            filled += 1
        else:
            _mark_backfill_checked("format", item)

    msg = f"Format backfill: {filled} filled, {failed} failed, {len(items)} candidates"
    logger.info(msg)
    return msg


PRUNE_DELETE_BATCH_SIZE = 500


def _feed_item_keys():
    """Return (media_type, media_id, source) for every item in a cached feed.

    Cached Discover/Trending/"Continue the Story" entries point at their Item
    row's local image, so those rows have to outlive the feed that shows them.
    """
    from django.contrib.auth import get_user_model  # noqa: PLC0415 avoid import cycle
    from django.core.cache import cache  # noqa: PLC0415 avoid import cycle

    from app import helpers  # noqa: PLC0415 helpers imports app.tasks at module level

    cache_keys = [helpers.trending_feed_cache_key()]
    for user_id in get_user_model().objects.values_list("id", flat=True):
        cache_keys.append(helpers.discover_feed_cache_key(user_id))
        cache_keys.append(helpers.unfinished_collections_cache_key(user_id))

    keys = set()
    for item_dicts in cache.get_many(cache_keys).values():
        for item_dict in item_dicts or []:
            keys.add(
                (
                    item_dict.get("media_type"),
                    str(item_dict.get("media_id")),
                    item_dict.get("source"),
                ),
            )
    return keys


@shared_task(name="Prune unreferenced items")
def prune_unreferenced_items():
    """Delete Item rows that nothing uses any more.

    Every search result, recommendation and details view creates an Item row
    (and caches its image) whether or not anyone goes on to track it, so the
    table only ever grows. A row is safe to drop when nothing references it:
    no tracked media of any type, no episode, no calendar event, no custom
    list, no notification exclusion. Rows with a user-set custom image and
    rows shown in a currently cached feed are kept. Anything pruned is simply
    recreated the next time it's searched for or viewed; its cached image file
    is removed by the orphaned-image cleanup.
    """
    from django.db.models import Exists, OuterRef  # noqa: PLC0415 avoid import cycle

    from app.models import Item  # noqa: PLC0415 avoid import cycle

    candidates = Item.objects.filter(image_locked=False)
    for relation in Item._meta.related_objects:
        references = relation.related_model._default_manager.filter(
            **{relation.field.name: OuterRef("pk")},
        )
        candidates = candidates.filter(~Exists(references))

    feed_keys = _feed_item_keys()
    prunable_ids = [
        item_id
        for item_id, media_type, media_id, source in candidates.order_by().values_list(
            "id",
            "media_type",
            "media_id",
            "source",
        )
        if (media_type, media_id, source) not in feed_keys
    ]

    deleted_count = 0
    for start in range(0, len(prunable_ids), PRUNE_DELETE_BATCH_SIZE):
        batch = prunable_ids[start : start + PRUNE_DELETE_BATCH_SIZE]
        # re-apply the filter at delete time: a row may have been tracked
        # since the candidate list was built
        deleted_count += (
            candidates.filter(id__in=batch)
            .delete()[1]
            .get(
                Item._meta.label,
                0,
            )
        )

    logger.info("Pruned %s unreferenced items.", deleted_count)
    return deleted_count


@shared_task(name="Cache profile image")
def cache_user_profile_image(user_id, image_url, crop=""):
    """Download a user's profile image off the request path and mark it cached.

    Until this lands (or if the fetch fails) the profile shows the raw URL.
    """
    from django.contrib.auth import get_user_model  # noqa: PLC0415 avoid import cycle

    from users.helpers import cache_profile_image  # noqa: PLC0415 avoid import cycle

    if not cache_profile_image(user_id, image_url, crop):
        logger.warning("Could not cache profile image for user %s", user_id)
        return False

    # only flag it when the user hasn't changed the image again meanwhile
    updated = (
        get_user_model()
        .objects.filter(id=user_id, image=image_url, image_crop=crop)
        .update(image_cached=True)
    )
    return bool(updated)
