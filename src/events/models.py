from datetime import datetime

from django.db import models
from django.db.models import Count, Exists, OuterRef, Q, Subquery, UniqueConstraint
from django.utils import timezone

from app.models import Item, Media, MediaTypes

# Statuses that represent inactive tracking
# will be ignored when creating events
INACTIVE_TRACKING_STATUSES = [
    Media.Status.PAUSED.value,
    Media.Status.DROPPED.value,
]


class EventManager(models.Manager):
    """Custom manager for the Event model."""

    def get_user_events(self, user, first_day, last_day):
        """Get all upcoming media events of the specified user."""
        # Convert date objects to datetime objects with timezone awareness
        first_datetime = timezone.make_aware(
            datetime.combine(first_day, datetime.min.time()),
        )
        last_datetime = timezone.make_aware(
            datetime.combine(last_day, datetime.max.time()),
        )

        media_types_with_user = [
            choice.value for choice in MediaTypes if choice != MediaTypes.EPISODE
        ]

        # Build query for user ownership
        user_query = Q()
        for media_type in media_types_with_user:
            user_query |= Q(**{f"item__{media_type}__user": user})

        # Build query to exclude inactive tracking statuses
        active_status_query = Q()
        for media_type in media_types_with_user:
            active_status_query &= ~Q(
                **{f"item__{media_type}__status__in": INACTIVE_TRACKING_STATUSES},
            )

        return self.filter(
            user_query,
            active_status_query,
            datetime__gte=first_datetime,
            datetime__lte=last_datetime,
        ).select_related("item")

    def get_items_to_process(self):
        """Get items to process for the calendar."""
        media_types_with_status = [
            choice.value for choice in MediaTypes if choice != MediaTypes.EPISODE
        ]

        # Build a query to find items with at least one active media
        active_query = Q()
        for media_type in media_types_with_status:
            active_query |= Q(
                **{f"{media_type}__isnull": False},
                **{
                    f"{media_type}__status__in": [
                        status
                        for status in Media.Status.values
                        if status not in INACTIVE_TRACKING_STATUSES
                    ],
                },
            )

        # Get all items with at least one active media
        items_with_active_media = Item.objects.filter(active_query).distinct()

        # Subquery to check if an item has any future events
        now = timezone.now()
        future_events = Event.objects.filter(
            item=OuterRef("pk"),
            datetime__gte=now,
        )

        # Subquery to check if a comic has events in the last year
        one_year_ago = now - timezone.timedelta(days=365)
        recent_comic_events = Event.objects.filter(
            item=OuterRef("pk"),
            item__media_type=MediaTypes.COMIC,
            datetime__gte=one_year_ago,
        ).order_by("-datetime")

        # manga with less than two events means we don't have total chapters count
        # comics with events in the last year should also be processed
        return (
            items_with_active_media.annotate(
                event_count=Count("event"),
                latest_comic_event=Subquery(recent_comic_events.values("datetime")[:1]),
            )
            .filter(
                Q(Exists(future_events))  # has future events
                | Q(event__isnull=True)  # no events
                | (Q(media_type=MediaTypes.MANGA) & Q(event_count__lt=2))
                | (
                    Q(media_type=MediaTypes.COMIC) & Q(latest_comic_event__isnull=False)
                ),
            )
            .distinct()
        )


class Event(models.Model):
    """Calendar event model."""

    item = models.ForeignKey(Item, on_delete=models.CASCADE)
    episode_number = models.IntegerField(null=True)
    datetime = models.DateTimeField()
    notification_sent = models.BooleanField(default=False)
    objects = EventManager()

    class Meta:
        """Meta class for Event model."""

        ordering = ["datetime"]
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
            return f"{self.item.title} S{self.item.season_number} - Ep. {self.episode_number}"  # noqa: E501
        if self.item.media_type == "manga":
            return f"{self.item.__str__()} - Ch. {self.episode_number}"
        if self.item.media_type == "anime":
            return f"{self.item.__str__()} - Ep. {self.episode_number}"
        if self.item.media_type == "comic":
            return f"{self.item.__str__()} #{self.episode_number}"
        return self.item.__str__()

    @property
    def readable_episode_number(self):
        """Return the episode number in a readable format."""
        if self.episode_number is None:
            return ""
        if self.item.media_type == "manga":
            return f"Ch. {self.episode_number}"
        return f"Ep. {self.episode_number}"
