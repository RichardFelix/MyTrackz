from django.apps import apps
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import Max
from django_celery_beat.models import PeriodicTask
from django_celery_results.models import TaskResult

from app.models import Item
from users import helpers

layouts = [
    ("grid", "Grid"),
    ("list", "List"),
]


class User(AbstractUser):
    """Custom user model that saves the last media search type."""

    is_demo = models.BooleanField(default=False)

    last_search_type = models.CharField(
        max_length=10,
        default=Item.MediaTypes.TV.value,
        choices=Item.MediaTypes.choices,
    )

    tv_enabled = models.BooleanField(default=True)
    tv_layout = models.CharField(
        max_length=20,
        default="grid",
        choices=layouts,
    )

    season_enabled = models.BooleanField(default=True)
    season_layout = models.CharField(
        max_length=20,
        default="grid",
        choices=layouts,
    )

    movie_enabled = models.BooleanField(default=True)
    movie_layout = models.CharField(
        max_length=20,
        default="grid",
        choices=layouts,
    )

    anime_enabled = models.BooleanField(default=True)
    anime_layout = models.CharField(
        max_length=20,
        default="list",
        choices=layouts,
    )

    manga_enabled = models.BooleanField(default=True)
    manga_layout = models.CharField(
        max_length=20,
        default="list",
        choices=layouts,
    )

    game_enabled = models.BooleanField(default=True)
    game_layout = models.CharField(
        max_length=20,
        default="grid",
        choices=layouts,
    )

    book_enabled = models.BooleanField(default=True)
    book_layout = models.CharField(
        max_length=20,
        default="grid",
        choices=layouts,
    )

    hide_from_search = models.BooleanField(default=True)

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

    def get_layout(self, media_type):
        """Return the layout for the media type."""
        return getattr(self, f"{media_type}_layout")

    def get_layout_template(self, media_type):
        """Return the layout template for the media type."""
        template = {
            "grid": "app/media_grid.html",
            "list": "app/media_list.html",
        }
        return template[self.get_layout(media_type)]

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
            for media_type in Item.MediaTypes.values
            if media_type != "episode" and getattr(self, f"{media_type}_enabled")
        ]

    def get_import_tasks(self):
        """Return a list of import tasks and their schedules for the user."""
        import_tasks = {
            "trakt": "Import from Trakt",
            "simkl": "Import from SIMKL",
            "myanimelist": "Import from MyAnimeList",
            "anilist": "Import from AniList",
            "kitsu": "Import from Kitsu",
            "yamtrack": "Import from Yamtrack",
        }

        task_result_filter_text = f"'user_id': {self.id},"
        # Get latest task results
        latest_tasks = (
            TaskResult.objects.filter(
                task_kwargs__contains=task_result_filter_text,
                task_name__in=import_tasks.values(),
            )
            .values("task_name")
            .annotate(latest_task=Max("id"))
        )

        task_objects = TaskResult.objects.filter(
            id__in=[task["latest_task"] for task in latest_tasks],
        )

        task_map = {
            task.task_name: helpers.process_task_result(task) for task in task_objects
        }

        # Get periodic tasks with their crontab schedules
        periodic_tasks_filter_text = f'"user_id": {self.id},'
        periodic_tasks = PeriodicTask.objects.filter(
            task__in=import_tasks.values(),
            kwargs__contains=periodic_tasks_filter_text,
            enabled=True,
        ).select_related("crontab")

        # Build result dictionary
        result = {}
        for key, task_name in import_tasks.items():
            # Get all periodic tasks for this import type
            tasks_for_import = [pt for pt in periodic_tasks if pt.task == task_name]

            # Get schedule info for all tasks
            schedule_info_list = []
            for periodic_task in tasks_for_import:
                schedule_info = helpers.get_next_run_info(periodic_task)
                if schedule_info:
                    schedule_info_list.append(
                        {
                            "task": periodic_task,
                            "next_run_text": (
                                f"Periodic Import Active: Next import scheduled for "
                                f"{
                                    schedule_info['next_run'].strftime(
                                        '%Y-%m-%d, %I:%M:%S %p'
                                    )
                                } "
                                f"({schedule_info['frequency']})"
                            ),
                        },
                    )

            result[key] = {
                "last_run": task_map.get(task_name),
                "schedules": schedule_info_list,
            }

        return result
