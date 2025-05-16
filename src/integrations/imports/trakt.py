import logging

import requests
from django.conf import settings
from django.db import transaction

import app
from app.models import Media, MediaTypes, Sources
from app.providers import services
from integrations.helpers import MediaImportError

logger = logging.getLogger(__name__)

TRAKT_API_BASE_URL = "https://api.trakt.tv"


def importer(username, user, mode):
    """Import the user's data from Trakt."""
    trakt_importer = TraktImporter(username, user, mode)

    with transaction.atomic():
        return trakt_importer.import_data()


class TraktImporter:
    """Class to handle importing user data from Trakt."""

    def __init__(self, username, user, mode):
        """Initialize the importer with user details and mode.

        Args:
            username (str): Trakt username to import from
            user: Django user object to import data for
            mode (str): Import mode ("new" or "overwrite")
        """
        self.username = username
        self.user = user
        self.mode = mode
        self.user_base_url = f"{TRAKT_API_BASE_URL}/users/{username}"
        self.warnings = []
        self.imported_instances = {
            MediaTypes.TV.value: set(),
            MediaTypes.SEASON.value: set(),
            MediaTypes.EPISODE.value: set(),
            MediaTypes.MOVIE.value: set(),
        }
        # Track existing media to handle "new" mode correctly
        self.existing_media = self._get_existing_media()
        logger.info(
            "Initialized Trakt importer for user %s with mode %s",
            username,
            mode,
        )

    def _get_existing_media(self):
        """Get all existing media for the user to check against during import."""
        existing = {
            MediaTypes.TV.value: set(),
            MediaTypes.SEASON.value: set(),
            MediaTypes.EPISODE.value: set(),
            MediaTypes.MOVIE.value: set(),
        }

        # Get existing TV shows
        for tv in app.models.TV.objects.filter(user=self.user).select_related("item"):
            existing[MediaTypes.TV.value].add(f"{tv.item.media_id}")

        # Get existing seasons
        for season in app.models.Season.objects.filter(user=self.user).select_related(
            "item",
        ):
            key = f"{season.item.media_id}:{season.item.season_number}"
            existing[MediaTypes.SEASON.value].add(key)

        # Get existing episodes
        for episode in app.models.Episode.objects.filter(
            related_season__user=self.user,
        ).select_related("item", "related_season"):
            key = (
                f"{episode.item.media_id}:"
                f"{episode.item.season_number}:{episode.item.episode_number}"
            )
            existing[MediaTypes.EPISODE.value].add(key)

        # Get existing movies
        for movie in app.models.Movie.objects.filter(user=self.user).select_related(
            "item",
        ):
            existing[MediaTypes.MOVIE.value].add(
                f"{movie.item.media_id}",
            )

        logger.info(
            "Found existing: %s TV shows, %s seasons, %s episodes, %s movies",
            len(existing[MediaTypes.TV.value]),
            len(existing[MediaTypes.SEASON.value]),
            len(existing[MediaTypes.EPISODE.value]),
            len(existing[MediaTypes.MOVIE.value]),
        )

        return existing

    def import_data(self):
        """Import all user data from Trakt."""
        self.import_history()
        self.import_watchlist()
        self.import_ratings()

        return (
            len(self.imported_instances[MediaTypes.TV.value]),
            len(self.imported_instances[MediaTypes.SEASON.value]),
            len(self.imported_instances[MediaTypes.EPISODE.value]),
            len(self.imported_instances[MediaTypes.MOVIE.value]),
            "\n".join(self.warnings),
        )

    def get_response(self, url):
        """Get the response from the Trakt API."""
        headers = {
            "Content-Type": "application/json",
            "trakt-api-version": "2",
            "trakt-api-key": settings.TRAKT_API,
        }
        return services.api_request(
            "TRAKT",
            "GET",
            url,
            headers=headers,
        )

    def import_history(self):
        """Import watch history from Trakt."""
        logger.info("Importing watch history for user %s", self.username)
        full_history = self.get_full_history()

        # Process in chronological order (oldest first)
        for entry in reversed(full_history):
            try:
                if entry["type"] == "movie":
                    self.process_movie(entry)
                elif entry["type"] == "episode":
                    self.process_episode(entry)
            except:
                logger.debug(
                    "Error processing entry %s",
                    entry,
                )
                raise

    def get_full_history(self):
        """Get the full watch history from Trakt.

        Returns:
            list: Complete watch history from Trakt
        """
        page = 1
        limit = 1000
        full_history = []

        while True:
            url = (
                f"{TRAKT_API_BASE_URL}/users/{self.username}/history"
                f"?page={page}&limit={limit}"
            )
            try:
                history_data = self.get_response(url)
            except requests.exceptions.HTTPError as error:
                if error.response.status_code == requests.codes.not_found:
                    msg = (
                        f"User slug {self.username} not found. "
                        "User slug can be found in your Trakt profile URL."
                    )
                    raise MediaImportError(msg) from error
                raise

            if not history_data or len(history_data) < limit:
                # We've reached the end of the data
                full_history.extend(history_data)
                break

            full_history.extend(history_data)
            page += 1
            logger.info(
                "Retrieved page %s of history for user %s",
                page - 1,
                self.username,
            )

        logger.info(
            "Retrieved %s history entries for user %s",
            len(full_history),
            self.username,
        )
        return full_history

    def get_tmdb_id(self, entry_data, media_type):
        """Extract TMDB ID from entry data."""
        if (
            "ids" in entry_data
            and "tmdb" in entry_data["ids"]
            and entry_data["ids"]["tmdb"]
        ):
            return str(entry_data["ids"]["tmdb"])

        self.warnings.append(
            f"No {Sources.TMDB.label} ID found for "
            f"{media_type} {entry_data.get('title', 'Unknown')}",
        )
        return None

    def get_metadata(self, media_type, tmdb_id, title, season_number=None):
        """Get metadata for a media item."""
        try:
            kwargs = {}
            if season_number is not None:
                kwargs["season_numbers"] = [season_number]

            return services.get_media_metadata(
                media_type,
                tmdb_id,
                Sources.TMDB.value,
                **kwargs,
            )
        except services.ProviderAPIError as error:
            if error.status_code == requests.codes.not_found:
                self.warnings.append(
                    f"{title} ({tmdb_id}): not found in {Sources.TMDB.label}",
                )
                return None
            raise

    def create_or_update_item(
        self,
        media_type,
        tmdb_id,
        metadata,
        season_number=None,
        episode_number=None,
    ):
        """Create or update an Item object."""
        item_kwargs = {
            "media_id": tmdb_id,
            "source": Sources.TMDB.value,
            "media_type": media_type,
        }

        if season_number is not None:
            item_kwargs["season_number"] = season_number

        if episode_number is not None:
            item_kwargs["episode_number"] = episode_number

        defaults = {
            "title": metadata["title"],
            "image": metadata["image"],
        }

        item, _ = app.models.Item.objects.get_or_create(
            **item_kwargs,
            defaults=defaults,
        )

        return item

    def should_process_media(
        self,
        media_type,
        tmdb_id,
        season_number=None,
        episode_number=None,
    ):
        """Determine if a media item should be processed.

        Based on mode and existing data.
        """
        # Create a key to check against existing media
        key = f"{tmdb_id}"
        if media_type == MediaTypes.SEASON.value:
            key = f"{key}:{season_number}"
        elif media_type == MediaTypes.EPISODE.value:
            key = f"{key}:{season_number}:{episode_number}"

        # Check if media exists
        exists = key in self.existing_media[media_type]

        if self.mode == "new" and exists:
            # In "new" mode, skip if media already exists
            logger.info(
                "Skipping existing %s: %s (mode: new)",
                media_type,
                key,
            )
            return False

        if self.mode == "overwrite" and exists:
            # In "overwrite" mode, delete existing media
            self.delete_existing_media(
                media_type,
                tmdb_id,
                season_number,
                episode_number,
            )

        return True

    def delete_existing_media(
        self,
        media_type,
        tmdb_id,
        season_number=None,
        episode_number=None,
    ):
        """Delete existing media based on type and identifiers."""
        if media_type == MediaTypes.MOVIE.value:
            app.models.Movie.objects.filter(
                item__media_id=tmdb_id,
                item__source=Sources.TMDB.value,
                user=self.user,
            ).delete()
            logger.info("Deleted existing movie: %s", tmdb_id)

        elif media_type == MediaTypes.EPISODE.value:
            app.models.Episode.objects.filter(
                item__media_id=tmdb_id,
                item__source=Sources.TMDB.value,
                item__season_number=season_number,
                item__episode_number=episode_number,
                related_season__user=self.user,
            ).delete()
            logger.info(
                "Deleted existing episode: %s, season: %s, episode: %s",
                tmdb_id,
                season_number,
                episode_number,
            )

    def process_movie(self, entry):
        """Process a single movie watch event."""
        movie = entry["movie"]
        tmdb_id = self.get_tmdb_id(movie, MediaTypes.MOVIE.value)
        if not tmdb_id:
            return

        # Check if we should process this movie based on mode
        if not self.should_process_media(MediaTypes.MOVIE.value, tmdb_id):
            return

        metadata = self.get_metadata(MediaTypes.MOVIE.value, tmdb_id, movie["title"])
        if not metadata:
            return

        item = self.create_or_update_item(MediaTypes.MOVIE.value, tmdb_id, metadata)
        watched_at = entry["watched_at"]

        movie_obj, created = app.models.Movie.objects.get_or_create(
            item=item,
            user=self.user,
            defaults={
                "end_date": watched_at,
                "status": Media.Status.COMPLETED.value,
            },
        )

        if not created:
            movie_obj.end_date = watched_at
            movie_obj.status = Media.Status.COMPLETED.value
            movie_obj.repeats += 1
            movie_obj.save()

        self.imported_instances[MediaTypes.MOVIE.value].add(item)

    def process_episode(self, entry):
        """Process a single episode watch event."""
        show = entry["show"]
        tmdb_id = self.get_tmdb_id(show, MediaTypes.TV.value)
        if not tmdb_id:
            return

        # Extract episode data
        season_number = entry["episode"]["season"]
        episode_number = entry["episode"]["number"]

        # Check if we should process this episode based on mode
        if not self.should_process_media(
            MediaTypes.EPISODE.value,
            tmdb_id,
            season_number,
            episode_number,
        ):
            return

        # Get TV metadata
        tv_metadata = self.get_metadata(MediaTypes.TV.value, tmdb_id, show["title"])
        if not tv_metadata:
            return

        # Get Season metadata
        season_metadata = self.get_metadata(
            MediaTypes.SEASON.value,
            tmdb_id,
            f"{show['title']} Season {season_number}",
            season_number,
        )
        if not season_metadata:
            return

        # Validate episode number exists in TMDB by checking episode numbers
        episode_exists = any(
            ep["episode_number"] == episode_number for ep in season_metadata["episodes"]
        )

        if not episode_exists:
            item_identifier = f"{show['title']} S{season_number}E{episode_number}"
            self.warnings.append(
                f"{item_identifier}: not found in TMDB {tmdb_id}.",
            )
            return

        episode_image = self.get_episode_image(episode_number, season_metadata)
        watched_at = entry["watched_at"]

        # Create TV item and object
        tv_item = self.create_or_update_item(MediaTypes.TV.value, tmdb_id, tv_metadata)
        tv_obj, _ = app.models.TV.objects.get_or_create(
            item=tv_item,
            user=self.user,
            defaults={
                "status": Media.Status.IN_PROGRESS.value,
            },
        )

        # Create Season item and object
        season_item = self.create_or_update_item(
            MediaTypes.SEASON.value,
            tmdb_id,
            season_metadata,
            season_number,
        )
        season_obj, _ = app.models.Season.objects.get_or_create(
            item=season_item,
            user=self.user,
            related_tv=tv_obj,
            defaults={
                "status": Media.Status.IN_PROGRESS.value,
            },
        )

        # Create Episode item and object
        episode_metadata = {
            "title": entry["episode"]["title"],
            "image": episode_image,
        }
        episode_item = self.create_or_update_item(
            MediaTypes.EPISODE.value,
            tmdb_id,
            episode_metadata,
            season_number,
            episode_number,
        )
        episode_obj, episode_created = app.models.Episode.objects.get_or_create(
            item=episode_item,
            related_season=season_obj,
            defaults={
                "end_date": watched_at,
            },
        )
        if not episode_created:
            episode_obj.end_date = watched_at
            episode_obj.repeats += 1
            episode_obj.save()

        # Add to imported instances
        self.imported_instances[MediaTypes.EPISODE.value].add(episode_item)
        self.imported_instances[MediaTypes.SEASON.value].add(season_item)
        self.imported_instances[MediaTypes.TV.value].add(tv_item)

    def get_episode_image(self, episode_number, season_metadata):
        """Extract episode image URL from season metadata."""
        for episode in season_metadata["episodes"]:
            if episode["episode_number"] == episode_number:
                if episode.get("still_path"):
                    return f"https://image.tmdb.org/t/p/w500{episode['still_path']}"
                break
        return settings.IMG_NONE

    def import_watchlist(self):
        """Import watchlist from Trakt."""
        logger.info("Importing watchlist for user %s", self.username)

        url = f"{self.user_base_url}/watchlist"
        watchlist_data = self.get_response(url)

        for entry in watchlist_data:
            try:
                self.process_watchlist_entry(entry)
            except:
                logger.debug(
                    "Error processing entry %s",
                    entry,
                )
                raise

    def process_watchlist_entry(self, entry):
        """Process a watchlist entry based on its type."""
        entry_type = entry["type"]

        if entry_type == "movie":
            self.process_media_item(
                entry["movie"],
                MediaTypes.MOVIE.value,
                app.models.Movie,
                {"status": Media.Status.PLANNING.value},
            )
        elif entry_type == "show":
            self.process_media_item(
                entry["show"],
                MediaTypes.TV.value,
                app.models.TV,
                {"status": Media.Status.PLANNING.value},
            )
        elif entry_type == "season":
            self.process_media_item(
                entry["show"],
                MediaTypes.SEASON.value,
                app.models.Season,
                {"status": Media.Status.PLANNING.value},
                entry["season"]["number"],
            )

    def import_ratings(self):
        """Import ratings from Trakt."""
        logger.info("Importing ratings for user %s", self.username)
        url = f"{self.user_base_url}/ratings"
        ratings_data = self.get_response(url)

        for entry in ratings_data:
            try:
                self.process_rating_entry(entry)
            except:
                logger.debug(
                    "Error processing entry %s",
                    entry,
                )
                raise

    def process_rating_entry(self, entry):
        """Process a rating entry based on its type."""
        entry_type = entry["type"]
        rating = entry["rating"]

        if entry_type == "movie":
            self.process_media_item(
                entry["movie"],
                MediaTypes.MOVIE.value,
                app.models.Movie,
                {"score": rating},
            )
        elif entry_type == "show":
            self.process_media_item(
                entry["show"],
                MediaTypes.TV.value,
                app.models.TV,
                {"score": rating},
            )
        elif entry_type == "season":
            self.process_media_item(
                entry["show"],
                MediaTypes.SEASON.value,
                app.models.Season,
                {"score": rating},
                entry["season"]["number"],
            )

    def process_media_item(
        self,
        media_data,
        media_type,
        model_class,
        defaults=None,
        season_number=None,
    ):
        """Process media items for watchlist and ratings."""
        tmdb_id = self.get_tmdb_id(media_data, media_type)
        if not tmdb_id:
            return

        # Create a key to check against existing media
        key = f"{tmdb_id}"
        if media_type == MediaTypes.SEASON.value:
            key = f"{key}:{season_number}"

        # Check if media exists
        exists = key in self.existing_media[media_type]

        # In "new" mode, skip if media already exists
        if self.mode == "new" and exists:
            logger.info(
                "Skipping existing %s: %s (mode: new)",
                media_type,
                key,
            )
            return

        metadata = self.get_metadata(
            media_type,
            tmdb_id,
            media_data["title"],
            season_number,
        )
        if not metadata:
            return

        # If we're processing a season, we need to create the TV show first
        if media_type == MediaTypes.SEASON.value:
            tv_metadata = self.get_metadata(
                MediaTypes.TV.value,
                tmdb_id,
                media_data["title"],
            )
            if not tv_metadata:
                return

            tv_item = self.create_or_update_item(
                MediaTypes.TV.value,
                tmdb_id,
                tv_metadata,
            )

            # Create or get the TV object
            tv_obj, _ = app.models.TV.objects.get_or_create(
                item=tv_item,
                user=self.user,
                defaults={"status": Media.Status.PLANNING.value},
            )

            self.imported_instances[MediaTypes.TV.value].add(tv_item)
            defaults["related_tv"] = tv_obj

        item = self.create_or_update_item(media_type, tmdb_id, metadata, season_number)

        model_class.objects.update_or_create(
            item=item,
            user=self.user,
            defaults=defaults,
        )

        self.imported_instances[media_type].add(item)
