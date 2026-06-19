from unittest.mock import MagicMock

from django.apps import AppConfig
from django.conf import settings


class AppConfig(AppConfig):
    """Default app config."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "app"

    def ready(self):
        """Import signals when the app is ready."""
        import app.signals  # noqa: F401, PLC0415

        # Disable the image-cache download task when testing
        if settings.TESTING:
            from app.tasks import cache_item_image  # noqa: PLC0415

            cache_item_image.delay = MagicMock()
