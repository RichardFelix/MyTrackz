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
    """Download an Item's external image and store it locally as a normalized JPEG."""
    from app.models import Item  # noqa: PLC0415 avoid import cycle

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
        relpath = Path(settings.IMAGE_CACHE_DIR) / digest[:2] / f"{digest}.jpg"

        out_buffer = BytesIO()
        image.save(out_buffer, format="JPEG", quality=85)
        default_storage.save(str(relpath), ContentFile(out_buffer.getvalue()))

    except (requests.exceptions.RequestException, OSError) as exc:
        logger.warning("Failed to cache image for item %s: %s", item_id, exc)
        raise self.retry(exc=exc) from exc
    else:
        Item.objects.filter(id=item_id, image=image_url).update(image_cached=True)
        return True


@shared_task(name="Cleanup orphaned item images")
def cleanup_orphaned_item_images():
    """Delete cached image files that no longer match any Item's current URL."""
    from app.models import Item  # noqa: PLC0415 avoid import cycle

    live_paths = set()
    for image in Item.objects.exclude(image="").values_list("image", flat=True):
        digest = hashlib.sha256(image.encode()).hexdigest()
        live_paths.add(f"{settings.IMAGE_CACHE_DIR}/{digest[:2]}/{digest}.jpg")

    try:
        shard_dirs, _ = default_storage.listdir(settings.IMAGE_CACHE_DIR)
    except FileNotFoundError:
        return 0

    deleted_count = 0
    for shard in shard_dirs:
        shard_path = f"{settings.IMAGE_CACHE_DIR}/{shard}"
        _, filenames = default_storage.listdir(shard_path)
        for filename in filenames:
            relpath = f"{shard_path}/{filename}"
            if relpath not in live_paths:
                default_storage.delete(relpath)
                deleted_count += 1

    logger.info("Deleted %s orphaned cached item images.", deleted_count)

    return deleted_count
