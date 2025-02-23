from django.db import models
from django.db.models import Count, Exists, OuterRef, Q, UniqueConstraint
from django.utils import timezone

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
            Media.Status.IN_PROGRESS.value,
            Media.Status.PLANNING.value,
            Media.Status.PAUSED.value,
            Media.Status.REPEATING.value,
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

        # Subquery to check if an item has any future events
        future_events = Event.objects.filter(
            item=OuterRef("pk"),
            date__gte=timezone.now(),
        )

        # manga with less than two events means we don't have total chapters count
        return (
            items_with_status.annotate(event_count=Count("event"))
            .filter(
                Q(Exists(future_events))  # has future events
                | Q(event__isnull=True)  # no events
                | (Q(media_type=Item.MediaTypes.MANGA) & Q(event_count__lt=2)),
            )
            .distinct()
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
        constraints = [
            UniqueConstraint(
                fields=["item", "episode_number"],
                name="unique_item_episode",
            ),
            UniqueConstraint(
                fields=["item"],
                condition=Q(episode_number__isnull=True),
                name="unique_item_null_episode",
            ),
        ]

    def __str__(self):
        """Return event title."""
        if self.item.media_type == "season":
            return f"{self.item.__str__()}E{self.episode_number}"
        if self.item.media_type == "manga":
            return f"{self.item.__str__()} - Ch. {self.episode}"
        if self.item.media_type == "anime":
            return f"{self.item.__str__()} - Ep. {self.episode_number}"
        return self.item.__str__()
