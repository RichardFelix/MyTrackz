import datetime
import logging
import re

import requests
from bs4 import BeautifulSoup
from django.apps import apps
from django.conf import settings
from django.core.cache import cache

import app
from app.models import Media, MediaTypes, Sources
from integrations import helpers
from integrations.helpers import MediaImportError

logger = logging.getLogger(__name__)

TRAKT_API_BASE_URL = "https://api.trakt.tv"


class MediaProcessor:
    """Base class providing common functionality for processing media from Trakt."""

    def __init__(self, user, bulk_media, media_instances, warnings):
        """Initialize the media processor with user and media instances."""
        self.user = user
        self.bulk_media = bulk_media
        self.media_instances = media_instances
        self.warnings = warnings

    def process_entry(self, entry, defaults):
        """Process a single media entry with the given defaults."""
        raise NotImplementedError

    def _get_metadata(self, fetch_func, source, title, *args, **kwargs):
        """Fetch metadata from provider APIs with proper error handling."""
        try:
            return fetch_func(*args, **kwargs)
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == requests.codes.not_found:
                msg = f"{title}: Couldn't fetch metadata from {source} ({args[0]})"
                logger.warning(msg)
                raise MediaImportError(msg) from e
            raise
        except KeyError as e:
            msg = (
                f"{title}: Couldn't parse incomplete metadata from {source} ({args[0]})"
            )
            logger.warning(msg)
            raise MediaImportError(msg) from e

    def _get_date(self, date):
        """Convert ISO formatted date string to date object."""
        if date:
            return (
                datetime.datetime.strptime(date, "%Y-%m-%dT%H:%M:%S.%fZ")
                .replace(tzinfo=datetime.UTC)
                .astimezone(settings.TZ)
                .date()
            )
        return None


class TVProcessor(MediaProcessor):
    """Handles processing of TV show entries from Trakt."""

    def process_entry(self, entry, defaults):
        """Process a TV show entry, creating or updating as needed."""
        tmdb_id = (
            str(entry["show"]["ids"]["tmdb"]) if entry["show"]["ids"]["tmdb"] else None
        )
        trakt_title = entry["show"]["title"]

        if not tmdb_id:
            message = f"No TMDB ID found for {trakt_title}"
            raise MediaImportError(message)

        if tmdb_id in self.media_instances[MediaTypes.TV.value]:
            for attr, value in defaults.items():
                setattr(self.media_instances[MediaTypes.TV.value][tmdb_id], attr, value)
        else:
            self.prepare_new_tv(defaults, tmdb_id, trakt_title)

    def prepare_new_tv(self, defaults, tmdb_id, title):
        """Create a new TV show instance with metadata from TMDB."""
        metadata = self._get_metadata(
            app.providers.tmdb.tv,
            Sources.TMDB.label,
            title,
            tmdb_id,
        )

        item, _ = app.models.Item.objects.get_or_create(
            media_id=tmdb_id,
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            defaults={
                "title": metadata["title"],
                "image": metadata["image"],
            },
        )

        tv_instance = app.models.TV(
            item=item,
            user=self.user,
            **defaults,
        )
        self.bulk_media[MediaTypes.TV.value].append(tv_instance)
        self.media_instances[MediaTypes.TV.value][tmdb_id] = tv_instance


class MovieProcessor(MediaProcessor):
    """Handles processing of movie entries from Trakt."""

    def process_entry(self, entry, defaults):
        """Process a movie entry, creating or updating as needed."""
        tmdb_id = (
            str(entry["movie"]["ids"]["tmdb"])
            if entry["movie"]["ids"]["tmdb"]
            else None
        )
        trakt_title = entry["movie"]["title"]

        if not tmdb_id:
            message = f"No TMDB ID found for {trakt_title}"
            raise MediaImportError(message)

        if tmdb_id in self.media_instances[MediaTypes.MOVIE.value]:
            for attr, value in defaults.items():
                setattr(
                    self.media_instances[MediaTypes.MOVIE.value][tmdb_id],
                    attr,
                    value,
                )
        else:
            self._prepare_new_movie(defaults, tmdb_id, trakt_title)

    def _prepare_new_movie(self, defaults, tmdb_id, title):
        """Create a new movie instance with metadata from TMDB."""
        metadata = self._get_metadata(
            app.providers.tmdb.movie,
            Sources.TMDB.label,
            title,
            tmdb_id,
        )

        item, _ = app.models.Item.objects.get_or_create(
            media_id=tmdb_id,
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            defaults={
                "title": metadata["title"],
                "image": metadata["image"],
            },
        )

        movie_instance = app.models.Movie(
            item=item,
            user=self.user,
            **defaults,
        )
        self.bulk_media[MediaTypes.MOVIE.value].append(movie_instance)
        self.media_instances[MediaTypes.MOVIE.value][tmdb_id] = movie_instance


class AnimeProcessor(MediaProcessor):
    """Handles processing of anime entries using MAL mappings."""

    def __init__(self, user, bulk_media, media_instances, warnings, mal_mapping):
        """Initialize the anime processor with user and MAL mappings."""
        super().__init__(user, bulk_media, media_instances, warnings)
        self.mal_mapping = mal_mapping

    def process_entry(self, entry, defaults, mal_id):
        """Process an anime entry with the given MAL ID."""
        try:
            if "show" in entry:
                title = entry["show"]["title"]
            else:
                title = entry["movie"]["title"]

            if mal_id in self.media_instances[MediaTypes.ANIME.value]:
                for attr, value in defaults.items():
                    setattr(
                        self.media_instances[MediaTypes.ANIME.value][mal_id],
                        attr,
                        value,
                    )
                return True

            self._prepare_new_anime(defaults, mal_id, title)

        except Exception as e:
            logger.exception("Error processing anime entry")
            self.warnings.append(f"{title}: Error processing anime - {e!s}")
            return False
        else:
            return True

    def _prepare_new_anime(self, defaults, mal_id, title):
        """Create a new anime instance with metadata from MAL."""
        metadata = self._get_metadata(
            app.providers.mal.anime,
            Sources.MAL.label,
            title,
            mal_id,
        )

        item, _ = app.models.Item.objects.get_or_create(
            media_id=mal_id,
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            defaults={
                "title": metadata["title"],
                "image": metadata["image"],
            },
        )

        anime_instance = app.models.Anime(
            item=item,
            user=self.user,
            **defaults,
        )
        self.bulk_media[MediaTypes.ANIME.value].append(anime_instance)
        self.media_instances[MediaTypes.ANIME.value][mal_id] = anime_instance


class SeasonProcessor(MediaProcessor):
    """Handles processing of TV season entries from Trakt."""

    def process_entry(self, entry, defaults):
        """Process a season entry, creating or updating as needed."""
        tmdb_id = (
            str(entry["show"]["ids"]["tmdb"]) if entry["show"]["ids"]["tmdb"] else None
        )
        season_number = entry["season"]["number"]
        trakt_title = entry["show"]["title"]

        if not tmdb_id:
            message = f"No TMDB ID found for {trakt_title} S{season_number}"
            raise MediaImportError(message)

        season_key = (tmdb_id, season_number)
        if season_key in self.media_instances[MediaTypes.SEASON.value]:
            for attr, value in defaults.items():
                setattr(
                    self.media_instances[MediaTypes.SEASON.value][season_key],
                    attr,
                    value,
                )
        else:
            self._prepare_new_season(
                defaults,
                tmdb_id,
                season_number,
                trakt_title,
            )

    def _prepare_new_season(self, defaults, tmdb_id, season_number, title):
        """Create a new season instance with metadata from TMDB."""
        metadata = self._get_metadata(
            app.providers.tmdb.tv_with_seasons,
            Sources.TMDB.label,
            title,
            tmdb_id,
            [season_number],
        )

        if tmdb_id not in self.media_instances[MediaTypes.TV.value]:
            tv_processor = TVProcessor(
                self.user,
                self.bulk_media,
                self.media_instances,
                self.warnings,
            )
            tv_processor.prepare_new_tv(
                {"status": Media.Status.IN_PROGRESS.value},
                tmdb_id,
                title,
            )

        tv_instance = self.media_instances[MediaTypes.TV.value][tmdb_id]
        season_metadata = metadata[f"season/{season_number}"]

        season_item, _ = app.models.Item.objects.get_or_create(
            media_id=tmdb_id,
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            season_number=season_number,
            defaults={
                "title": metadata["title"],
                "image": season_metadata["image"],
            },
        )

        season_instance = app.models.Season(
            item=season_item,
            user=self.user,
            related_tv=tv_instance,
            **defaults,
        )
        self.bulk_media[MediaTypes.SEASON.value].append(season_instance)
        self.media_instances[MediaTypes.SEASON.value][(tmdb_id, season_number)] = (
            season_instance
        )


class TraktImporter(MediaProcessor):
    """Main class handling the complete Trakt import process."""

    def __init__(self):
        """Initialize the Trakt importer with user and media processors."""
        self.processors = {
            "show": TVProcessor,
            "movie": MovieProcessor,
            "season": SeasonProcessor,
        }
        super().__init__(None, None, None, None)

    def importer(self, username, user, mode):
        """Import data from Trakt."""
        logger.info("Starting Trakt import for user %s with mode %s", username, mode)

        user_base_url = f"{TRAKT_API_BASE_URL}/users/{username}"
        mal_shows_map = get_mal_mappings(is_show=True)
        mal_movies_map = get_mal_mappings(is_show=False)

        bulk_media = {
            MediaTypes.TV.value: [],
            MediaTypes.MOVIE.value: [],
            MediaTypes.ANIME.value: [],
            MediaTypes.SEASON.value: [],
            MediaTypes.EPISODE.value: [],
        }
        media_instances = {
            MediaTypes.TV.value: {},
            MediaTypes.MOVIE.value: {},
            MediaTypes.ANIME.value: {},
            MediaTypes.SEASON.value: {},
            MediaTypes.EPISODE.value: {},
        }
        warnings = []

        try:
            self.user = user
            self.bulk_media = bulk_media
            self.media_instances = media_instances
            self.warnings = warnings

            self._process_watched_content(user_base_url, mal_shows_map, mal_movies_map)
            self._process_lists(user_base_url, mal_shows_map, mal_movies_map)

            helpers.update_season_references(bulk_media[MediaTypes.SEASON.value], user)
            helpers.update_episode_references(
                bulk_media[MediaTypes.EPISODE.value],
                user,
            )

            imported_counts = {}
            for media_type, bulk_list in bulk_media.items():
                if bulk_list:
                    imported_counts[media_type] = helpers.bulk_chunk_import(
                        bulk_list,
                        apps.get_model(app_label="app", model_name=media_type),
                        user,
                        mode,
                    )

            return (
                imported_counts.get(MediaTypes.TV.value, 0),
                imported_counts.get(MediaTypes.MOVIE.value, 0),
                imported_counts.get(MediaTypes.ANIME.value, 0),
                "\n".join(warnings),
            )

        except requests.exceptions.HTTPError as error:
            if error.response.status_code == requests.codes.not_found:
                msg = f"User slug {username} not found."
                raise MediaImportError(msg) from error
            raise

    def _process_watched_content(self, user_base_url, mal_shows_map, mal_movies_map):
        """Process watched shows and movies from Trakt."""
        watched_shows = get_response(f"{user_base_url}/watched/shows")
        self._process_watched_shows(watched_shows, mal_shows_map)

        watched_movies = get_response(f"{user_base_url}/watched/movies")
        self._process_watched_movies(watched_movies, mal_movies_map)

    def _process_watched_shows(self, watched, mal_mapping):
        """Process watched shows from Trakt."""
        logger.info("Processing watched shows")

        anime_processor = AnimeProcessor(
            self.user,
            self.bulk_media,
            self.media_instances,
            self.warnings,
            mal_mapping,
        )
        tv_processor = TVProcessor(
            self.user,
            self.bulk_media,
            self.media_instances,
            self.warnings,
        )

        for entry in watched:
            try:
                trakt_id = entry["show"]["ids"]["trakt"]
                trakt_title = entry["show"]["title"]
                tmdb_id = (
                    str(entry["show"]["ids"]["tmdb"])
                    if entry["show"]["ids"]["tmdb"]
                    else None
                )

                for season in entry["seasons"]:
                    season_number = season["number"]
                    mal_id = mal_mapping.get((trakt_id, season_number))

                    if mal_id and self.user.anime_enabled:
                        # Process as anime
                        defaults = self._get_anime_default_fields(
                            trakt_title,
                            season,
                            mal_id,
                        )
                        anime_processor.process_entry(entry, defaults, mal_id)
                    elif tmdb_id:
                        # Process as TV show
                        self._process_tv_season(
                            entry,
                            season,
                            tmdb_id,
                            trakt_title,
                            tv_processor,
                        )
                    else:
                        self.warnings.append(
                            f"No TMDB ID found for {trakt_title} in watch history",
                        )
                        break

            except MediaImportError as e:
                self.warnings.append(str(e))
            except Exception as e:
                logger.exception("Error processing %s", trakt_title)
                self.warnings.append(
                    f"{trakt_title}: Unexpected error: {e!s}, check logs for more data",
                )

        logger.info("Processed %d shows", len(watched))

    def _process_tv_season(
        self,
        entry,
        season,
        tmdb_id,
        title,
        tv_processor,
    ):
        """Process a single TV season and its episodes."""
        season_numbers = [s["number"] for s in entry["seasons"]]
        metadata = self._get_metadata(
            app.providers.tmdb.tv_with_seasons,
            Sources.TMDB.label,
            title,
            tmdb_id,
            season_numbers,
        )

        total_episodes_watched = sum(len(s["episodes"]) for s in entry["seasons"])
        status = get_status(
            total_episodes_watched,
            entry["plays"],
            metadata["max_progress"],
        )

        tv_processor.process_entry(entry, {"status": status})

        season_metadata = metadata[f"season/{season['number']}"]
        season_item, _ = app.models.Item.objects.get_or_create(
            media_id=tmdb_id,
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            season_number=season["number"],
            defaults={
                "title": metadata["title"],
                "image": season_metadata["image"],
            },
        )

        tv_instance = self.media_instances[MediaTypes.TV.value][tmdb_id]
        season_instance = app.models.Season(
            item=season_item,
            user=self.user,
            related_tv=tv_instance,
        )
        self.bulk_media[MediaTypes.SEASON.value].append(season_instance)
        self.media_instances[MediaTypes.SEASON.value][(tmdb_id, season["number"])] = (
            season_instance
        )

        total_plays = 0
        for episode in season["episodes"]:
            total_plays += episode["plays"]
            episode_number = episode["number"]
            ep_img = self._get_episode_image(episode_number, season_metadata)

            episode_item, _ = app.models.Item.objects.get_or_create(
                media_id=tmdb_id,
                source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                season_number=season["number"],
                episode_number=episode_number,
                defaults={
                    "title": metadata["title"],
                    "image": ep_img,
                },
            )

            episode_instance = app.models.Episode(
                item=episode_item,
                related_season=season_instance,
                end_date=self._get_date(episode["last_watched_at"]),
                repeats=episode["plays"] - 1,
            )
            self.bulk_media[MediaTypes.EPISODE.value].append(episode_instance)
            self.media_instances[MediaTypes.EPISODE.value][
                (tmdb_id, season["number"], episode_number)
            ] = episode_instance

        season_instance.status = get_status(
            season["episodes"][-1]["number"],
            total_plays,
            season_metadata["max_progress"],
        )

    def _get_episode_image(self, episode_number, season_metadata):
        """Extract episode image URL from season metadata."""
        for episode in season_metadata["episodes"]:
            if episode["episode_number"] == episode_number:
                if episode.get("still_path"):
                    return f"https://image.tmdb.org/t/p/w500{episode['still_path']}"
                break
        return settings.IMG_NONE

    def _get_anime_default_fields(self, title, season, mal_id):
        """Generate default tracking fields for anime entries."""
        metadata = self._get_metadata(
            app.providers.mal.anime,
            Sources.MAL.label,
            title,
            mal_id,
        )

        start_date = season["episodes"][0]["last_watched_at"]
        end_date = season["episodes"][-1]["last_watched_at"]
        anime_repeats = 0
        total_plays = 0

        for episode in season["episodes"]:
            current_watch = episode["last_watched_at"]
            start_date = min(start_date, current_watch)
            end_date = max(end_date, current_watch)
            anime_repeats = max(anime_repeats, episode["plays"] - 1)
            total_plays += episode["plays"]

        status = get_status(
            season["episodes"][-1]["number"],
            total_plays,
            metadata["max_progress"],
        )

        return {
            "progress": season["episodes"][-1]["number"],
            "status": status,
            "repeats": anime_repeats,
            "start_date": self._get_date(start_date),
            "end_date": self._get_date(end_date),
        }

    def _process_watched_movies(self, watched, mal_mapping):
        """Process watched movies, handling both anime and regular movies."""
        logger.info("Processing watched movies")

        anime_processor = AnimeProcessor(
            self.user,
            self.bulk_media,
            self.media_instances,
            self.warnings,
            mal_mapping,
        )
        movie_processor = MovieProcessor(
            self.user,
            self.bulk_media,
            self.media_instances,
            self.warnings,
        )

        for entry in watched:
            try:
                defaults = {
                    "progress": 1,
                    "status": Media.Status.COMPLETED.value,
                    "repeats": entry["plays"] - 1,
                    "start_date": self._get_date(entry["last_watched_at"]),
                    "end_date": self._get_date(entry["last_watched_at"]),
                }

                trakt_id = entry["movie"]["ids"]["trakt"]
                mal_id = mal_mapping.get((trakt_id, 1))

                if (
                    mal_id
                    and self.user.anime_enabled
                    and anime_processor.process_entry(entry, defaults, mal_id)
                ):
                    continue

                movie_processor.process_entry(entry, defaults)

            except MediaImportError as e:
                self.warnings.append(str(e))
            except Exception as e:
                trakt_title = entry["movie"]["title"]
                logger.exception("Error processing %s", trakt_title)
                self.warnings.append(f"{trakt_title}: Unexpected error: {e!s}")

        logger.info("Processed %d movies", len(watched))

    def _process_lists(self, user_base_url, mal_shows_map, mal_movies_map):
        """Process watchlist and ratings lists from Trakt."""
        watchlist = get_response(f"{user_base_url}/watchlist")
        self._process_list(watchlist, mal_shows_map, mal_movies_map, "watchlist")

        ratings = get_response(f"{user_base_url}/ratings")
        self._process_list(ratings, mal_shows_map, mal_movies_map, "ratings")

    def _process_list(self, entries, mal_shows_map, mal_movies_map, list_type):
        """Process a single list (watchlist or ratings)."""
        logger.info("Processing %s", list_type)

        processors = self._initialize_processors(mal_shows_map, mal_movies_map)

        for entry in entries:
            try:
                defaults = self._get_defaults(list_type, entry)
                self._process_entry(
                    entry,
                    defaults,
                    processors,
                    mal_shows_map,
                    mal_movies_map,
                )
            except MediaImportError as e:
                self.warnings.append(str(e))
            except Exception as error:
                entry_details = entry.get("show") or entry.get("movie")
                trakt_title = entry_details["title"]
                logger.exception("Error processing %s", trakt_title)
                self.warnings.append(f"{trakt_title}: Unexpected error: {error!s}")

        logger.info("Processed %d entries from %s", len(entries), list_type)

    def _initialize_processors(self, mal_shows_map, mal_movies_map):
        """Initialize processors for different media types."""
        return {
            "show": TVProcessor(
                self.user,
                self.bulk_media,
                self.media_instances,
                self.warnings,
            ),
            "movie": MovieProcessor(
                self.user,
                self.bulk_media,
                self.media_instances,
                self.warnings,
            ),
            "season": SeasonProcessor(
                self.user,
                self.bulk_media,
                self.media_instances,
                self.warnings,
            ),
            "anime_shows": AnimeProcessor(
                self.user,
                self.bulk_media,
                self.media_instances,
                self.warnings,
                mal_shows_map,
            ),
            "anime_movies": AnimeProcessor(
                self.user,
                self.bulk_media,
                self.media_instances,
                self.warnings,
                mal_movies_map,
            ),
        }

    def _get_defaults(self, list_type, entry):
        """Get default values based on list type."""
        if list_type == "watchlist":
            return {"status": Media.Status.PLANNING.value}
        if list_type == "ratings":
            return {"score": entry["rating"]}
        return {}

    def _process_entry(
        self,
        entry,
        defaults,
        processors,
        mal_shows_map,
        mal_movies_map,
    ):
        """Process a single entry."""
        entry_type = entry["type"]
        trakt_id = entry[entry_type]["ids"]["trakt"]

        if entry_type == "movie":
            self._process_movie_entry(
                entry,
                defaults,
                processors,
                mal_movies_map,
                trakt_id,
            )
        else:
            self._process_show_or_season_entry(
                entry,
                defaults,
                processors,
                mal_shows_map,
                trakt_id,
                entry_type,
            )

    def _process_movie_entry(
        self,
        entry,
        defaults,
        processors,
        mal_movies_map,
        trakt_id,
    ):
        """Process a movie entry."""
        mal_id = mal_movies_map.get((trakt_id, 1))
        if (
            mal_id
            and self.user.anime_enabled
            and processors["anime_movies"].process_entry(entry, defaults, mal_id)
        ):
            return
        processors["movie"].process_entry(entry, defaults)

    def _process_show_or_season_entry(
        self,
        entry,
        defaults,
        processors,
        mal_shows_map,
        trakt_id,
        entry_type,
    ):
        """Process a show or season entry."""
        if entry_type not in ["show", "season"]:
            return

        season_num = entry.get("season", {}).get("number", 1)
        mal_id = mal_shows_map.get((trakt_id, season_num))
        if mal_id and self.user.anime_enabled:
            processors["anime_shows"].process_entry(entry, defaults, mal_id)
        else:
            processors[entry_type].process_entry(entry, defaults)


def get_response(url):
    """Make authenticated request to Trakt API and return response."""
    headers = {
        "Content-Type": "application/json",
        "trakt-api-version": "2",
        "trakt-api-key": settings.TRAKT_API,
    }
    return app.providers.services.api_request(
        "TRAKT",
        "GET",
        url,
        headers=headers,
    )


def get_status(episodes_watched, total_plays, max_progress):
    """Determine media status based on watch progress."""
    if max_progress == episodes_watched:
        if total_plays % max_progress != 0:
            return Media.Status.REPEATING.value
        return Media.Status.COMPLETED.value
    return Media.Status.IN_PROGRESS.value


def download_and_parse_anitrakt_db(url):
    """Download and parse the AniTrakt database to get Trakt-MAL mappings."""
    response = requests.get(url, timeout=settings.REQUEST_TIMEOUT)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    trakt_to_mal = {}

    rows = soup.select("tbody tr")
    for row in rows:
        trakt_cell = row.find("td", id="trakt")
        trakt_link = trakt_cell.find("a")

        try:
            mal_cell = row.find_all("td")[1]
        except IndexError:
            continue

        trakt_url = trakt_link.get("href")
        trakt_id = int(re.search(r"/(?:shows|movies)/(\d+)", trakt_url).group(1))

        mal_links = mal_cell.find_all("a")
        for i, mal_link in enumerate(mal_links, start=1):
            mal_url = mal_link.get("href")
            mal_id = re.search(r"/anime/(\d+)", mal_url).group(1)
            trakt_to_mal[(trakt_id, i)] = mal_id

    return trakt_to_mal


def get_mal_mappings(is_show):
    """Get cached Trakt to MAL mappings from AniTrakt database."""
    cache_key = "anitrakt_shows_mapping" if is_show else "anitrakt_movies_mapping"
    url = (
        "https://anitrakt.huere.net/db/db_index_shows.php"
        if is_show
        else "https://anitrakt.huere.net/db/db_index_movies.php"
    )

    mapping = cache.get(cache_key)
    if mapping is None:
        mapping = download_and_parse_anitrakt_db(url)
        cache.set(cache_key, mapping, 60 * 60 * 24)
    return mapping


def importer(username, user, mode):
    """Legacy import function maintained for backward compatibility."""
    return TraktImporter().importer(username, user, mode)
