import hashlib
import ipaddress
import logging
import socket
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

import requests
from celery import shared_task
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils import timezone
from PIL import Image, UnidentifiedImageError

from app.models import UserMessage

logger = logging.getLogger(__name__)


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
    """Reject non-http(s) schemes and hostnames resolving to private/internal addresses."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    try:
        addr = socket.gethostbyname(parsed.hostname)
        ip = ipaddress.ip_address(addr)
    except (OSError, ValueError):
        return False
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
    )


@shared_task(name="Cache item image", bind=True, max_retries=2, default_retry_delay=30)
def cache_item_image(self, item_id, image_url):
    """Download an Item's external image and store it locally as a normalized WebP."""
    from app.models import ImageCacheFormat, Item  # noqa: PLC0415 avoid import cycle

    if not _is_safe_image_host(image_url):
        logger.warning("Refusing to cache image for item %s: unsafe URL", item_id)
        return False

    try:
        response = requests.get(
            image_url,
            timeout=settings.IMAGE_DOWNLOAD_TIMEOUT,
            stream=True,
            allow_redirects=False,
        )

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
        buffer.seek(0)

        try:
            image = Image.open(buffer)
            image.verify()
            buffer.seek(0)
            image = Image.open(buffer)
            if image.mode != "RGB":
                image = image.convert("RGB")
        except UnidentifiedImageError:
            logger.warning(
                "Refusing to cache image for item %s: not a valid image",
                item_id,
            )
            return False

        digest = hashlib.sha256(image_url.encode()).hexdigest()
        relpath = Path(settings.IMAGE_CACHE_DIR) / digest[:2] / f"{digest}.webp"

        out_buffer = BytesIO()
        image.save(out_buffer, format="WEBP", quality=85)
        default_storage.save(str(relpath), ContentFile(out_buffer.getvalue()))

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
    Item.objects.filter(id=item_id).update(image_cache_format=ImageCacheFormat.WEBP.value)

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
    """Return (total_size_bytes, file_count) for the current image cache."""
    total_size = 0
    file_count = 0
    for relpath, _digest in _iter_cached_image_files():
        total_size += default_storage.size(relpath)
        file_count += 1
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

    logger.info(
        "Evicted %s cached item images (%s bytes freed) to stay under the %s byte cap.",
        deleted_count,
        bytes_freed,
        max_bytes,
    )

    return deleted_count
