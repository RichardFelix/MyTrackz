import logging

from django.apps import apps
from django.conf import settings
from django.core.validators import (
    DecimalValidator,
    MaxValueValidator,
    MinValueValidator,
)
from django.db import models
from django.db.models import (
    Case,
    CheckConstraint,
    F,
    FloatField,
    IntegerField,
    Max,
    Prefetch,
    Q,
    Sum,
    UniqueConstraint,
    When,
)
from django.db.models.functions import Cast
from django.utils import timezone
from model_utils import FieldTracker
from simple_history.models import HistoricalRecords
from simple_history.utils import bulk_create_with_history, bulk_update_with_history

import app
import events
import users
from app import providers
from app.mixins import CalendarTriggerMixin

logger = logging.getLogger(__name__)


class Sources(models.TextChoices):
    """Choices for the source of the item."""

    TMDB = "tmdb", "The Movie Database"
    MAL = "mal", "MyAnimeList"
    MANGAUPDATES = "mangaupdates", "MangaUpdates"
    IGDB = "igdb", "Internet Game Database"
    OPENLIBRARY = "openlibrary", "Open Library"
    COMICVINE = "comicvine", "Comic Vine"
    MANUAL = "manual", "Manual"


class MediaTypes(models.TextChoices):
    """Choices for the media type of the item."""

    TV = "tv", "TV Show"
    SEASON = "season", "TV Season"
    EPISODE = "episode", "Episode"
    MOVIE = "movie", "Movie"
    ANIME = "anime", "Anime"
    MANGA = "manga", "Manga"
    GAME = "game", "Game"
    BOOK = "book", "Book"
    COMIC = "comic", "Comic"


class Item(models.Model):
    """Model for items in custom lists."""

    media_id = models.CharField(max_length=20)
    source = models.CharField(
        max_length=20,
        choices=Sources.choices,
    )
    media_type = models.CharField(
        max_length=10,
        choices=MediaTypes.choices,
        default=MediaTypes.MOVIE.value,
    )
    title = models.CharField(max_length=255)
    image = models.URLField()  # if add default, custom media entry will show the value
    season_number = models.PositiveIntegerField(null=True, blank=True)
    episode_number = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        """Meta options for the model."""

        constraints = [
            # Ensures items without season/episode numbers are unique
            UniqueConstraint(
                fields=["media_id", "source", "media_type"],
                condition=Q(season_number__isnull=True, episode_number__isnull=True),
                name="unique_item_without_season_episode",
            ),
            # Ensures seasons are unique within a show
            UniqueConstraint(
                fields=["media_id", "source", "media_type", "season_number"],
                condition=Q(season_number__isnull=False, episode_number__isnull=True),
                name="unique_item_with_season",
            ),
            # Ensures episodes are unique within a season
            UniqueConstraint(
                fields=[
                    "media_id",
                    "source",
                    "media_type",
                    "season_number",
                    "episode_number",
                ],
                condition=Q(season_number__isnull=False, episode_number__isnull=False),
                name="unique_item_with_season_episode",
            ),
            # Enforces that season items must have a season number but no episode number
            CheckConstraint(
                check=Q(
                    media_type=MediaTypes.SEASON.value,
                    season_number__isnull=False,
                    episode_number__isnull=True,
                )
                | ~Q(media_type=MediaTypes.SEASON.value),
                name="season_number_required_for_season",
            ),
            # Enforces that episode items must have both season and episode numbers
            CheckConstraint(
                check=Q(
                    media_type=MediaTypes.EPISODE.value,
                    season_number__isnull=False,
                    episode_number__isnull=False,
                )
                | ~Q(media_type=MediaTypes.EPISODE.value),
                name="season_and_episode_required_for_episode",
            ),
            # Prevents season/episode numbers from being set on non-TV media types
            CheckConstraint(
                check=Q(
                    ~Q(
                        media_type__in=[
                            MediaTypes.SEASON.value,
                            MediaTypes.EPISODE.value,
                        ],
                    ),
                    season_number__isnull=True,
                    episode_number__isnull=True,
                )
                | Q(media_type__in=[MediaTypes.SEASON.value, MediaTypes.EPISODE.value]),
                name="no_season_episode_for_other_types",
            ),
            # Validate source choices
            CheckConstraint(
                check=Q(source__in=Sources.values),
                name="%(app_label)s_%(class)s_source_valid",
            ),
            # Validate media_type choices
            CheckConstraint(
                check=Q(media_type__in=MediaTypes.values),
                name="%(app_label)s_%(class)s_media_type_valid",
            ),
        ]
        ordering = ["media_id"]

    def __str__(self):
        """Return the name of the item."""
        name = self.title
        if self.season_number is not None:
            name += f" S{self.season_number}"
            if self.episode_number is not None:
                name += f"E{self.episode_number}"
        return name

    @classmethod
    def generate_manual_id(cls, media_type):
        """Generate a new ID for manual items."""
        latest_item = (
            cls.objects.filter(source=Sources.MANUAL.value, media_type=media_type)
            .annotate(
                media_id_int=Cast("media_id", IntegerField()),
            )
            .order_by("-media_id_int")
            .first()
        )

        if latest_item is None:
            return "1"

        return str(int(latest_item.media_id) + 1)


class MediaManager(models.Manager):
    """Custom manager for media models."""

    def get_historical_models(self):
        """Return list of historical model names."""
        return [f"historical{media_type}" for media_type in MediaTypes.values]

    def get_media_list(self, user, media_type, status_filter, sort_filter, search=None):
        """Get media list based on filters, sorting and search."""
        model = apps.get_model(app_label="app", model_name=media_type)
        queryset = model.objects.filter(user=user.id)

        if users.models.MediaStatusChoices.ALL not in status_filter:
            queryset = queryset.filter(status__in=status_filter)

        if search:
            queryset = queryset.filter(item__title__icontains=search)

        queryset = queryset.select_related("item")

        # Apply media-specific prefetches
        queryset = self._apply_prefetch_related(queryset, media_type)

        return self._apply_sorting(queryset, model, media_type, sort_filter)

    def _apply_prefetch_related(self, queryset, media_type):
        """Apply appropriate prefetch_related based on media type."""
        if media_type == MediaTypes.TV.value:
            return queryset.prefetch_related(
                Prefetch(
                    "seasons",
                    queryset=Season.objects.select_related("item"),
                ),
                Prefetch(
                    "seasons__episodes",
                    queryset=Episode.objects.select_related("item"),
                ),
                Prefetch(
                    "seasons__item__event_set",
                    queryset=events.models.Event.objects.all(),
                    to_attr="prefetched_events",
                ),
            )
        if media_type == MediaTypes.SEASON.value:
            return queryset.prefetch_related(
                Prefetch(
                    "episodes",
                    queryset=Episode.objects.select_related("item"),
                ),
            )
        return queryset

    def _apply_sorting(self, queryset, model, media_type, sort_filter):
        """Apply sorting based on the sort filter and media type."""
        # Check if sort_filter is a property on the model
        sort_is_property = sort_filter in [
            name for name in dir(model) if isinstance(getattr(model, name), property)
        ]
        if (
            media_type in (MediaTypes.TV.value, MediaTypes.SEASON.value)
            and sort_is_property
        ):
            return self._sort_by_property(queryset, sort_filter)

        # Handle sorting by Item fields
        sort_is_item_field = sort_filter in [f.name for f in Item._meta.fields]  # noqa: SLF001
        if sort_is_item_field:
            sort_field = f"item__{sort_filter}"
            return queryset.order_by(
                F(sort_field).asc() if sort_filter == "title" else F(sort_field).desc(),
            )

        # Default sorting
        return queryset.order_by(F(sort_filter).desc(nulls_last=True))

    def _sort_by_property(self, queryset, sort_filter):
        """Sort queryset by a property field."""
        # Special handling for date fields
        if sort_filter in ("start_date", "end_date"):
            result_list = list(queryset)

            # Split items with and without dates
            with_dates = [
                item for item in result_list if getattr(item, sort_filter) is not None
            ]
            without_dates = [
                item for item in result_list if getattr(item, sort_filter) is None
            ]

            # Sort items with dates
            if sort_filter == "start_date":
                # For start_date, sort ascending (earliest first)
                sorted_with_dates = sorted(
                    with_dates,
                    key=lambda x: getattr(x, sort_filter),
                )
            else:
                # For other date fields, sort descending (latest first)
                sorted_with_dates = sorted(
                    with_dates,
                    key=lambda x: getattr(x, sort_filter),
                    reverse=True,
                )

            # Combine lists - items with dates first, then items without dates
            return sorted_with_dates + without_dates

        # For non-date properties, sort in descending order
        return sorted(queryset, key=lambda x: getattr(x, sort_filter), reverse=True)

    def get_in_progress(self, user, sort_by, items_limit, specific_media_type=None):
        """Get a media list of in progress media by type."""
        list_by_type = {}

        media_types_to_process = self._get_media_types_to_process(
            user,
            specific_media_type,
        )

        for media_type in media_types_to_process:
            # Get base queryset for in-progress media
            media_list = self.get_media_list(
                user=user,
                media_type=media_type,
                status_filter=[
                    Media.Status.IN_PROGRESS.value,
                    Media.Status.REPEATING.value,
                ],
                sort_filter="score",
            )

            if not media_list.exists():
                continue

            # Annotate both max_progress and next_event
            media_list = self.annotate_max_progress(media_list, media_type)
            media_list = self._annotate_next_event(media_list)

            # Apply sorting based on the requested sort criteria
            media_list = self._sort_in_progress_media(media_list, sort_by, media_type)

            # Store results with pagination
            total_count = (
                len(media_list) if isinstance(media_list, list) else media_list.count()
            )

            # Apply limit based on whether this is a specific type request or dashboard
            limit = None if specific_media_type else items_limit
            offset = items_limit if specific_media_type else 0

            if isinstance(media_list, list):
                media_list = (
                    media_list[offset:] if specific_media_type else media_list[:limit]
                )
            else:
                media_list = (
                    media_list[offset:] if specific_media_type else media_list[:limit]
                )

            list_by_type[media_type] = {
                "items": media_list,
                "total": total_count,
            }

        return list_by_type

    def _get_media_types_to_process(self, user, specific_media_type):
        """Determine which media types to process based on user settings."""
        if specific_media_type:
            return [specific_media_type]

        active_types = user.get_active_media_types()

        # Filter out TV
        return [
            media_type
            for media_type in active_types
            if media_type != MediaTypes.TV.value
        ]

    def _annotate_next_event(self, media_list):
        """Annotate next_event for media items."""
        # Prefetch the next event for each media item
        media_list = media_list.prefetch_related(
            Prefetch(
                "item__event_set",
                queryset=events.models.Event.objects.filter(
                    datetime__gt=timezone.now(),
                ).order_by("datetime"),
                to_attr="next_events",
            ),
        )

        # Process each media item to attach the next event
        for media in media_list:
            media.next_event = (
                media.item.next_events[0] if media.item.next_events else None
            )

        return media_list

    def _sort_in_progress_media(self, media_list, sort_by, media_type):
        """Sort in-progress media based on the sort criteria."""
        if sort_by == "upcoming":
            # Convert queryset to list if it's not already
            if not isinstance(media_list, list):
                media_list = list(media_list)

            # Sort by next_event datetime
            return sorted(
                media_list,
                key=lambda x: (
                    x.next_event is None,  # Items without events come last
                    x.next_event.datetime if x.next_event else None,
                    x.item.title,
                ),
            )
        if sort_by == "title":
            return (
                media_list.order_by("item__title")
                if not isinstance(media_list, list)
                else sorted(media_list, key=lambda x: x.item.title)
            )
        if sort_by in ["completion", "episodes_left"]:
            return self._sort_by_completion_or_episodes(media_list, sort_by, media_type)

        return media_list

    def _sort_by_completion_or_episodes(self, media_list, sort_by, media_type):
        """Sort media by completion percentage or episodes left."""
        # For Season, we need to evaluate the queryset and sort in Python
        if media_type == MediaTypes.SEASON.value:
            return self._sort_season_by_completion_or_episodes(
                list(media_list),
                sort_by,
            )

        # For other media types, use database annotations
        media_list = media_list.annotate(
            completion_rate=Case(
                When(
                    max_progress__isnull=False,
                    then=(Cast("progress", FloatField()) * 100.0)
                    / Cast("max_progress", FloatField()),
                ),
                default=0.0,
                output_field=FloatField(),
            ),
            episodes_remaining=Case(
                When(
                    max_progress__isnull=False,
                    then=F("max_progress") - F("progress"),
                ),
                default=0,
                output_field=IntegerField(),
            ),
        )

        if sort_by == "completion":
            return media_list.order_by(
                Case(When(max_progress__isnull=True, then=0), default=1),
                "-completion_rate",
                "item__title",
            )
        # episodes_left
        return media_list.order_by(
            Case(When(max_progress__isnull=True, then=1), default=0),
            "episodes_remaining",
            "item__title",
        )

    def _sort_season_by_completion_or_episodes(self, media_list, sort_by):
        """Sort season media by completion percentage or episodes left."""
        if sort_by == "completion":
            media_list.sort(
                key=lambda x: (
                    x.max_progress is not None,  # Items with max_progress first
                    (x.progress / x.max_progress * 100 if x.max_progress else 0),
                    x.item.title,
                ),
                reverse=True,
            )
        else:  # episodes_left
            media_list.sort(
                key=lambda x: (
                    not x.max_progress,  # Items without max_progress last
                    (x.max_progress - x.progress if x.max_progress else 0),
                    x.item.title,
                ),
            )
        return media_list

    def annotate_max_progress(self, queryset, media_type):
        """Annotate max_progress only for the current page items."""
        current_datetime = timezone.now()
        if media_type == MediaTypes.TV.value:
            return self._annotate_tv_released_episodes(queryset, current_datetime)

        if media_type == MediaTypes.MOVIE.value:
            if isinstance(queryset, list):
                for media in queryset:
                    media.max_progress = 1
                return queryset
            return queryset.annotate(
                max_progress=models.Value(1, output_field=models.IntegerField()),
            )

        if isinstance(queryset, list):
            return self.annotate_list_max_progress(queryset, current_datetime)

        return queryset.annotate(
            max_progress=models.Max(
                "item__event__content_number",
                filter=models.Q(item__event__datetime__lte=current_datetime),
            ),
        )

    def annotate_list_max_progress(self, queryset, current_datetime):
        """Annotate max_progress for a list of media items."""
        item_ids = [media.item_id for media in queryset]
        max_progress_map = (
            events.models.Event.objects.filter(
                item_id__in=item_ids,
                datetime__lte=current_datetime,
            )
            .values("item_id")
            .annotate(max_ep=models.Max("content_number"))
        )

        max_progress_dict = {
            item["item_id"]: item["max_ep"] for item in max_progress_map
        }

        for media in queryset:
            media.max_progress = max_progress_dict.get(media.item_id)

        return queryset

    def _annotate_tv_released_episodes(self, queryset, current_datetime):
        """Annotate TV shows with the number of released episodes."""
        for tv in queryset:
            total_released_episodes = 0
            if hasattr(tv, "seasons"):
                for season in tv.seasons.all():
                    if (
                        hasattr(season.item, "prefetched_events")
                        and season.item.season_number != 0
                    ):
                        # Filter events by datetime and find max episode number
                        released_events = [
                            event
                            for event in season.item.prefetched_events
                            if event.datetime <= current_datetime
                            and event.content_number is not None
                        ]
                        max_episode = max(
                            [event.content_number for event in released_events],
                            default=0,
                        )

                        total_released_episodes += max_episode
            tv.max_progress = total_released_episodes

        return queryset

    def get_media(
        self,
        user,
        media_id,
        media_type,
        source,
        season_number=None,
        episode_number=None,
    ):
        """Get user media object given the media type and item."""
        model = apps.get_model(app_label="app", model_name=media_type)
        params = {
            "item__media_type": media_type,
            "item__source": source,
            "item__media_id": media_id,
        }

        if media_type == MediaTypes.SEASON.value:
            params["item__season_number"] = season_number
            params["user"] = user
        elif media_type == MediaTypes.EPISODE.value:
            params["item__season_number"] = season_number
            params["item__episode_number"] = episode_number
            params["related_season__user"] = user
        else:
            params["user"] = user

        try:
            return model.objects.get(**params)
        except model.DoesNotExist:
            return None


class Media(CalendarTriggerMixin, models.Model):
    """Abstract model for all media types."""

    class Status(models.TextChoices):
        """Choices for item status."""

        COMPLETED = "Completed", "Completed"
        IN_PROGRESS = "In progress", "In Progress"
        REPEATING = "Repeating", "Repeating"
        PLANNING = "Planning", "Planning"
        PAUSED = "Paused", "Paused"
        DROPPED = "Dropped", "Dropped"

    history = HistoricalRecords(
        cascade_delete_history=True,
        inherit=True,
        excluded_fields=[
            "item",
            "user",
            "related_tv",
        ],
    )

    item = models.ForeignKey(Item, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    score = models.DecimalField(
        null=True,
        blank=True,
        max_digits=3,
        decimal_places=1,
        validators=[
            DecimalValidator(3, 1),
            MinValueValidator(0),
            MaxValueValidator(10),
        ],
    )
    progress = models.PositiveIntegerField(default=0)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.COMPLETED.value,
    )
    repeats = models.PositiveIntegerField(default=0)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        """Meta options for the model."""

        abstract = True
        ordering = ["-score"]
        constraints = [
            models.UniqueConstraint(
                fields=["item", "user"],
                name="%(app_label)s_%(class)s_unique_item_user",
            ),
        ]

    def __str__(self):
        """Return the title of the media."""
        return self.item.__str__()

    def save(self, *args, **kwargs):
        """Save the media instance."""
        if self.tracker.has_changed("progress"):
            self.process_progress()

        if self.tracker.has_changed("status"):
            self.process_status()

        super().save(*args, **kwargs)

    def process_progress(self):
        """Update fields depending on the progress of the media."""
        if self.progress < 0:
            self.progress = 0
        else:
            max_progress = providers.services.get_media_metadata(
                self.item.media_type,
                self.item.media_id,
                self.item.source,
            )["max_progress"]

            if max_progress:
                self.progress = min(self.progress, max_progress)

                if self.progress == max_progress:
                    self.status = self.Status.COMPLETED.value

    def process_status(self):
        """Update fields depending on the status of the media."""
        now = timezone.now().date()

        if self.status == self.Status.IN_PROGRESS.value:
            if not self.start_date:
                self.start_date = now

        elif self.status == self.Status.COMPLETED.value:
            if not self.end_date:
                self.end_date = now

            max_progress = providers.services.get_media_metadata(
                self.item.media_type,
                self.item.media_id,
                self.item.source,
            )["max_progress"]

            if max_progress:
                self.progress = max_progress

            if self.tracker.previous("status") == self.Status.REPEATING.value:
                self.repeats += 1

        if (
            not self._disable_calendar_triggers
            and self.status not in events.models.INACTIVE_TRACKING_STATUSES
        ):
            events.tasks.reload_calendar.delay(items_to_process=[self.item])

    @property
    def formatted_progress(self):
        """Return the progress of the media in a formatted string."""
        return str(self.progress)

    def increase_progress(self):
        """Increase the progress of the media by one."""
        self.progress += 1
        self.save()
        logger.info("Watched %s E%s", self, self.progress)

    def decrease_progress(self):
        """Decrease the progress of the media by one."""
        self.progress -= 1
        self.save()
        logger.info("Unwatched %s E%s", self, self.progress + 1)


class BasicMedia(Media):
    """Model for basic media types."""

    objects = MediaManager()


class TV(Media):
    """Model for TV shows."""

    tracker = FieldTracker()

    @tracker  # postpone field reset until after the save
    def save(self, *args, **kwargs):
        """Save the media instance."""
        super(Media, self).save(*args, **kwargs)

        if self.tracker.has_changed("status"):
            if self.status == self.Status.COMPLETED.value:
                self.completed()
            if (
                self.status not in events.models.INACTIVE_TRACKING_STATUSES
                and not self._disable_calendar_triggers
            ):
                events.tasks.reload_calendar.delay(items_to_process=[self.item])

    @property
    def progress(self):
        """Return the total episodes watched for the TV show."""
        return sum(
            season.progress
            for season in self.seasons.all()
            if season.item.season_number != 0
        )

    @property
    def last_watched(self):
        """Return the latest watched episode in SxxExx format."""
        if not hasattr(self, "seasons"):
            return ""

        watched_episodes = [
            {
                "season": season.item.season_number,
                "episode": episode.item.episode_number,
                "end_date": episode.end_date,
            }
            for season in self.seasons.all()
            if hasattr(season, "episodes") and season.item.season_number != 0
            for episode in season.episodes.all()
            if episode.end_date is not None
        ]

        if not watched_episodes:
            return ""

        latest_episode = max(
            watched_episodes,
            key=lambda x: (x["end_date"], x["season"], x["episode"]),
        )

        return f"S{latest_episode['season']:02d}E{latest_episode['episode']:02d}"

    @property
    def repeats(self):
        """Return the number of max repeated episodes in the TV show."""
        return max((season.repeats for season in self.seasons.all()), default=0)

    @property
    def start_date(self):
        """Return the date of the first episode watched."""
        dates = [
            season.start_date for season in self.seasons.all() if season.start_date
        ]
        return min(dates) if dates else None

    @property
    def end_date(self):
        """Return the date of the last episode watched."""
        dates = [season.end_date for season in self.seasons.all() if season.end_date]
        return max(dates) if dates else None

    def completed(self):
        """Create remaining seasons and episodes for a TV show."""
        tv_metadata = providers.services.get_media_metadata(
            self.item.media_type,
            self.item.media_id,
            self.item.source,
        )
        max_progress = tv_metadata["max_progress"]

        if not max_progress or self.progress > max_progress:
            return

        seasons_to_update = []
        episodes_to_create = []

        season_numbers = [
            season["season_number"]
            for season in tv_metadata["related"]["seasons"]
            if season["season_number"] != 0
        ]
        tv_with_seasons_metadata = providers.services.get_media_metadata(
            "tv_with_seasons",
            self.item.media_id,
            self.item.source,
            season_numbers,
        )
        for season_number in season_numbers:
            season_metadata = tv_with_seasons_metadata[f"season/{season_number}"]

            item, _ = Item.objects.get_or_create(
                media_id=self.item.media_id,
                source=self.item.source,
                media_type=MediaTypes.SEASON.value,
                season_number=season_number,
                defaults={
                    "title": self.item.title,
                    "image": season_metadata["image"],
                },
            )
            try:
                season_instance = Season.objects.get(
                    item=item,
                    user=self.user,
                )

                if season_instance.status != self.Status.COMPLETED.value:
                    season_instance.status = self.Status.COMPLETED.value
                    seasons_to_update.append(season_instance)

            except Season.DoesNotExist:
                season_instance = Season(
                    item=item,
                    score=None,
                    status=self.Status.COMPLETED.value,
                    notes="",
                    related_tv=self,
                    user=self.user,
                )
                Season.save_base(season_instance)
            episodes_to_create.extend(
                season_instance.get_remaining_eps(season_metadata),
            )
        bulk_update_with_history(seasons_to_update, Season, ["status"])
        bulk_create_with_history(episodes_to_create, Episode)


class Season(Media):
    """Model for seasons of TV shows."""

    related_tv = models.ForeignKey(
        TV,
        on_delete=models.CASCADE,
        related_name="seasons",
    )

    tracker = FieldTracker()

    class Meta:
        """Limit the uniqueness of seasons.

        Only one season per media can have the same season number.
        """

        constraints = [
            models.UniqueConstraint(
                fields=["related_tv", "item"],
                name="%(app_label)s_season_unique_tv_item",
            ),
        ]

    def __str__(self):
        """Return the title of the media and season number."""
        return f"{self.item.title} S{self.item.season_number}"

    @tracker  # postpone field reset until after the save
    def save(self, *args, **kwargs):
        """Save the media instance."""
        # if related_tv is not set
        if self.related_tv_id is None:
            self.related_tv = self.get_tv()

        super(Media, self).save(*args, **kwargs)

        if self.tracker.has_changed("status"):
            if self.status == self.Status.COMPLETED.value:
                season_metadata = providers.services.get_media_metadata(
                    MediaTypes.SEASON.value,
                    self.item.media_id,
                    self.item.source,
                    [self.item.season_number],
                )
                bulk_create_with_history(
                    self.get_remaining_eps(season_metadata),
                    Episode,
                )
            if (
                self.status not in events.models.INACTIVE_TRACKING_STATUSES
                and not self._disable_calendar_triggers
            ):
                events.tasks.reload_calendar.delay(
                    items_to_process=[self.related_tv.item],
                )

    @property
    def progress(self):
        """Return the current episode number of the season."""
        # continue initial watch
        if self.status == self.Status.IN_PROGRESS.value:
            sorted_episodes = sorted(
                self.episodes.all(),
                key=lambda e: e.item.episode_number,
                reverse=True,
            )
        else:
            # sort by repeats and then by episode_number
            sorted_episodes = sorted(
                self.episodes.all(),
                key=lambda e: (e.repeats, e.item.episode_number),
                reverse=True,
            )

        if sorted_episodes:
            return sorted_episodes[0].item.episode_number
        return 0

    @property
    def repeats(self):
        """Return the number of max repeated episodes in the season."""
        return max((episodes.repeats for episodes in self.episodes.all()), default=0)

    @property
    def start_date(self):
        """Return the date of the first episode watched."""
        dates = [
            episode.end_date
            for episode in self.episodes.all()
            if episode.end_date is not None
        ]
        return min(dates) if dates else None

    @property
    def end_date(self):
        """Return the date of the last episode watched."""
        dates = [
            episode.end_date
            for episode in self.episodes.all()
            if episode.end_date is not None
        ]
        return max(dates) if dates else None

    def increase_progress(self):
        """Watch the next episode of the season."""
        season_metadata = providers.services.get_media_metadata(
            MediaTypes.SEASON.value,
            self.item.media_id,
            self.item.source,
            [self.item.season_number],
        )
        episodes = season_metadata["episodes"]

        if self.progress == 0:
            # start watching from the first episode
            next_episode_number = episodes[0]["episode_number"]
        else:
            next_episode_number = providers.tmdb.find_next_episode(
                self.progress,
                episodes,
            )

        today = timezone.now().date()

        if next_episode_number:
            self.watch(next_episode_number, today)
        else:
            logger.info("No more episodes to watch.")

    def watch(self, episode_number, end_date):
        """Create or add a repeat to an episode of the season."""
        item = self.get_episode_item(episode_number)

        try:
            episode = Episode.objects.get(
                related_season=self,
                item=item,
            )
            episode.end_date = end_date
            episode.repeats += 1
            episode.save()
            logger.info(
                "%s rewatched successfully.",
                episode,
            )
        except Episode.DoesNotExist:
            # from the form, end_date is a string
            if end_date == "None":
                end_date = None

            episode = Episode.objects.create(
                related_season=self,
                item=item,
                end_date=end_date,
            )
            logger.info(
                "%s created successfully.",
                episode,
            )

    def decrease_progress(self):
        """Unwatch the current episode of the season."""
        self.unwatch(self.progress)

    def unwatch(self, episode_number):
        """Unwatch the episode instance."""
        try:
            item = self.get_episode_item(episode_number)

            episode = Episode.objects.get(
                related_season=self,
                item=item,
            )

            if episode.repeats > 0:
                episode.repeats -= 1
                episode.save(update_fields=["repeats"])
                logger.info(
                    "%s watch count decreased.",
                    episode,
                )
            else:
                episode.delete()
                logger.info(
                    "%s deleted successfully.",
                    episode,
                )

        except Episode.DoesNotExist:
            logger.warning(
                "Episode %sE%s does not exist.",
                self,
                episode_number,
            )

    def get_tv(self):
        """Get related TV instance for a season and create it if it doesn't exist."""
        try:
            tv = TV.objects.get(
                item__media_id=self.item.media_id,
                item__media_type=MediaTypes.TV.value,
                item__season_number=None,
                item__source=self.item.source,
                user=self.user,
            )
        except TV.DoesNotExist:
            tv_metadata = providers.services.get_media_metadata(
                MediaTypes.TV.value,
                self.item.media_id,
                self.item.source,
            )

            # creating tv with multiple seasons from a completed season
            if (
                self.status == self.Status.COMPLETED.value
                and tv_metadata["details"]["seasons"] > 1
            ):
                status = self.Status.IN_PROGRESS.value
            else:
                status = self.status

            item, _ = Item.objects.get_or_create(
                media_id=self.item.media_id,
                source=Sources.TMDB.value,
                media_type=MediaTypes.TV.value,
                defaults={
                    "title": tv_metadata["title"],
                    "image": tv_metadata["image"],
                },
            )

            tv = TV(
                item=item,
                score=None,
                status=status,
                notes="",
                user=self.user,
            )

            # save_base to avoid custom save method
            TV.save_base(tv)

            logger.info("%s did not exist, it was created successfully.", tv)

        return tv

    def get_remaining_eps(self, season_metadata):
        """Return episodes needed to complete a season."""
        max_episode_number = Episode.objects.filter(related_season=self).aggregate(
            max_episode_number=Max("item__episode_number"),
        )["max_episode_number"]

        if max_episode_number is None:
            max_episode_number = 0

        episodes_to_create = []
        today = timezone.now().date()

        # Create Episode objects for the remaining episodes
        for episode in reversed(season_metadata["episodes"]):
            if episode["episode_number"] <= max_episode_number:
                break

            item = self.get_episode_item(episode["episode_number"], season_metadata)

            episode_db = Episode(
                related_season=self,
                item=item,
                end_date=today,
            )
            episodes_to_create.append(episode_db)

        return episodes_to_create

    def get_episode_item(self, episode_number, season_metadata=None):
        """Get the episode item instance, create it if it doesn't exist."""
        if not season_metadata:
            season_metadata = providers.services.get_media_metadata(
                MediaTypes.SEASON.value,
                self.item.media_id,
                self.item.source,
                [self.item.season_number],
            )

        image = settings.IMG_NONE
        for episode in season_metadata["episodes"]:
            if episode["episode_number"] == episode_number:
                if episode.get("still_path"):
                    image = (
                        f"https://image.tmdb.org/t/p/original{episode['still_path']}"
                    )
                elif "image" in episode:
                    # for manual seasons
                    image = episode["image"]
                else:
                    image = settings.IMG_NONE
                break

        item, _ = Item.objects.get_or_create(
            media_id=self.item.media_id,
            source=self.item.source,
            media_type=MediaTypes.EPISODE.value,
            season_number=self.item.season_number,
            episode_number=episode_number,
            defaults={
                "title": self.item.title,
                "image": image,
            },
        )

        return item


class Episode(models.Model):
    """Model for episodes of a season."""

    history = HistoricalRecords(
        cascade_delete_history=True,
        excluded_fields=["item", "related_season"],
    )

    item = models.ForeignKey(Item, on_delete=models.CASCADE, null=True)
    related_season = models.ForeignKey(
        Season,
        on_delete=models.CASCADE,
        related_name="episodes",
    )
    end_date = models.DateField(null=True, blank=True)
    repeats = models.PositiveIntegerField(default=0)

    class Meta:
        """Limit the uniqueness of episodes.

        Only one episode per season can have the same episode number.
        """

        ordering = ["related_season", "item"]
        constraints = [
            models.UniqueConstraint(
                fields=["related_season", "item"],
                name="%(app_label)s_episode_unique_season_item",
            ),
        ]

    def __str__(self):
        """Return the season and episode number."""
        return self.item.__str__()

    def save(self, *args, **kwargs):
        """Save the episode instance."""
        super().save(*args, **kwargs)

        if self.related_season.status in (
            Media.Status.IN_PROGRESS.value,
            Media.Status.REPEATING.value,
        ):
            season_number = self.item.season_number
            tv_with_seasons_metadata = providers.services.get_media_metadata(
                "tv_with_seasons",
                self.item.media_id,
                self.item.source,
                [season_number],
            )
            season_metadata = tv_with_seasons_metadata[f"season/{season_number}"]
            max_progress = len(season_metadata["episodes"])
            total_repeats = self.related_season.episodes.aggregate(
                total_repeats=Sum("repeats"),
            )["total_repeats"]

            # clear prefetch cache to get the updated episodes
            self.related_season.refresh_from_db()

            total_watches = self.related_season.progress + total_repeats

            if total_watches >= max_progress * (self.related_season.repeats + 1):
                self.related_season.status = Media.Status.COMPLETED.value
                self.related_season.save_base(update_fields=["status"])

                last_season = tv_with_seasons_metadata["related"]["seasons"][-1][
                    "season_number"
                ]
                # mark the TV show as completed if it's the last season
                if season_number == last_season:
                    self.related_season.related_tv.status = Media.Status.COMPLETED.value
                    self.related_season.related_tv.save_base(update_fields=["status"])


class Manga(Media):
    """Model for manga."""

    tracker = FieldTracker()


class Anime(Media):
    """Model for anime."""

    tracker = FieldTracker()


class Movie(Media):
    """Model for movies."""

    tracker = FieldTracker()


class Game(Media):
    """Model for games."""

    tracker = FieldTracker()

    @property
    def formatted_progress(self):
        """Return progress in hours:minutes format."""
        return app.helpers.minutes_to_hhmm(self.progress)

    def increase_progress(self):
        """Increase the progress of the media by 30 minutes."""
        self.progress += 30
        self.save()
        logger.info("Watched %s E%s", self, self.progress)

    def decrease_progress(self):
        """Decrease the progress of the media by 30 minutes."""
        self.progress -= 30
        self.save()
        logger.info("Unwatched %s E%s", self, self.progress + 1)


class Book(Media):
    """Model for books."""

    tracker = FieldTracker()


class Comic(Media):
    """Model for comics."""

    tracker = FieldTracker()
