from django.core.management.base import BaseCommand

from app.models import ImageCacheFormat, Item
from app.tasks import transcode_cached_image_to_webp


class Command(BaseCommand):
    """One-off backfill: locally re-encode already-cached JPEGs as WebP."""

    help = (
        "Queue local re-encoding of existing cached JPEG images as WebP. "
        "Purely local file transcoding, no provider API calls."
    )

    def handle(self, *args, **options):  # noqa: ARG002
        """Queue transcode_cached_image_to_webp for every JPEG-cached Item."""
        items = Item.objects.filter(
            image_cached=True,
            image_cache_format=ImageCacheFormat.JPEG.value,
        )

        queued_count = 0
        for item in items.iterator():
            transcode_cached_image_to_webp.delay(item.id)
            queued_count += 1

        self.stdout.write(f"Queued {queued_count} cached images for WebP transcoding.")
