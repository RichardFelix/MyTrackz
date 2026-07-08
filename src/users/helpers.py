import json
import zoneinfo
from datetime import datetime

import croniter
from django.utils import timezone

import integrations

# A browser-like UA avoids blanket blocks from some image CDNs (e.g. Twitter's
# pbs.twimg.com). Retries smooth over transient timeouts since profile-image
# caching runs synchronously inside the user's save request.
_IMAGE_FETCH_ATTEMPTS = 3
_IMAGE_FETCH_BACKOFF = 0.5
_BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def _download_image_bytes(image_url):
    """Fetch an external image (with retries) and return raw bytes, or None.

    Reuses the same SSRF guard, content-type allowlist and size cap as the item
    image cache.
    """
    import time  # noqa: PLC0415
    from io import BytesIO  # noqa: PLC0415
    from urllib.parse import urlsplit  # noqa: PLC0415

    import requests  # noqa: PLC0415
    from django.conf import settings  # noqa: PLC0415

    from app.tasks import _safe_image_get  # noqa: PLC0415

    # A same-origin Referer gets past most hotlink blocks, and retrying transient
    # statuses handles CDNs that challenge the first request (Cloudflare, magnific,
    # twimg, ...) before serving the image. We deliberately don't advertise AVIF in
    # Accept — some CDNs would then serve AVIF, which our allowlist/PIL can't read.
    parts = urlsplit(image_url)
    headers = {
        "User-Agent": _BROWSER_UA,
        "Accept": "image/webp,image/jpeg,image/png,image/gif,*/*;q=0.8",
        "Referer": f"{parts.scheme}://{parts.netloc}/",
    }
    retryable_status = {403, 429, 500, 502, 503, 504}

    # Each attempt covers the whole fetch AND streamed read, so a mid-download
    # connection drop retries too (larger images fail this way more often).
    for attempt in range(_IMAGE_FETCH_ATTEMPTS):
        try:
            response = _safe_image_get(
                image_url,
                timeout=settings.IMAGE_DOWNLOAD_TIMEOUT,
                headers=headers,
            )
            if response is None:
                return None
            if response.status_code == requests.codes.ok:
                content_type = (
                    response.headers.get("Content-Type", "").split(";")[0].strip()
                )
                if content_type not in settings.IMAGE_ALLOWED_CONTENT_TYPES:
                    return None
                buffer = BytesIO()
                downloaded = 0
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    downloaded += len(chunk)
                    if downloaded > settings.IMAGE_DOWNLOAD_MAX_BYTES:
                        return None
                    buffer.write(chunk)
                return buffer.getvalue()
            if response.status_code not in retryable_status:
                return None
        except requests.exceptions.RequestException:
            pass
        if attempt < _IMAGE_FETCH_ATTEMPTS - 1:
            time.sleep(_IMAGE_FETCH_BACKOFF)

    return None


def _apply_circle_crop(image, crop):
    """Crop ``image`` to the square selected by a "left,top,side" fraction string.

    ``left``/``top``/``side`` are fractions (0-1) of the source width/height, as
    produced by the account cropper. Returns a square image resized to at most
    512px; a blank or malformed crop returns the image unchanged.
    """
    if not crop:
        return image
    try:
        left_f, top_f, side_f = (float(v) for v in crop.split(","))
    except (ValueError, AttributeError):
        return image
    if side_f <= 0:
        return image

    width, height = image.size
    side = side_f * width
    left = min(max(0.0, left_f * width), width)
    top = min(max(0.0, top_f * height), height)
    right = min(left + side, width)
    bottom = min(top + side, height)
    image = image.crop((round(left), round(top), round(right), round(bottom)))

    max_side = 512
    if image.width > max_side:
        image = image.resize((max_side, max_side))
    return image


def cache_profile_image(user_id, image_url, crop=""):
    """Download a user's profile image and store it locally as WebP.

    ``crop`` is an optional "left,top,side" fraction string selecting a square
    region. The stored file is keyed per user so two users pasting the same URL
    don't share (and clobber) one another's crop. Returns True on success; on
    any failure the caller should leave the image uncached so the raw URL is
    used as a fallback.
    """
    import hashlib  # noqa: PLC0415
    from io import BytesIO  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    from django.conf import settings  # noqa: PLC0415
    from django.core.files.base import ContentFile  # noqa: PLC0415
    from django.core.files.storage import default_storage  # noqa: PLC0415
    from PIL import Image, UnidentifiedImageError  # noqa: PLC0415

    data = _download_image_bytes(image_url)
    if data is None:
        return False

    try:
        buffer = BytesIO(data)
        image = Image.open(buffer)
        image.verify()
        buffer.seek(0)
        image = Image.open(buffer)
        if image.mode != "RGB":
            image = image.convert("RGB")

        image = _apply_circle_crop(image, crop)

        digest = hashlib.sha256(f"{user_id}:{image_url}".encode()).hexdigest()
        relpath = str(Path(settings.PROFILE_IMAGE_DIR) / digest[:2] / f"{digest}.webp")

        out_buffer = BytesIO()
        image.save(out_buffer, format="WEBP", quality=85)
        # Delete any stale file so the digest-based path stays exact (storage
        # would otherwise append a random suffix to an existing name).
        if default_storage.exists(relpath):
            default_storage.delete(relpath)
        default_storage.save(relpath, ContentFile(out_buffer.getvalue()))
    except (OSError, UnidentifiedImageError):
        return False

    return True


def process_task_result(task):
    """Process task result based on status and format appropriately."""
    if task.status == "FAILURE":
        result_json = json.loads(task.result)
        if result_json["exc_type"] == "MediaImportError":
            task.summary = result_json["exc_message"][0]
            task.errors = task.traceback
        else:
            task.summary = "Unexpected error occurred while processing the task."
            task.errors = task.traceback
    elif task.status == "STARTED":
        task.summary = "This task is currently running."
        task.errors = None
    elif task.status == "SUCCESS":
        result_json = json.loads(task.result)
        # Split by the error indicator
        parts = result_json.split(integrations.tasks.ERROR_TITLE.strip())
        if len(parts) > 1:
            # We have both summary and errors
            task.summary = parts[0].strip()

            # Keep errors as a single string with newlines
            task.errors = parts[1].strip()
        else:
            # Only summary, no errors
            task.summary = result_json.strip()
            task.errors = None
    elif task.status == "PENDING":
        task.summary = "This task has been queued and is waiting to run."
        task.errors = None

    return task


def get_next_run_info(periodic_task):
    """Calculate next run time and frequency for a periodic task."""
    if not periodic_task.crontab:
        return None

    try:
        kwargs = json.loads(periodic_task.kwargs)
        mode = kwargs.get("mode", "new")  # Default to 'new' if not specified
    except json.JSONDecodeError:
        mode = "new"

    mode = "Only New Items" if mode == "new" else "Overwrite Existing"

    cron = periodic_task.crontab
    tz = zoneinfo.ZoneInfo(str(cron.timezone))
    now = timezone.now().astimezone(tz)

    # Create cron expression
    cron_expr = (
        f"{cron.minute} {cron.hour} {cron.day_of_month} "
        f"{cron.month_of_year} {cron.day_of_week}"
    )
    cron_iter = croniter.croniter(cron_expr, now)
    next_run = cron_iter.get_next(datetime)

    # Determine frequency
    if cron.day_of_week == "*":
        frequency = "Every Day"
    elif cron.day_of_week == "*/2":
        frequency = "Every 2 days"
    else:
        frequency = f"Cron: {cron_expr}"

    return {
        "next_run": next_run,
        "frequency": frequency,
        "mode": mode,
    }
