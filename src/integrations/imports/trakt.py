import datetime
import logging
import re

import requests
from bs4 import BeautifulSoup
from django.apps import apps
from django.conf import settings
from django.core.cache import cache

import app
from app.models import Media
from integrations import helpers

logger = logging.getLogger(__name__)

TRAKT_API_BASE_URL = "https://api.trakt.tv"


def importer(username, user, mode):
    """Import the user's data from Trakt."""
    user_base_url = f"{TRAKT_API_BASE_URL}/users/{username}"
    mal_shows_map = get_mal_mappings(is_show=True)
    mal_movies_map = get_mal_mappings(is_show=False)

    # Initialize bulk media dictionary for all types
    bulk_media = {
        "tv": [],
        "movie": [],
        "anime": [],
        "season": [],
        "episode": [],
    }
    # Keep track of created instances for watchlist and ratings updates
    media_instances = {
        "tv": {},
        "movie": {},
        "anime": {},
        "season": {},
        "episode": {},
    }
    warnings = []

    try:
        watched_shows = get_response(f"{user_base_url}/watched/shows")
    except requests.exceptions.HTTPError as error:
        if error.response.status_code == requests.codes.not_found:
            msg = (
                f"User slug {username} not found. "
                "User slug can be found in the URL when viewing your Trakt profile."
            )
            raise ValueError(msg) from error
        raise

    # Process watched media first
    process_watched_shows(
        watched_shows,
        mal_shows_map,
        user,
        bulk_media,
        media_instances,
        warnings,
    )

    watched_movies = get_response(f"{user_base_url}/watched/movies")
    process_watched_movies(
        watched_movies,
        mal_movies_map,
        user,
        bulk_media,
        media_instances,
        warnings,
    )

    # Process lists that might modify existing entries
    watchlist = get_response(f"{user_base_url}/watchlist")
    process_list(
        watchlist,
        mal_shows_map,
        mal_movies_map,
        user,
        "watchlist",
        bulk_media,
        media_instances,
        warnings,
    )

    ratings = get_response(f"{user_base_url}/ratings")
    process_list(
        ratings,
        mal_shows_map,
        mal_movies_map,
        user,
        "ratings",
        bulk_media,
        media_instances,
        warnings,
    )

    # Update references before bulk creation
    helpers.update_season_references(bulk_media["season"], user)
    helpers.update_episode_references(bulk_media["episode"], user)

    # Bulk create all media types
    imported_counts = {}
    for media_type, bulk_list in bulk_media.items():
        if bulk_list:  # Only process non-empty lists
            imported_counts[media_type] = helpers.bulk_chunk_import(
                bulk_list,
                apps.get_model(app_label="app", model_name=media_type),
                user,
                mode,
            )

    return (
        imported_counts.get("tv", 0),
        imported_counts.get("movie", 0),
        imported_counts.get("anime", 0),
        "\n".join(warnings),
    )


def get_response(url):
    """Get the response from the Trakt API."""
    trakt_api = "b4d9702b11cfaddf5e863001f68ce9d4394b678926e8a3f64d47bf69a55dd0fe"
    headers = {
        "Content-Type": "application/json",
        "trakt-api-version": "2",
        "trakt-api-key": trakt_api,
    }
    return app.providers.services.api_request(
        "TRAKT",
        "GET",
        url,
        headers=headers,
    )


def process_watched_shows(
    watched,
    mal_mapping,
    user,
    bulk_media,
    media_instances,
    warnings,
):
    """Process watched shows from Trakt and prepare for bulk creation."""
    logger.info("Processing watched shows")

    for entry in watched:
        trakt_id = entry["show"]["ids"]["trakt"]
        trakt_title = entry["show"]["title"]

        try:
            for season in entry["seasons"]:
                season_number = season["number"]
                mal_id = mal_mapping.get((trakt_id, season_number))

                if mal_id and user.anime_enabled:
                    defaults = get_anime_default_fields(trakt_title, season, mal_id)
                    prepare_mal_anime(
                        entry,
                        mal_id,
                        user,
                        defaults,
                        bulk_media,
                        media_instances,
                    )
                else:
                    tmdb_id = entry["show"]["ids"]["tmdb"]
                    if not tmdb_id:
                        warnings.append(
                            f"No TMDB ID found for {trakt_title} in watch history",
                        )
                        break

                    # Only create TV and seasons for TMDB content
                    if tmdb_id not in media_instances["tv"]:
                        # Get metadata for all seasons at once
                        season_numbers = [s["number"] for s in entry["seasons"]]
                        metadata = get_metadata(
                            app.providers.tmdb.tv_with_seasons,
                            "TMDB",
                            trakt_title,
                            tmdb_id,
                            season_numbers,
                        )

                        tv_item, _ = app.models.Item.objects.get_or_create(
                            media_id=tmdb_id,
                            source="tmdb",
                            media_type="tv",
                            defaults={
                                "title": metadata["title"],
                                "image": metadata["image"],
                            },
                        )
                        total_episodes_watched = sum(
                            len(season["episodes"]) for season in entry["seasons"]
                        )

                        status = get_status(
                            total_episodes_watched,
                            entry["plays"],
                            metadata["max_progress"],
                        )

                        tv_instance = app.models.TV(
                            item=tv_item,
                            user=user,
                            status=status,
                        )
                        bulk_media["tv"].append(tv_instance)
                        media_instances["tv"][tmdb_id] = tv_instance

                    prepare_tmdb_season_and_episodes(
                        season,
                        metadata,
                        tmdb_id,
                        user,
                        bulk_media,
                        media_instances,
                    )

        except ValueError as e:
            warnings.append(str(e))
            continue

    logger.info("Processed %d shows", len(watched))


def get_anime_default_fields(title, season, mal_id):
    """Get the defaults tracking fields for watched anime."""
    metadata = get_metadata(app.providers.mal.anime, "MAL", title, mal_id)

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
        "start_date": get_date(start_date),
        "end_date": get_date(end_date),
    }


def get_status(episodes_wached, total_plays, max_progress):
    """Get the status of the media."""
    if max_progress == episodes_wached:
        if total_plays % max_progress != 0:
            return Media.Status.REPEATING.value
        return Media.Status.COMPLETED.value
    return Media.Status.IN_PROGRESS.value


def process_watched_movies(
    watched,
    mal_mapping,
    user,
    bulk_media,
    media_instances,
    warnings,
):
    """Process watched movies from Trakt and prepare for bulk creation."""
    logger.info("Processing watched movies")

    for entry in watched:
        try:
            update_or_prepare_movie(
                entry,
                user,
                {
                    "progress": 1,
                    "status": Media.Status.COMPLETED.value,
                    "repeats": entry["plays"] - 1,
                    "start_date": get_date(entry["last_watched_at"]),
                    "end_date": get_date(entry["last_watched_at"]),
                },
                "history",
                mal_mapping,
                bulk_media,
                media_instances,
            )
        except ValueError as e:
            warnings.append(str(e))

    logger.info("Processed %d movies", len(watched))


def process_list(
    entries,
    mal_shows_map,
    mal_movies_map,
    user,
    list_type,
    bulk_media,
    media_instances,
    warnings,
):
    """Process watchlist or ratings from Trakt."""
    logger.info("Processing %s", list_type)

    for entry in entries:
        try:
            if list_type == "watchlist":
                defaults = {"status": Media.Status.PLANNING.value}
            elif list_type == "ratings":
                defaults = {"score": entry["rating"]}

            if entry["type"] == "show":
                update_or_prepare_show(
                    entry,
                    user,
                    defaults,
                    list_type,
                    mal_shows_map,
                    bulk_media,
                    media_instances,
                )
            elif entry["type"] == "season":
                update_or_prepare_season(
                    entry,
                    user,
                    defaults,
                    list_type,
                    mal_shows_map,
                    bulk_media,
                    media_instances,
                )
            elif entry["type"] == "movie":
                update_or_prepare_movie(
                    entry,
                    user,
                    defaults,
                    list_type,
                    mal_movies_map,
                    bulk_media,
                    media_instances,
                )
        except ValueError as e:
            warnings.append(str(e))

    logger.info("Processed %d entries from %s", len(entries), list_type)


def update_or_prepare_show(
    entry,
    user,
    defaults,
    list_type,
    mal_shows_map,
    bulk_media,
    media_instances,
):
    """Update existing show or prepare new one for bulk creation."""
    trakt_id = entry["show"]["ids"]["trakt"]
    tmdb_id = entry["show"]["ids"]["tmdb"]
    mal_id = mal_shows_map.get((trakt_id, 1))

    if mal_id and user.anime_enabled:
        if mal_id in media_instances["anime"]:
            # Update existing instance
            for attr, value in defaults.items():
                setattr(media_instances["anime"][mal_id], attr, value)
        else:
            prepare_mal_anime(
                entry,
                mal_id,
                user,
                defaults,
                bulk_media,
                media_instances,
            )
    elif tmdb_id in media_instances["tv"]:
        # Update existing instance
        for attr, value in defaults.items():
            setattr(media_instances["tv"][tmdb_id], attr, value)
    else:
        prepare_tmdb_show(entry, user, defaults, list_type, bulk_media, media_instances)


def update_or_prepare_movie(
    entry,
    user,
    defaults,
    list_type,
    mal_mapping,
    bulk_media,
    media_instances,
):
    """Update existing movie or prepare new one for bulk creation."""
    trakt_id = entry["movie"]["ids"]["trakt"]
    tmdb_id = entry["movie"]["ids"]["tmdb"]
    mal_id = mal_mapping.get((trakt_id, 1))

    if mal_id and user.anime_enabled:
        if mal_id in media_instances["anime"]:
            # Update existing instance
            for attr, value in defaults.items():
                setattr(media_instances["anime"][mal_id], attr, value)
        else:
            prepare_mal_anime(
                entry,
                mal_id,
                user,
                defaults,
                bulk_media,
                media_instances,
            )
    elif tmdb_id in media_instances["movie"]:
        # Update existing instance
        for attr, value in defaults.items():
            setattr(media_instances["movie"][tmdb_id], attr, value)
    else:
        prepare_tmdb_movie(
            entry,
            user,
            defaults,
            list_type,
            bulk_media,
            media_instances,
        )


def update_or_prepare_season(
    entry,
    user,
    defaults,
    list_type,
    mal_shows_map,
    bulk_media,
    media_instances,
):
    """Update existing season or prepare new one for bulk creation."""
    trakt_id = entry["show"]["ids"]["trakt"]
    tmdb_id = entry["show"]["ids"]["tmdb"]
    season_number = entry["season"]["number"]
    mal_id = mal_shows_map.get((trakt_id, season_number))

    if mal_id and user.anime_enabled:
        if mal_id in media_instances["anime"]:
            # Update existing instance
            for attr, value in defaults.items():
                setattr(media_instances["anime"][mal_id], attr, value)
        else:
            prepare_mal_anime(
                entry,
                mal_id,
                user,
                defaults,
                bulk_media,
                media_instances,
            )
    else:
        season_key = (tmdb_id, season_number)
        if season_key in media_instances["season"]:
            # Update existing instance
            for attr, value in defaults.items():
                setattr(media_instances["season"][season_key], attr, value)
        else:
            prepare_tmdb_season(
                entry,
                user,
                defaults,
                list_type,
                bulk_media,
                media_instances,
            )


def prepare_tmdb_show(entry, user, defaults, list_type, bulk_media, media_instances):
    """Prepare TMDB show for bulk creation."""
    tmdb_id = entry["show"]["ids"]["tmdb"]
    trakt_title = entry["show"]["title"]

    if not tmdb_id:
        msg = f"No TMDB ID found for {trakt_title} in {list_type}"
        raise ValueError(msg)

    metadata = get_metadata(app.providers.tmdb.tv, "TMDB", trakt_title, tmdb_id)

    item, _ = app.models.Item.objects.get_or_create(
        media_id=tmdb_id,
        source="tmdb",
        media_type="tv",
        defaults={
            "title": metadata["title"],
            "image": metadata["image"],
        },
    )

    tv_instance = app.models.TV(
        item=item,
        user=user,
        **defaults,
    )
    bulk_media["tv"].append(tv_instance)
    media_instances["tv"][tmdb_id] = tv_instance


def prepare_tmdb_season_and_episodes(
    season,
    metadata,
    tmdb_id,
    user,
    bulk_media,
    media_instances,
):
    """Prepare TMDB season and its episodes for bulk creation."""
    season_number = season["number"]
    season_metadata = metadata[f"season/{season_number}"]

    # Create season item
    season_item, _ = app.models.Item.objects.get_or_create(
        media_id=tmdb_id,
        source="tmdb",
        media_type="season",
        season_number=season_number,
        defaults={
            "title": metadata["title"],
            "image": season_metadata["image"],
        },
    )

    tv_instance = media_instances["tv"][tmdb_id]
    season_instance = app.models.Season(
        item=season_item,
        user=user,
        related_tv=tv_instance,
    )
    bulk_media["season"].append(season_instance)
    media_instances["season"][(tmdb_id, season_number)] = season_instance

    # Prepare episodes
    total_plays = 0
    for episode in season["episodes"]:
        total_plays += episode["plays"]
        episode_number = episode["number"]
        ep_img = get_episode_image(episode_number, season_metadata)

        episode_item, _ = app.models.Item.objects.get_or_create(
            media_id=tmdb_id,
            source="tmdb",
            media_type="episode",
            season_number=season_number,
            episode_number=episode_number,
            defaults={
                "title": metadata["title"],
                "image": ep_img,
            },
        )

        episode_instance = app.models.Episode(
            item=episode_item,
            related_season=season_instance,
            end_date=get_date(episode["last_watched_at"]),
            repeats=episode["plays"] - 1,
        )
        bulk_media["episode"].append(episode_instance)
        media_instances["episode"][(tmdb_id, season_number, episode_number)] = (
            episode_instance
        )

    season_instance.status = get_status(
        season["episodes"][-1]["number"],
        total_plays,
        season_metadata["max_progress"],
    )


def prepare_tmdb_season(entry, user, defaults, list_type, bulk_media, media_instances):
    """Prepare TMDB season for bulk creation."""
    tmdb_id = entry["show"]["ids"]["tmdb"]
    trakt_title = entry["show"]["title"]
    season_number = entry["season"]["number"]

    if not tmdb_id:
        msg = f"No TMDB ID found for {trakt_title} S{season_number} in {list_type}"
        raise ValueError(msg)

    metadata = get_metadata(
        app.providers.tmdb.tv_with_seasons,
        "TMDB",
        trakt_title,
        tmdb_id,
        [season_number],
    )

    # Prepare TV show if it doesn't exist
    if tmdb_id not in media_instances["tv"]:
        tv_item, _ = app.models.Item.objects.get_or_create(
            media_id=tmdb_id,
            source="tmdb",
            media_type="tv",
            defaults={
                "title": metadata["title"],
                "image": metadata["image"],
            },
        )
        tv_instance = app.models.TV(
            item=tv_item,
            user=user,
            status=Media.Status.IN_PROGRESS.value,
        )
        bulk_media["tv"].append(tv_instance)
        media_instances["tv"][tmdb_id] = tv_instance
    else:
        tv_instance = media_instances["tv"][tmdb_id]

    season_metadata = metadata[f"season/{season_number}"]
    season_item, _ = app.models.Item.objects.get_or_create(
        media_id=tmdb_id,
        source="tmdb",
        media_type="season",
        season_number=season_number,
        defaults={
            "title": metadata["title"],
            "image": season_metadata["image"],
        },
    )

    season_instance = app.models.Season(
        item=season_item,
        user=user,
        related_tv=tv_instance,
        **defaults,
    )
    bulk_media["season"].append(season_instance)
    media_instances["season"][(tmdb_id, season_number)] = season_instance


def prepare_tmdb_movie(entry, user, defaults, list_type, bulk_media, media_instances):
    """Prepare TMDB movie for bulk creation."""
    tmdb_id = entry["movie"]["ids"]["tmdb"]
    trakt_title = entry["movie"]["title"]

    if not tmdb_id:
        msg = f"No TMDB ID found for {trakt_title} in {list_type}"
        raise ValueError(msg)

    metadata = get_metadata(app.providers.tmdb.movie, "TMDB", trakt_title, tmdb_id)

    item, _ = app.models.Item.objects.get_or_create(
        media_id=tmdb_id,
        source="tmdb",
        media_type="movie",
        defaults={
            "title": metadata["title"],
            "image": metadata["image"],
        },
    )

    movie_instance = app.models.Movie(
        item=item,
        user=user,
        **defaults,
    )
    bulk_media["movie"].append(movie_instance)
    media_instances["movie"][tmdb_id] = movie_instance


def prepare_mal_anime(entry, mal_id, user, defaults, bulk_media, media_instances):
    """Prepare MAL anime for bulk creation."""
    try:
        title = entry["show"]["title"]
    except KeyError:
        title = entry["movie"]["title"]

    metadata = get_metadata(app.providers.mal.anime, "MAL", title, mal_id)

    item, _ = app.models.Item.objects.get_or_create(
        media_id=mal_id,
        source="mal",
        media_type="anime",
        defaults={
            "title": metadata["title"],
            "image": metadata["image"],
        },
    )

    anime_instance = app.models.Anime(
        item=item,
        user=user,
        **defaults,
    )
    bulk_media["anime"].append(anime_instance)
    media_instances["anime"][mal_id] = anime_instance


def get_episode_image(episode_number, season_metadata):
    """Get episode image from metadata."""
    for episode in season_metadata["episodes"]:
        if episode["episode_number"] == episode_number:
            if episode.get("still_path"):
                return f"https://image.tmdb.org/t/p/w500{episode['still_path']}"
            break
    return settings.IMG_NONE


def get_metadata(fetch_func, source, title, *args, **kwargs):
    """Fetch metadata from various sources and handle errors."""
    try:
        return fetch_func(*args, **kwargs)
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == requests.codes.not_found:
            msg = f"{title}: Couldn't fetch metadata from {source} ({args[0]})"
            logger.warning(msg)
            raise ValueError(msg) from e
        raise
    except KeyError as e:
        msg = f"{title}: Couldn't parse incomplete metadata from {source} ({args[0]})"
        logger.warning(msg)
        raise ValueError(msg) from e


def download_and_parse_anitrakt_db(url):
    """Download and parse the AniTrakt database."""
    response = requests.get(url, timeout=settings.REQUEST_TIMEOUT)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    trakt_to_mal = {}

    # Find all table rows
    rows = soup.select("tbody tr")

    for row in rows:
        trakt_cell = row.find("td", id="trakt")
        trakt_link = trakt_cell.find("a")

        try:
            mal_cell = row.find_all("td")[1]
        # skip if there is no MAL cell
        except IndexError:
            continue

        trakt_url = trakt_link.get("href")
        trakt_id = int(
            re.search(r"/(?:shows|movies)/(\d+)", trakt_url).group(1),
        )

        # Extract all MAL links for different seasons
        mal_links = mal_cell.find_all("a")
        for i, mal_link in enumerate(mal_links, start=1):
            mal_url = mal_link.get("href")
            mal_id = re.search(r"/anime/(\d+)", mal_url).group(1)

            # Store as (trakt_id, season)
            trakt_to_mal[(trakt_id, i)] = mal_id

    return trakt_to_mal


def get_mal_mappings(is_show):
    """Get or update the mapping from AniTrakt to MAL."""
    if is_show:
        cache_key = "anitrakt_shows_mapping"
        url = "https://anitrakt.huere.net/db/db_index_shows.php"
    else:
        cache_key = "anitrakt_movies_mapping"
        url = "https://anitrakt.huere.net/db/db_index_movies.php"

    mapping = cache.get(cache_key)
    if mapping is None:
        mapping = download_and_parse_anitrakt_db(url)
        cache.set(cache_key, mapping, 60 * 60 * 24)  # 24 hours
    return mapping


def get_date(date):
    """Convert the date from Trakt to a date object."""
    if date:
        return (
            datetime.datetime.strptime(date, "%Y-%m-%dT%H:%M:%S.%fZ")
            .replace(tzinfo=datetime.UTC)
            .astimezone(settings.TZ)
            .date()
        )
    return None
