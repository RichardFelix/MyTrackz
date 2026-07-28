import json
import logging
import re
from enum import StrEnum

from app.models import MediaTypes

from .base import BaseWebhookProcessor

logger = logging.getLogger(__name__)


class PlexEvent(StrEnum):
    """Plex webhook event names."""

    MEDIA_PLAY = "media.play"
    MEDIA_SCROBBLE = "media.scrobble"


class PlexWebhookProcessor(BaseWebhookProcessor):
    """Processor for Plex webhook events."""

    def process_payload(self, payload, user):
        """Process the incoming Plex webhook payload."""
        logger.debug("Received Plex webhook payload: %s", json.dumps(payload, indent=2))

        event_type = payload.get("event")
        if not self._is_supported_event(payload.get("event")):
            logger.debug("Ignoring Plex webhook event type: %s", event_type)
            return

        payload_user = payload["Account"]["title"].strip().lower()
        if not self._is_valid_user(payload_user, user):
            logger.debug(
                "Ignoring Plex webhook event for user %s: not a valid user",
                payload_user,
            )
            return

        ids = self._extract_external_ids(payload)
        logger.info("Extracted IDs from payload: %s", ids)

        if not self._has_processable_identity(payload, ids):
            logger.warning(
                "Ignoring Plex webhook call because no usable media identity "
                "was found. Metadata summary: %s",
                self._get_identity_summary(payload),
            )
            return

        self._process_media(payload, user, ids)

    def _is_supported_event(self, event_type):
        return event_type in {PlexEvent.MEDIA_PLAY, PlexEvent.MEDIA_SCROBBLE}

    def _is_valid_user(self, payload_user, user):
        stored_usernames = [
            u.strip().lower()
            for u in (user.plex_usernames or "").split(",")
            if u.strip()
        ]
        logger.debug(
            "Checking if payload user '%s' is in stored usernames: %s",
            payload_user,
            stored_usernames,
        )
        return payload_user in stored_usernames

    def _is_played(self, payload):
        return payload["event"] == PlexEvent.MEDIA_SCROBBLE

    def _get_media_type(self, payload):
        metadata = payload["Metadata"]
        media_type = metadata.get("type")
        if not media_type:
            media_type = metadata.get("librarySectionType")

        # Live TV/DVR payloads can retain the TV library classification while
        # omitting or changing the item-level type.
        if str(metadata.get("librarySectionType", "")).lower() == "show":
            return MediaTypes.TV.value

        if not media_type:
            return None

        return self.MEDIA_TYPE_MAPPING.get(media_type.title())

    def _get_media_title(self, payload):
        """Get media title from payload."""
        title = None

        if self._get_media_type(payload) == MediaTypes.TV.value:
            series_name = payload["Metadata"].get("grandparentTitle")
            season_number = payload["Metadata"].get("parentIndex")
            episode_number = payload["Metadata"].get("index")
            title = self._format_tv_title(
                series_name,
                season_number,
                episode_number,
            )

        elif self._get_media_type(payload) == MediaTypes.MOVIE.value:
            title = payload["Metadata"].get("title")

        return title

    def _get_episode_number(self, payload):
        metadata = payload["Metadata"]
        episode_number = metadata.get("index")
        if episode_number is not None:
            return episode_number

        match = re.fullmatch(
            r"\s*(?:episode|ep|e)\s*#?\s*(\d+)\s*",
            str(metadata.get("title", "")),
            flags=re.IGNORECASE,
        )
        return match.group(1) if match else None

    def _get_series_title(self, payload):
        return payload["Metadata"].get("grandparentTitle")

    def _get_episode_title(self, payload):
        return payload["Metadata"].get("title")

    def _get_season_number(self, payload):
        metadata = payload["Metadata"]
        season_number = metadata.get("parentIndex")
        if season_number is not None:
            return season_number

        match = re.fullmatch(
            r"\s*season\s*#?\s*(\d+)\s*",
            str(metadata.get("parentTitle", "")),
            flags=re.IGNORECASE,
        )
        return match.group(1) if match else None

    def _get_air_date(self, payload):
        return payload["Metadata"].get("originallyAvailableAt")

    def _extract_external_ids(self, payload):
        guids = payload["Metadata"].get("Guid", [])
        guid = payload["Metadata"].get("guid", None)

        def get_id(prefix):
            return next(
                (
                    guid["id"].replace(f"{prefix}://", "")
                    for guid in guids
                    if guid["id"].startswith(f"{prefix}://")
                ),
                None,
            )

        def extract_hama_anidb_id(guid):
            """Extract the AniDB ID from a Hama agent GUID string.

            e.g., "com.plexapp.agents.hama://anidb-12834/1/2?lang=en" -> "12834"
            """
            if guid and "hama://anidb-" in guid:
                match = re.search(r"anidb-(\d+)", guid)
                if match:
                    return match.group(1)
            return None

        return {
            "tmdb_id": get_id("tmdb"),
            "imdb_id": get_id("imdb"),
            "tvdb_id": get_id("tvdb"),
            "anidb_id": extract_hama_anidb_id(guid),
        }

    @staticmethod
    def _get_identity_summary(payload):
        """Return non-sensitive fields needed to diagnose unmatched media."""
        metadata = payload.get("Metadata", {})
        fields = (
            "type",
            "librarySectionType",
            "title",
            "grandparentTitle",
            "parentTitle",
            "index",
            "parentIndex",
            "originallyAvailableAt",
        )
        return {field: metadata.get(field) for field in fields}
