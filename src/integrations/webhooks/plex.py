import json
import logging

from .base import BaseWebhookProcessor

logger = logging.getLogger(__name__)


class PlexWebhookProcessor(BaseWebhookProcessor):
    """Processor for Plex webhook events."""

    def process_payload(self, payload, user):
        """Process the incoming Plex webhook payload."""
        logger.debug("Received Plex webhook payload: %s", json.dumps(payload, indent=2))

        if not self._is_supported_event(payload.get("event")):
            return

        if not self._is_valid_user(payload, user):
            return

        ids = self._extract_external_ids(payload)
        if not any(ids.values()):
            return

        self._process_media(payload, user, ids)

    def _is_supported_event(self, event_type):
        return event_type in ("media.scrobble", "media.play")

    def _is_valid_user(self, payload, user):
        incoming_username = payload["Account"]["title"].strip().lower()
        stored_usernames = [
            u.strip().lower()
            for u in (user.plex_usernames or "").split(",")
            if u.strip()
        ]
        return incoming_username in stored_usernames

    def _extract_external_ids(self, payload):
        guids = payload["Metadata"].get("Guid", [])

        def get_id(prefix):
            return next(
                (
                    guid["id"].replace(f"{prefix}://", "")
                    for guid in guids
                    if guid["id"].startswith(f"{prefix}://")
                ),
                None,
            )

        return {
            "tmdb_id": get_id("tmdb"),
            "imdb_id": get_id("imdb"),
            "tvdb_id": get_id("tvdb"),
        }

    def _get_media_type(self, payload):
        media_type = payload["Metadata"].get("type")
        if not media_type:
            return None

        return self.MEDIA_TYPE_MAPPING.get(media_type.title())

    def _is_played(self, payload):
        return payload["event"] == "media.scrobble"
