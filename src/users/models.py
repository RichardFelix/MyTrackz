from django.apps import apps
from django.contrib.auth.models import AbstractUser
from django.db import models
from django_celery_beat.models import PeriodicTask
from django_celery_results.models import TaskResult

from app.models import MediaTypes
from users import helpers


class LayoutChoices(models.TextChoices):
    """Choices for media list layout options."""

    GRID = "grid", "Grid"
    TABLE = "table", "Table"


class CalendarLayoutChoices(models.TextChoices):
    """Choices for calendar layout options."""

    GRID = "grid", "Grid"
    LIST = "list", "List"


class ListSortChoices(models.TextChoices):
    """Choices for list sort options."""

    LAST_ITEM_ADDED = "last_item_added", "Last Item Added"
    NAME = "name", "Name"
    ITEMS_COUNT = "items_count", "Items Count"
    CREATION_ORDER = "creation_order", "Newest First"


class User(AbstractUser):
    """Custom user model."""

    is_demo = models.BooleanField(default=False)

    last_search_type = models.CharField(
        max_length=10,
        default=MediaTypes.TV.value,
        choices=MediaTypes.choices,
    )

    tv_enabled = models.BooleanField(default=True)
    tv_layout = models.CharField(
        max_length=20,
        default=LayoutChoices.GRID,
        choices=LayoutChoices.choices,
    )

    season_enabled = models.BooleanField(default=True)
    season_layout = models.CharField(
        max_length=20,
        default=LayoutChoices.GRID,
        choices=LayoutChoices.choices,
    )

    movie_enabled = models.BooleanField(default=True)
    movie_layout = models.CharField(
        max_length=20,
        default=LayoutChoices.GRID,
        choices=LayoutChoices.choices,
    )

    anime_enabled = models.BooleanField(default=True)
    anime_layout = models.CharField(
        max_length=20,
        default=LayoutChoices.TABLE,
        choices=LayoutChoices.choices,
    )

    manga_enabled = models.BooleanField(default=True)
    manga_layout = models.CharField(
        max_length=20,
        default=LayoutChoices.TABLE,
        choices=LayoutChoices.choices,
    )

    game_enabled = models.BooleanField(default=True)
    game_layout = models.CharField(
        max_length=20,
        default=LayoutChoices.GRID,
        choices=LayoutChoices.choices,
    )

    book_enabled = models.BooleanField(default=True)
    book_layout = models.CharField(
        max_length=20,
        default=LayoutChoices.GRID,
        choices=LayoutChoices.choices,
    )

    hide_from_search = models.BooleanField(default=True)

    calendar_layout = models.CharField(
        max_length=20,
        default=CalendarLayoutChoices.GRID,
        choices=CalendarLayoutChoices.choices,
    )

    lists_sort = models.CharField(
        max_length=20,
        default=ListSortChoices.LAST_ITEM_ADDED,
        choices=ListSortChoices.choices,
    )

    token = models.CharField(
        max_length=32,
        null=True,
        blank=True,
        unique=True,
        help_text="Token for external webhooks",
    )

    class Meta:
        """Meta options for the model."""

        ordering = ["username"]
        constraints = [
            models.CheckConstraint(
                name="last_search_type_valid",
                check=models.Q(last_search_type__in=MediaTypes.values),
            ),
            models.CheckConstraint(
                name="tv_layout_valid",
                check=models.Q(tv_layout__in=LayoutChoices.values),
            ),
            models.CheckConstraint(
                name="season_layout_valid",
                check=models.Q(season_layout__in=LayoutChoices.values),
            ),
            models.CheckConstraint(
                name="movie_layout_valid",
                check=models.Q(movie_layout__in=LayoutChoices.values),
            ),
            models.CheckConstraint(
                name="anime_layout_valid",
                check=models.Q(anime_layout__in=LayoutChoices.values),
            ),
            models.CheckConstraint(
                name="manga_layout_valid",
                check=models.Q(manga_layout__in=LayoutChoices.values),
            ),
            models.CheckConstraint(
                name="game_layout_valid",
                check=models.Q(game_layout__in=LayoutChoices.values),
            ),
            models.CheckConstraint(
                name="book_layout_valid",
                check=models.Q(book_layout__in=LayoutChoices.values),
            ),
            models.CheckConstraint(
                name="calendar_layout_valid",
                check=models.Q(calendar_layout__in=CalendarLayoutChoices.values),
            ),
            models.CheckConstraint(
                name="lists_sort_valid",
                check=models.Q(lists_sort__in=ListSortChoices.values),
            ),
        ]

    def get_layout(self, media_type):
        """Return the layout for the media type."""
        return getattr(self, f"{media_type}_layout")

    def set_layout(self, media_type, layout):
        """Set the layout for the media type."""
        setattr(self, f"{media_type}_layout", layout)
        self.save(update_fields=[f"{media_type}_layout"])

    def set_last_search_type(self, media_type):
        """Set the last search type, used for default search type."""
        self.last_search_type = media_type
        self.save(update_fields=["last_search_type"])

    def get_active_media_types(self):
        """Return a list of active media types."""
        return [
            apps.get_model(app_label="app", model_name=media_type)
            for media_type in MediaTypes.values
            if media_type != "episode" and getattr(self, f"{media_type}_enabled")
        ]

    def get_import_tasks(self):
        """Return import tasks history and schedules for the user."""
        import_tasks = {
            "trakt": "Import from Trakt",
            "simkl": "Import from SIMKL",
            "myanimelist": "Import from MyAnimeList",
            "anilist": "Import from AniList",
            "kitsu": "Import from Kitsu",
            "yamtrack": "Import from Yamtrack",
        }

        # Reverse mapping to get source from task name
        task_to_source = {v: k for k, v in import_tasks.items()}

        task_result_filter_text = f"'user_id': {self.id},"

        # Get all task results for this user
        task_results = TaskResult.objects.filter(
            task_kwargs__contains=task_result_filter_text,
            task_name__in=import_tasks.values(),
        ).order_by("-date_done")  # Most recent first

        # Build results list
        results = []
        for task in task_results:
            source = task_to_source.get(task.task_name, "unknown")
            processed_task = helpers.process_task_result(task)
            results.append(
                {
                    "task": processed_task,
                    "source": source,
                    "date": task.date_done,
                    "status": task.status,
                    "summary": processed_task.summary,
                    "errors": processed_task.errors,
                    "mode": processed_task.mode,
                },
            )

        # Get periodic tasks with their crontab schedules
        periodic_tasks_filter_text = f'"user_id": {self.id},'
        periodic_tasks = PeriodicTask.objects.filter(
            task__in=import_tasks.values(),
            kwargs__contains=periodic_tasks_filter_text,
            enabled=True,
        ).select_related("crontab")

        # Build schedules list
        schedules = []
        for periodic_task in periodic_tasks:
            source = task_to_source.get(periodic_task.task, "unknown")

            # Extract username from task name if available
            username = ""
            if " for " in periodic_task.name:
                username = periodic_task.name.split(" for ")[1].split(" at ")[0]

            schedule_info = helpers.get_next_run_info(periodic_task)
            if schedule_info:
                schedules.append(
                    {
                        "task": periodic_task,
                        "source": source,
                        "username": username,
                        "last_run": periodic_task.last_run_at,
                        "next_run": schedule_info["next_run"],
                        "schedule": schedule_info["frequency"],
                        "mode": schedule_info["mode"],
                    },
                )

        return {
            "results": results,
            "schedules": schedules,
        }
