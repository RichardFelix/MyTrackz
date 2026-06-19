from django.conf import settings
from django.core.management.base import BaseCommand

from app.models import Item
from app.tasks import cache_item_image


class Command(BaseCommand):
    """One-off backfill: queue the image cache task for pre-existing Items."""

    help = "Queue image caching for existing Items that predate the local image cache."

    def handle(self, *args, **options):  # noqa: ARG002
        """Queue cache_item_image for every Item missing a cached image."""
        items = Item.objects.exclude(image="").exclude(image=settings.IMG_NONE).filter(
            image_cached=False,
        )

        queued_count = 0
        for item in items.iterator():
            cache_item_image.delay(item.id, item.image)
            queued_count += 1

        self.stdout.write(f"Queued {queued_count} items for image caching.")
