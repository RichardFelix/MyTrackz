import logging

from django.core.cache import cache
from django.utils import timezone

import app
from app.models import MediaTypes, Sources, Status

logger = logging.getLogger(__name__)


class BaseWebhookProcessor:
    """Base class for webhook processors."""

    MEDIA_TYPE_MAPPING = {
        "Episode": MediaTypes.TV.value,
        "Movie": MediaTypes.MOVIE.value,
    }

    def process_payload(self, payload, user):
        """Process webhook payload."""
        raise NotImplementedError

    def _extract_external_ids(self, payload):
        """Extract external IDs from payload."""
        raise NotImplementedError

    def _get_media_type(self, payload):
        """Get media type from payload."""
        raise NotImplementedError

    def _is_supported_event(self, event_type):
        """Check if event type is supported."""
        raise NotImplementedError

    def _process_media(self, payload, user, ids):
        """Route processing based on media type."""
        media_type = self._get_media_type(payload)
        if not media_type:
            logger.info("Ignoring unsupported media type")
            return

        if media_type == MediaTypes.TV.value:
            self._process_tv(payload, user, ids)
        elif media_type == MediaTypes.MOVIE.value:
            self._process_movie(payload, user, ids)

    def _process_tv(self, payload, user, ids):
        media_id, season_number, episode_number = self._find_tv_media_id(ids)
        if not media_id:
            logger.info("No TMDB ID found for TV show")
            return

        tvdb_id = app.providers.tmdb.tv_with_seasons(media_id, [season_number])[
            "tvdb_id"
        ]

        if tvdb_id and user.anime_enabled:
            mapping_data = self._fetch_mapping_data()
            mal_id, episode_offset = self._get_mal_id_from_tvdb(
                mapping_data,
                int(tvdb_id),
                season_number,
                episode_number,
            )
            if mal_id:
                logger.info("Detected anime")
                self._handle_anime(mal_id, episode_offset, payload, user)
                return

        self._handle_tv_episode(media_id, season_number, episode_number, payload, user)

    def _process_movie(self, payload, user, ids):
        if ids["tmdb_id"]:
            tmdb_id = int(ids["tmdb_id"])
            mapping_data = self._fetch_mapping_data()
            mal_id = self._get_mal_id_from_tmdb_movie(mapping_data, tmdb_id)
            if mal_id and user.anime_enabled:
                logger.info("Detected anime movie")
                self._handle_anime(mal_id, 1, payload, user)
                return

            logger.info("Detected movie")
            self._handle_movie(tmdb_id, payload, user)
        elif ids["imdb_id"]:
            response = app.providers.tmdb.find(ids["imdb_id"], "imdb_id")
            if response.get("movie_results"):
                media_id = response["movie_results"][0]["id"]
                logger.info("Detected movie via IMDB")
                self._handle_movie(media_id, payload, user)

    def _find_tv_media_id(self, ids):
        """Find TV media ID from external IDs."""
        for ext_id, ext_type in [
            (ids["imdb_id"], "imdb_id"),
            (ids["tvdb_id"], "tvdb_id"),
        ]:
            if ext_id:
                response = app.providers.tmdb.find(ext_id, ext_type)
                if response.get("tv_episode_results"):
                    result = response["tv_episode_results"][0]
                    return (
                        result.get("show_id"),
                        result.get("season_number"),
                        result.get("episode_number"),
                    )
        return None, None, None

    def _fetch_mapping_data(self):
        """Fetch anime mapping data with caching."""
        data = cache.get("anime_mapping_data")
        if data is None:
            url = "https://raw.githubusercontent.com/Kometa-Team/Anime-IDs/refs/heads/master/anime_ids.json"
            data = app.providers.services.api_request("GITHUB", "GET", url)
            cache.set("anime_mapping_data", data)
        return data

    def _get_mal_id_from_tvdb(
        self,
        mapping_data,
        tvdb_id,
        season_number,
        episode_number,
    ):
        matching_entries = [
            entry
            for entry in mapping_data.values()
            if entry.get("tvdb_id") == tvdb_id
            and entry.get("tvdb_season") == season_number
            and "mal_id" in entry
        ]

        if not matching_entries:
            return None, None

        matching_entries.sort(key=lambda x: x.get("tvdb_epoffset", 0))
        for i, entry in enumerate(matching_entries):
            current_offset = entry.get("tvdb_epoffset", 0)
            next_offset = (
                matching_entries[i + 1].get("tvdb_epoffset", float("inf"))
                if i < len(matching_entries) - 1
                else float("inf")
            )

            if current_offset < episode_number <= next_offset:
                return entry["mal_id"], episode_number - current_offset

        return None, None

    def _get_mal_id_from_tmdb_movie(self, mapping_data, tmdb_movie_id):
        """Find MAL ID from TMDB movie mapping."""
        for entry in mapping_data.values():
            if entry.get("tmdb_movie_id") == tmdb_movie_id and "mal_id" in entry:
                return entry["mal_id"]
        return None

    def _handle_movie(self, media_id, payload, user):
        """Handle movie playback event."""
        movie_metadata = app.providers.tmdb.movie(media_id)
        movie_item, _ = app.models.Item.objects.get_or_create(
            media_id=media_id,
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            defaults={
                "title": movie_metadata["title"],
                "image": movie_metadata["image"],
            },
        )

        movie_instances = app.models.Movie.objects.filter(item=movie_item, user=user)
        current_instance = movie_instances.first()
        movie_played = self._is_played(payload)

        progress = 1 if movie_played else 0
        now = timezone.now().replace(second=0, microsecond=0)

        if current_instance and current_instance.status != Status.COMPLETED.value:
            current_instance.progress = progress
            if movie_played:
                current_instance.end_date = now
                current_instance.status = Status.COMPLETED.value
            elif current_instance.status != Status.IN_PROGRESS.value:
                current_instance.start_date = now
                current_instance.status = Status.IN_PROGRESS.value
            current_instance.save()
        else:
            app.models.Movie.objects.create(
                item=movie_item,
                user=user,
                progress=progress,
                status=Status.COMPLETED.value
                if movie_played
                else Status.IN_PROGRESS.value,
                start_date=now if not movie_played else None,
                end_date=now if movie_played else None,
            )

    def _handle_tv_episode(
        self,
        media_id,
        season_number,
        episode_number,
        payload,
        user,
    ):
        """Handle TV episode playback event."""
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

        tv_instance, created = app.models.TV.objects.get_or_create(
            item=tv_item,
            user=user,
            defaults={"status": Status.IN_PROGRESS.value},
        )

        if not created and tv_instance.status not in (
            Status.COMPLETED.value,
            Status.IN_PROGRESS.value,
        ):
            tv_instance.status = Status.IN_PROGRESS.value
            tv_instance.save()

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

        season_instance, _ = app.models.Season.objects.get_or_create(
            item=season_item,
            user=user,
            related_tv=tv_instance,
            defaults={"status": Status.IN_PROGRESS.value},
        )

        episode_item, _ = app.models.Item.objects.get_or_create(
            media_id=media_id,
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            season_number=season_number,
            episode_number=episode_number,
            defaults={
                "title": tv_metadata["title"],
                "image": season_metadata["image"],
            },
        )

        if self._is_played(payload):
            app.models.Episode.objects.create(
                item=episode_item,
                related_season=season_instance,
                end_date=timezone.now().replace(second=0, microsecond=0),
            )

    def _handle_anime(self, media_id, episode_number, payload, user):
        """Handle anime playback event."""
        anime_metadata = app.providers.mal.anime(media_id)
        anime_item, _ = app.models.Item.objects.get_or_create(
            media_id=media_id,
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            defaults={
                "title": anime_metadata["title"],
                "image": anime_metadata["image"],
            },
        )

        anime_instances = app.models.Anime.objects.filter(item=anime_item, user=user)
        current_instance = anime_instances.first()

        if not self._is_played(payload):
            episode_number = max(0, episode_number - 1)

        if current_instance and current_instance.status != Status.COMPLETED.value:
            current_instance.progress = episode_number
            current_instance.status = Status.IN_PROGRESS.value
            current_instance.save()
        else:
            app.models.Anime.objects.create(
                item=anime_item,
                user=user,
                progress=episode_number,
                status=Status.IN_PROGRESS.value,
            )

    def _is_played(self, payload):
        """Check if media is marked as played."""
        raise NotImplementedError
