from django.apps import AppConfig


class UsersConfig(AppConfig):
    """Users app configuration."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "users"

    def ready(self):
        """Connect the signal receivers."""
        from users import signals  # noqa: F401, PLC0415
