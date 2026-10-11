import contextlib

from django.apps import apps
from django.contrib import admin
from django.contrib.admin.sites import AlreadyRegistered

from app.models import (
    Episode,
    Genre,
    Item,
    SneakerProduct,
    UserMessage,
)


# Custom ModelAdmin classes with search functionality
@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    """Custom admin for Item model with search and filter options."""

    search_fields = ["title", "media_id", "source"]
    list_display = [
        "title",
        "media_id",
        "season_number",
        "episode_number",
        "media_type",
        "source",
    ]
    list_filter = ["media_type", "source"]


@admin.register(Episode)
class EpisodeAdmin(admin.ModelAdmin):
    """Custom admin for Episode model with search and filter options."""

    search_fields = ["item__title", "related_season__item__title"]
    list_display = ["__str__", "end_date"]


@admin.register(Genre)
class GenreAdmin(admin.ModelAdmin):
    """Custom admin for Genre model."""

    search_fields = ["name"]
    list_display = ["name"]


@admin.register(UserMessage)
class UserMessageAdmin(admin.ModelAdmin):
    """Custom admin for persistent user messages."""

    search_fields = ["user__username", "message"]
    list_display = ["message", "level", "user", "created_at", "shown_at"]
    list_filter = ["level", "shown_at"]


@admin.register(SneakerProduct)
class SneakerProductAdmin(admin.ModelAdmin):
    """Inspect snapshots without changing shared sneaker identities."""

    list_display = ["name", "brand", "style_code", "captured_at"]
    search_fields = ["name", "brand", "style_code"]

    def has_add_permission(self, request):  # noqa: ARG002
        """Snapshots are created through catalog lookup."""
        return False

    def has_change_permission(self, request, obj=None):  # noqa: ARG002
        """Keep the first lookup immutable for every collector."""
        return False


class MediaAdmin(admin.ModelAdmin):
    """Custom admin for regular media model with search and filter options."""

    search_fields = ["item__title", "user__username", "notes"]
    list_display = ["__str__", "status", "score", "user"]
    list_filter = ["status"]


# Register models with custom admin classes


# Auto-register remaining models
app_models = apps.get_app_config("app").get_models()
SpecialModels = [
    "Item",
    "Episode",
    "BasicMedia",
    "UserMessage",
    "Genre",
    "SneakerProduct",
]
for model in app_models:
    if (
        not model.__name__.startswith("Historical")
        and model.__name__ not in SpecialModels
    ):
        with contextlib.suppress(AlreadyRegistered):
            admin.site.register(model, MediaAdmin)
