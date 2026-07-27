import logging
import re
import unicodedata

import requests
from django.db import transaction
from django.utils import timezone

import app
from app.models import MediaTypes, Sources, Status
from app.providers import services
from app.providers import tvdb as tvdb_provider

from . import anime_mappings

logger = logging.getLogger(__name__)


class TVWebhookMixin:
    """TV-specific webhook processing."""

    def _process_tv(self, payload, user, ids):
        anidb_id = ids.get("anidb_id")
        if (
            user.anime_enabled
            and anidb_id
            and self._process_anidb_episode(anidb_id, payload, user)
        ):
            return

        tvdb_episode_id = ids.get("tvdb_id")
        if tvdb_episode_id:
            tvdb_episode = self._get_tvdb_episode(tvdb_episode_id)
            if not tvdb_episode:
                logger.warning(
                    "No TVDB episode metadata found for TVDB episode ID: %s",
                    tvdb_episode_id,
                )
            elif self._process_tvdb_episode(
                tvdb_episode, tvdb_episode_id, payload, user
            ):
                return
        else:
            logger.debug("No TVDB episode ID found for TV episode")

        imdb_id = ids.get("imdb_id")
        if imdb_id and self._process_imdb_episode(imdb_id, payload, user):
            return

        if self._process_tv_title_fallback(payload, user, ids.get("tmdb_id")):
            return

        if imdb_id:
            logger.warning(
                "No TV episode match found for IMDB ID %s or title fallback",
                imdb_id,
            )
        else:
            logger.warning("No TV episode match found through IDs or title fallback")

    def _process_anidb_episode(self, anidb_id, payload, user):
        """Process an anime episode through its AniDB mapping when possible."""
        episode_number = self._get_episode_number(payload)
        if not episode_number:
            logger.warning("No episode number found for AniDB ID: %s", anidb_id)
            return False

        mapping_data = anime_mappings.fetch_mapping_data()
        mal_id, mal_episode_number = anime_mappings.get_mal_id_from_anidb(
            mapping_data,
            anidb_id,
            episode_number,
        )
        if not mal_id:
            logger.info(
                "AniDB ID %s not found in mapping, falling through to TV processing",
                anidb_id,
            )
            return False

        logger.info(
            "Detected anime via AniDB ID: %s. Matching MAL ID: %s, Episode: %d",
            anidb_id,
            mal_id,
            mal_episode_number,
        )
        self._handle_anime(mal_id, mal_episode_number, payload, user)
        return True

    @staticmethod
    def _get_tvdb_episode(tvdb_episode_id):
        """Return TVDB episode metadata without aborting webhook fallback."""
        try:
            return tvdb_provider.episode(int(tvdb_episode_id))
        except (
            TypeError,
            ValueError,
            services.ProviderAPIError,
            requests.exceptions.RequestException,
        ):
            logger.exception(
                "TVDB lookup failed for episode ID %s; continuing fallbacks",
                tvdb_episode_id,
            )
            return None

    def _process_tvdb_episode(self, tvdb_episode, tvdb_episode_id, payload, user):
        """Process a TV episode using TVDB metadata."""
        if user.anime_enabled:
            mapping_data = anime_mappings.fetch_mapping_data()
            mal_id, episode_offset = anime_mappings.get_mal_id_from_tvdb(
                mapping_data,
                tvdb_episode["series_id"],
                tvdb_episode["season_number"],
                tvdb_episode["episode_number"],
            )
            if mal_id:
                logger.info(
                    "Detected anime episode via MAL ID: %s, Episode: %d",
                    mal_id,
                    episode_offset,
                )
                self._handle_anime(mal_id, episode_offset, payload, user)
                return True

        media_id, season_number, episode_number = self._find_tv_media_id(
            tvdb_episode_id,
            "tvdb_id",
        )
        if not media_id:
            try:
                media_id = tvdb_provider.series_tmdb_id(tvdb_episode["series_id"])
            except (
                services.ProviderAPIError,
                requests.exceptions.RequestException,
            ):
                logger.exception(
                    "TVDB series lookup failed for series ID %s",
                    tvdb_episode["series_id"],
                )
                media_id = None
            season_number = tvdb_episode["season_number"]
            episode_number = tvdb_episode["episode_number"]

        if not media_id:
            logger.warning(
                "No matching TMDB ID found for TVDB episode ID: %s", tvdb_episode_id
            )
            return False

        logger.info(
            "Detected TV episode via TMDB ID: %s, Season: %d, Episode: %d",
            media_id,
            season_number,
            episode_number,
        )
        self._handle_tv_episode(media_id, season_number, episode_number, payload, user)
        return True

    def _process_imdb_episode(self, imdb_id, payload, user):
        """Process a TV episode using IMDB as a TVDB fallback."""
        media_id, season_number, episode_number = self._find_tv_media_id(
            imdb_id,
            "imdb_id",
        )
        if not media_id:
            return False

        logger.info(
            "Detected TV episode via IMDB ID: %s, TMDB ID: %s, Season: %d, Episode: %d",
            imdb_id,
            media_id,
            season_number,
            episode_number,
        )
        self._handle_tv_episode(media_id, season_number, episode_number, payload, user)
        return True

    def _process_tv_title_fallback(self, payload, user, tmdb_episode_id):
        """Resolve a TV episode using its series title and episode coordinates."""
        coordinates = self._get_title_fallback_coordinates(payload)
        if not coordinates:
            return False
        series_title, season_number, episode_number = coordinates

        matches = self._find_title_fallback_matches(
            series_title,
            season_number,
            episode_number,
            tmdb_episode_id,
        )
        if matches is None:
            return False

        if not matches:
            if tmdb_episode_id:
                logger.warning(
                    "TMDB episode ID %s did not match any title-search candidate "
                    "for %s S%02dE%02d",
                    tmdb_episode_id,
                    series_title,
                    season_number,
                    episode_number,
                )
            else:
                logger.warning(
                    "No exact TMDB title match contains %s S%02dE%02d",
                    series_title,
                    season_number,
                    episode_number,
                )
            return False

        if len(matches) > 1:
            matches = self._disambiguate_by_episode_title(payload, matches)

        if len(matches) != 1:
            logger.warning(
                "Ambiguous TMDB title fallback for %s S%02dE%02d; skipping",
                series_title,
                season_number,
                episode_number,
            )
            return False

        media_id, tv_metadata, _episode_metadata = matches[0]
        if self._process_resolved_anime(
            tv_metadata,
            season_number,
            episode_number,
            payload,
            user,
        ):
            return True

        logger.info(
            "Resolved TV episode by title fallback: %s S%02dE%02d -> TMDB %s",
            series_title,
            season_number,
            episode_number,
            media_id,
        )
        self._handle_tv_episode(
            media_id,
            season_number,
            episode_number,
            payload,
            user,
        )
        return True

    def _get_title_fallback_coordinates(self, payload):
        """Return validated series, season, and episode coordinates."""
        series_title = self._get_series_title(payload)
        season_number = self._coerce_int(self._get_season_number(payload))
        episode_number = self._coerce_int(self._get_episode_number(payload))
        valid = (
            bool(series_title)
            and season_number is not None
            and season_number >= 0
            and episode_number is not None
            and episode_number > 0
        )
        if valid:
            return series_title, season_number, episode_number

        logger.warning(
            "Cannot use TV title fallback because series title, season, or "
            "episode coordinates are missing or invalid",
        )
        return None

    def _find_title_fallback_matches(
        self,
        series_title,
        season_number,
        episode_number,
        tmdb_episode_id,
    ):
        """Return TMDB candidates validated against title and episode metadata."""
        search_results = []
        for search_title in self._get_title_search_queries(series_title):
            try:
                search_results.extend(
                    app.providers.tmdb.search(
                        MediaTypes.TV.value,
                        search_title,
                        1,
                    ).get("results", []),
                )
            except (
                services.ProviderAPIError,
                requests.exceptions.RequestException,
            ):
                logger.exception(
                    "TMDB search failed during TV title fallback for %s S%02dE%02d",
                    search_title,
                    season_number,
                    episode_number,
                )
                return None

        matches = []
        seen_media_ids = set()
        normalized_series_title = self._normalize_series_title(series_title)
        for candidate in search_results:
            media_id = candidate.get("media_id")
            if not media_id or media_id in seen_media_ids:
                continue
            seen_media_ids.add(media_id)
            if (
                not tmdb_episode_id
                and self._normalize_series_title(candidate.get("title"))
                != normalized_series_title
            ):
                continue

            match = self._match_title_fallback_candidate(
                candidate,
                series_title,
                season_number,
                episode_number,
                tmdb_episode_id,
            )
            if match:
                matches.append(match)
        return matches

    @classmethod
    def _get_title_search_queries(cls, series_title):
        """Return title searches, retrying without a Plex-style trailing year."""
        queries = [series_title]
        title_without_year = cls._strip_trailing_year(series_title)
        if title_without_year != series_title.strip():
            queries.append(title_without_year)
        return queries

    @staticmethod
    def _strip_trailing_year(title):
        """Remove a trailing parenthetical year used to disambiguate Plex titles."""
        return re.sub(r"\s+\((?:19|20)\d{2}\)\s*$", "", str(title)).strip()

    @classmethod
    def _normalize_series_title(cls, title):
        """Normalize a series title after removing a Plex disambiguation year."""
        return cls._normalize_title(cls._strip_trailing_year(title))

    def _match_title_fallback_candidate(
        self,
        candidate,
        series_title,
        season_number,
        episode_number,
        tmdb_episode_id,
    ):
        """Validate one TMDB series candidate and return its episode metadata."""
        media_id = candidate["media_id"]
        try:
            tv_metadata = app.providers.tmdb.tv_with_seasons(
                media_id,
                [season_number],
            )
        except (services.ProviderAPIError, requests.exceptions.RequestException):
            logger.info(
                "TMDB candidate %s has no usable season %s for %s",
                media_id,
                season_number,
                series_title,
            )
            return None

        season_metadata = tv_metadata.get(f"season/{season_number}")
        if not season_metadata:
            return None

        episode_metadata = next(
            (
                episode
                for episode in season_metadata.get("episodes", [])
                if self._coerce_int(episode.get("episode_number")) == episode_number
            ),
            None,
        )
        if not episode_metadata:
            return None
        if tmdb_episode_id and str(episode_metadata.get("id")) != str(tmdb_episode_id):
            return None
        return media_id, tv_metadata, episode_metadata

    def _disambiguate_by_episode_title(self, payload, matches):
        """Use an episode title to narrow otherwise-valid series matches."""
        episode_title = self._get_episode_title(payload)
        if not episode_title:
            return matches

        normalized_episode_title = self._normalize_title(episode_title)
        title_matches = [
            match
            for match in matches
            if self._normalize_title(match[2].get("name"))
            == normalized_episode_title
        ]
        return title_matches or matches

    def _process_resolved_anime(
        self,
        tv_metadata,
        season_number,
        episode_number,
        payload,
        user,
    ):
        """Route a title-resolved episode through existing TVDB anime mappings."""
        tvdb_series_id = tv_metadata.get("tvdb_id")
        if not user.anime_enabled or not tvdb_series_id:
            return False

        mapping_data = anime_mappings.fetch_mapping_data()
        mal_id, mal_episode_number = anime_mappings.get_mal_id_from_tvdb(
            mapping_data,
            tvdb_series_id,
            season_number,
            episode_number,
        )
        if not mal_id:
            return False

        logger.info(
            "Detected title-resolved anime via MAL ID: %s, Episode: %d",
            mal_id,
            mal_episode_number,
        )
        self._handle_anime(mal_id, mal_episode_number, payload, user)
        return True

    @staticmethod
    def _normalize_title(title):
        """Normalize a title for conservative exact comparisons."""
        if not title:
            return ""
        normalized = unicodedata.normalize("NFKC", str(title)).casefold()
        return " ".join(normalized.split())

    @staticmethod
    def _coerce_int(value):
        """Return an integer value or None for malformed coordinates."""
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _find_tv_media_id(self, external_id, external_source):
        """Find TMDB TV episode metadata from an external ID."""
        if external_id:
            try:
                response = app.providers.tmdb.find(external_id, external_source)
            except (
                services.ProviderAPIError,
                requests.exceptions.RequestException,
            ):
                logger.exception(
                    "TMDB lookup failed for %s %s; continuing fallbacks",
                    external_source,
                    external_id,
                )
                return None, None, None
            if response.get("tv_episode_results"):
                result = response["tv_episode_results"][0]
                return (
                    result.get("show_id"),
                    result.get("season_number"),
                    result.get("episode_number"),
                )
        return None, None, None

    def _handle_tv_episode(
        self,
        media_id,
        season_number,
        episode_number,
        payload,
        user,
    ):
        """Handle TV episode playback event."""
        if self._is_unplayed(payload):
            self._delete_tv_episode(media_id, season_number, episode_number, user)
            return

        tv_metadata = app.providers.tmdb.tv_with_seasons(media_id, [season_number])
        season_metadata = tv_metadata[f"season/{season_number}"]

        tv_item, _ = app.models.Item.objects.get_or_create(
            media_id=media_id,
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            defaults={
                "title": tv_metadata["title"],
                "image": tv_metadata["image"],
            },
        )

        tv_instance, tv_created = app.models.TV.objects.get_or_create(
            item=tv_item,
            user=user,
            defaults={"status": Status.IN_PROGRESS.value},
        )

        if tv_created:
            logger.info("Created new TV instance: %s", tv_metadata["title"])
        elif tv_instance.status != Status.IN_PROGRESS.value:
            tv_instance.status = Status.IN_PROGRESS.value
            tv_instance.save()
            logger.info(
                "Updated TV instance status to %s: %s",
                Status.IN_PROGRESS.value,
                tv_metadata["title"],
            )

        season_item, _ = app.models.Item.objects.get_or_create(
            media_id=media_id,
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            season_number=season_number,
            defaults={
                "title": tv_metadata["title"],
                "image": season_metadata["image"],
            },
        )

        season_instance, season_created = app.models.Season.objects.get_or_create(
            item=season_item,
            user=user,
            related_tv=tv_instance,
            defaults={"status": Status.IN_PROGRESS.value},
        )

        if season_created:
            logger.info(
                "Created new season instance: %s S%02d",
                tv_metadata["title"],
                season_number,
            )
        elif season_instance.status != Status.IN_PROGRESS.value:
            season_instance.status = Status.IN_PROGRESS.value
            season_instance.save()
            logger.info(
                "Updated season instance status to %s: %s S%02d",
                Status.IN_PROGRESS.value,
                tv_metadata["title"],
                season_number,
            )

        episode_item = season_instance.get_episode_item(episode_number, season_metadata)

        if self._is_played(payload):
            now = timezone.now().replace(second=0, microsecond=0)
            latest_episode = (
                app.models.Episode.objects.filter(
                    item=episode_item,
                    related_season=season_instance,
                )
                .order_by("-end_date")
                .first()
            )

            should_create = True
            # check for duplicate episode records,
            # sometimes webhooks are triggered multiple times #689
            if latest_episode and latest_episode.end_date:
                time_diff = abs((now - latest_episode.end_date).total_seconds())
                threshold = 5
                if time_diff < threshold:
                    should_create = False
                    logger.debug(
                        "Skipping duplicate episode record "
                        "(time difference: %d seconds): %s S%02dE%02d",
                        time_diff,
                        tv_metadata["title"],
                        season_number,
                        episode_number,
                    )

            if should_create:
                episode = app.models.Episode.objects.create(
                    item=episode_item,
                    related_season=season_instance,
                    end_date=now,
                )
                logger.info(
                    "Marked episode as played: %s S%02dE%02d",
                    tv_metadata["title"],
                    season_number,
                    episode_number,
                )
                self._mark_previous_episodes_after_webhook(
                    season_instance,
                    episode,
                    episode_number,
                    tv_metadata["title"],
                    user,
                )
        else:
            logger.debug(
                "Episode not marked as played: %s S%02dE%02d",
                tv_metadata["title"],
                season_number,
                episode_number,
            )

    @staticmethod
    def _mark_previous_episodes_after_webhook(
        season,
        episode,
        episode_number,
        series_title,
        user,
    ):
        """Apply the user's earlier-episode preference after a webhook watch."""
        if not user.auto_mark_previous_episodes:
            return

        try:
            with transaction.atomic():
                season.mark_previous_episodes_watched(
                    episode_number,
                    episode.end_date,
                )
        except (
            KeyError,
            ValueError,
            requests.exceptions.RequestException,
            services.ProviderAPIError,
        ):
            logger.warning(
                "Could not mark previous episodes after webhook for %s S%02dE%02d",
                series_title,
                season.item.season_number,
                episode_number,
                exc_info=True,
            )

    def _delete_tv_episode(self, media_id, season_number, episode_number, user):
        """Delete the latest tracked episode instance for an unplayed event."""
        episode = (
            app.models.Episode.objects.filter(
                related_season__user=user,
                item__media_id=media_id,
                item__source=Sources.TMDB.value,
                item__media_type=MediaTypes.EPISODE.value,
                item__season_number=season_number,
                item__episode_number=episode_number,
            )
            .order_by("-end_date", "-created_at")
            .first()
        )

        if not episode:
            logger.debug(
                "Episode marked as unplayed but no instance exists: %s S%02dE%02d",
                media_id,
                season_number,
                episode_number,
            )
            return

        episode.delete()
        logger.info(
            "Marked episode as unplayed: %s S%02dE%02d",
            media_id,
            season_number,
            episode_number,
        )
