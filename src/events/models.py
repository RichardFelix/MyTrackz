from datetime import datetime

from django.conf import settings
from django.db import models
from django.db.models import Q

from app.models import Item, Media


class EventManager(models.Manager):
    """Custom manager for the Event model."""

    def get_user_events(self, user):
        """Get all upcoming media events of the specified user."""
        media_types_with_user = [
            choice.value
            for choice in Item.MediaTypes
            if choice != Item.MediaTypes.EPISODE
        ]
        query = Q()
        for media_type in media_types_with_user:
            query |= Q(**{f"item__{media_type}__user": user})

        return self.filter(
            query,
        )

    def get_items_to_process(self):
        """Get items to process for the calendar."""
        statuses_to_track = [
            choice.value
            for choice in Media.Status
            if choice
            not in [
                Media.Status.COMPLETED,
                Media.Status.DROPPED,
                Media.Status.REPEATING,
            ]
        ]
        media_types_with_status = [
            choice.value
            for choice in Item.MediaTypes
            if choice != Item.MediaTypes.EPISODE
        ]
        query = Q()
        for media_type in media_types_with_status:
            query |= Q(**{f"{media_type}__status__in": statuses_to_track})

        items_with_status = Item.objects.filter(query).distinct()

        future_events = Event.objects.filter(date__gte=datetime.now(tz=settings.TZ))
        future_event_item_ids = set(future_events.values_list("item_id", flat=True))
        items_without_events = items_with_status.exclude(
            id__in=Event.objects.values_list("item_id", flat=True),
        )

        # Combine items with future events and items without any events
        return items_with_status.filter(
            Q(id__in=future_event_item_ids) | Q(id__in=items_without_events),
        )


class Event(models.Model):
    """Calendar event model."""

    item = models.ForeignKey(Item, on_delete=models.CASCADE)
    episode_number = models.IntegerField(null=True)
    date = models.DateField()
    objects = EventManager()

    class Meta:
        """Meta class for Event model."""

        ordering = ["date"]
        unique_together = ["item", "episode_number"]

    def __str__(self):
        """Return event title."""
        if self.item.media_type == "season":
            return f"{self.item.__str__()}E{self.episode_number}"
        if self.episode_number:
            return f"{self.item.__str__()} - Ep. {self.episode_number}"
        return self.item.__str__()
