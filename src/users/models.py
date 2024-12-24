from django.contrib.auth.models import AbstractUser
from django.db import models

from app.models import Item

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
