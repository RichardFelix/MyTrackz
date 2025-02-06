from django.apps import apps

import app


class CalendarTriggerMixin:
    """Mixin to handle calendar trigger functionality for media models."""

    _disable_calendar_triggers = False  # Class-level flag for each model

    @classmethod
    def disable_calendar_triggers(cls):
        """Context manager to disable calendar triggers for this model."""
        return DisableCalendarTriggers(cls)


class DisableCalendarTriggers:
    """Context manager for disabling calendar triggers during bulk operations."""

    def __init__(self, *models):
        """Initialize with one or more models to disable triggers for.

        Args:
            *models: Variable number of model classes to disable triggers for.
                    If no models provided, affects all Media-based models.
        """
        self.models = models if models else self._get_all_media_models()
        self.original_values = {}

    def _get_all_media_models(self):
        """Get all non-abstract models that inherit from Media."""
        return [
            model
            for model in apps.get_app_config("app").get_models()
            if (
                isinstance(model, type)
                and issubclass(model, app.models.Media)
                and model != app.models.Media
                and not model._meta.abstract  # noqa: SLF001
            )
        ]

    def __enter__(self):
        """Disable calendar triggers for the specified models."""
        for model in self.models:
            if hasattr(model, "_disable_calendar_triggers"):
                self.original_values[model] = model._disable_calendar_triggers  # noqa: SLF001
                model._disable_calendar_triggers = True  # noqa: SLF001
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Restore original trigger values for the specified models."""
        for model, original_value in self.original_values.items():
            model._disable_calendar_triggers = original_value  # noqa: SLF001


def disable_all_calendar_triggers(*models):
    """
    Context manager to disable calendar triggers for media models.

    If no models are specified, disables triggers for all Media-based models.

    Usage:
        # Disable all Media-based models
        with disable_all_calendar_triggers():
            # bulk operations

        # Disable specific models
        with disable_all_calendar_triggers(TV, Movie):
            # specific operations
    """
    return DisableCalendarTriggers(*models)
