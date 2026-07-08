from django.core.management.base import BaseCommand

from app.models import Item, Sources


class Command(BaseCommand):
    """One-off backfill: rewrite existing IGDB image URLs to the t_1080p variant."""

    help = (
        "Rewrite existing IGDB Item images from t_original to t_1080p so they fit "
        "under the download size cap and cache locally."
    )

    def handle(self, *args, **options):  # noqa: ARG002
        """Point IGDB items at t_1080p; Item.save() re-queues the cache task."""
        # Skip image_locked items: those carry a user's custom image, with the
        # untouched original preserved in image_original.
        items = Item.objects.filter(
            source=Sources.IGDB.value,
            image_locked=False,
            image__contains="/t_original/",
        )

        updated_count = 0
        for item in items.iterator():
            # save() detects the changed URL, resets image_cached, and queues
            # cache_item_image itself, so no explicit re-queue is needed here.
            item.image = item.image.replace("/t_original/", "/t_1080p/")
            item.save(update_fields=["image"])
            updated_count += 1

        self.stdout.write(f"Rewrote {updated_count} IGDB items to t_1080p.")
