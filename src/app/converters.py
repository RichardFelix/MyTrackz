# https://docs.djangoproject.com/en/stable/topics/http/urls/#registering-custom-path-converters
from app import models


class MediaTypeChecker:
    """Check if the media type is valid."""

    regex = f"({'|'.join(models.MediaTypes.values)})"

    def to_python(self, value):
        """Return the media type if it is valid."""
        return value

    def to_url(self, value):
        """Return the media type if it is valid."""
        return value


class SourceChecker:
    """Check if the source is valid."""

    regex = f"({'|'.join(models.Sources.values)})"

    def to_python(self, value):
        """Return the source if it is valid."""
        return value

    def to_url(self, value):
        """Return the source if it is valid."""
        return value
