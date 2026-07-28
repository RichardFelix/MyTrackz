import logging

from app.models import MediaTypes
from app.providers import tvdb as tvdb_provider  # noqa: F401

from .anime import AnimeWebhookMixin
from .movie import MovieWebhookMixin
from .tv import TVWebhookMixin

logger = logging.getLogger(__name__)


class BaseWebhookProcessor(TVWebhookMixin, MovieWebhookMixin, AnimeWebhookMixin):
    """Base class for webhook processors."""

    MEDIA_TYPE_MAPPING = {
        "Episode": MediaTypes.TV.value,
        "Movie": MediaTypes.MOVIE.value,
    }

    def process_payload(self, payload, user):
        """Process webhook payload."""
        raise NotImplementedError

    def _is_supported_event(self, event_type):
        """Check if event type is supported."""
        raise NotImplementedError

    def _is_played(self, payload):
        """Check if media is marked as played."""
        raise NotImplementedError

    def _is_unplayed(self, _payload):
        """Check if media is marked as unplayed."""
        return False

    def _extract_external_ids(self, payload):
        """Extract external IDs from payload."""
        raise NotImplementedError

    def _get_media_type(self, payload):
        """Get media type from payload."""
        raise NotImplementedError

    def _get_media_title(self, payload):
        """Get media title from payload."""
        raise NotImplementedError

    def _get_episode_number(self, payload):
        """Get episode number from payload."""
        raise NotImplementedError

    def _get_series_title(self, payload):
        """Get the parent TV series title from an episode payload."""
        raise NotImplementedError

    def _get_episode_title(self, payload):
        """Get the episode title from a TV payload."""
        raise NotImplementedError

    def _get_season_number(self, payload):
        """Get the season number from a TV payload."""
        raise NotImplementedError

    def _get_air_date(self, _payload):
        """Return an episode air date when the media server supplies one."""

    def _has_processable_identity(self, payload, ids):
        """Return whether a webhook has enough identity data to process."""
        if any(ids.values()):
            return True

        if self._get_media_type(payload) != MediaTypes.TV.value:
            return False

        series_title = self._get_series_title(payload)
        season_number = self._get_season_number(payload)
        episode_number = self._get_episode_number(payload)
        has_coordinates = season_number is not None and episode_number is not None
        return bool(series_title and (has_coordinates or self._get_air_date(payload)))

    @staticmethod
    def _format_tv_title(series_title, season_number, episode_number):
        """Format a TV episode label without failing on malformed coordinates."""
        try:
            return (
                f"{series_title} S{int(season_number):02d}E{int(episode_number):02d}"
            )
        except (TypeError, ValueError):
            return series_title

    def _process_media(self, payload, user, ids):
        """Route processing based on media type."""
        media_type = self._get_media_type(payload)
        if not media_type:
            logger.debug("Ignoring unsupported media type")
            return

        title = self._get_media_title(payload)
        logger.info("Received webhook for %s: %s", media_type, title)

        if media_type == MediaTypes.TV.value:
            self._process_tv(payload, user, ids)
        elif media_type == MediaTypes.MOVIE.value:
            self._process_movie(payload, user, ids)

    def _get_current_instance(self, model, media_id, source, media_type, user):
        """Return the newest tracked media instance without creating metadata."""
        return (
            model.objects.filter(
                item__media_id=media_id,
                item__source=source,
                item__media_type=media_type,
                user=user,
            )
            .order_by("-created_at")
            .first()
        )

    def _delete_media_instance(self, current_instance, media_label):
        """Delete an existing media instance for an unplayed event."""
        if not current_instance:
            logger.debug("%s marked as unplayed but no instance exists", media_label)
            return

        item = current_instance.item
        current_instance.delete()
        logger.info("Marked existing %s instance as unplayed: %s", media_label, item)
