# media_webhooks/jellyfin.py
import json
import logging

from .base import BaseWebhookProcessor

logger = logging.getLogger(__name__)


class JellyfinWebhookProcessor(BaseWebhookProcessor):
    """Processor for Jellyfin webhook events."""

    def process_payload(self, payload, user):
        """Process the incoming Jellyfin webhook payload."""
        logger.debug(
            "Processing Jellyfin webhook payload: %s",
            json.dumps(payload, indent=2),
        )

        if not self._is_supported_event(payload.get("Event")):
            return

        ids = self._extract_external_ids(payload)
        if not any(ids.values()):
            return

        self._process_media(payload, user, ids)

    def _is_supported_event(self, event_type):
        return event_type in ("Play", "Stop")

    def _extract_external_ids(self, payload):
        provider_ids = payload["Item"].get("ProviderIds", {})
        return {
            "tmdb_id": provider_ids.get("Tmdb"),
            "imdb_id": provider_ids.get("Imdb"),
            "tvdb_id": provider_ids.get("Tvdb"),
        }

    def _get_media_type(self, payload):
        return self.MEDIA_TYPE_MAPPING.get(payload["Item"].get("Type"))

    def _is_played(self, payload):
        return payload["Item"]["UserData"]["Played"]

    def _is_unplayed(self, payload):
        return payload["Event"] == "MarkUnplayed"
