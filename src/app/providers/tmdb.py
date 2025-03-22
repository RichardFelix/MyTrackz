import requests
from django.conf import settings
from django.core.cache import cache

from app.providers import services

base_url = "https://api.themoviedb.org/3"
base_params = {
    "api_key": settings.TMDB_API,
    "language": settings.TMDB_LANG,
}


def search(media_type, query):
    """Search for media on TMDB."""
    data = cache.get(f"search_{media_type}_{query}")

    if data is None:
        url = f"{base_url}/search/{media_type}"

        params = {
            **base_params,
            "query": query,
        }

        if settings.TMDB_NSFW:
            params["include_adult"] = "true"

        response = services.api_request("TMDB", "GET", url, params=params)

        # Sort results by popularity
        response = sorted(response["results"], key=lambda r: r["popularity"], reverse=True)
        data = [
            {
                "media_id": media["id"],
                "source": "tmdb",
                "media_type": media_type,
                "title": get_title(media),
                "image": get_image_url(media["poster_path"]),
            }
            for media in response
        ]

        cache.set(f"search_{media_type}_{query}", data)

    return data


def movie(media_id):
    """Return the metadata for the selected movie from The Movie Database."""
    data = cache.get(f"movie_{media_id}")

    if data is None:
        url = f"{base_url}/movie/{media_id}"
        params = {
            **base_params,
            "append_to_response": "recommendations",
        }
        response = services.api_request("TMDB", "GET", url, params=params)
        data = {
            "media_id": media_id,
            "source": "tmdb",
            "media_type": "movie",
            "title": response["title"],
            "max_progress": 1,
            "image": get_image_url(response["poster_path"]),
            "backdrop": get_backdrop_url(response),
            "synopsis": get_synopsis(response["overview"]),
            "genres": get_genres(response["genres"]),
            "details": {
                "format": "Movie",
                "release_date": get_start_date(response["release_date"]),
                "status": response["status"],
                "runtime": get_readable_duration(response["runtime"]),
                "studios": get_companies(response["production_companies"]),
                "country": get_country(response["production_countries"]),
                "languages": get_languages(response["spoken_languages"]),
            },
            "related": {
                "recommendations": get_related(
                    response["recommendations"]["results"][:15],
                    "movie",
                ),
            },
        }

        cache.set(f"movie_{media_id}", data)

    return data


def tv_with_seasons(media_id, season_numbers):
    """Return the metadata for the tv show with a season appended to the response."""
    if season_numbers == []:
        return tv(media_id)

    url = f"{base_url}/tv/{media_id}"
    base_append = "recommendations,external_ids"
    data = cache.get(f"tv_{media_id}", {})

    uncached_seasons = []
    for season_number in season_numbers:
        season_data = cache.get(f"season_{media_id}_{season_number}")

        if season_data:
            data[f"season/{season_number}"] = season_data
        else:
            uncached_seasons.append(season_number)

    # tmdb max remote request is 20 but we have recommendations and external_ids
    max_seasons_per_request = 18
    for i in range(0, len(uncached_seasons), max_seasons_per_request):
        season_subset = uncached_seasons[i : i + max_seasons_per_request]
        append_text = ",".join([f"season/{season}" for season in season_subset])

        params = {
            **base_params,
            "append_to_response": f"{base_append},{append_text}"
            if append_text
            else base_append,
        }

        response = services.api_request("TMDB", "GET", url, params=params)
        # tv show metadata is not in the response
        if "media_id" not in data:
            tv_data = process_tv(response)
            cache.set(f"tv_{media_id}", tv_data)

            # merge tv show metadata with seasons metadata
            data = tv_data | data

        # add seasons metadata to the response
        for season_number in season_subset:
            season_key = f"season/{season_number}"
            if season_key not in response:
                msg = f"Season {season_number} not found for TMDB TV show {media_id}"
                # Create a new response object with 404 status
                not_found_response = requests.Response()
                not_found_response.status_code = 404
                raise requests.exceptions.HTTPError(msg, response=not_found_response)

            season_data = process_season(response[season_key])
            season_data["media_id"] = media_id
            season_data["title"] = data["title"]
            season_data["external_ids"] = data["external_ids"]
            season_data["genres"] = data["genres"]
            season_data["backdrop"] = data["backdrop"]
            if season_data["synopsis"] == "No synopsis available.":
                season_data["synopsis"] = data["synopsis"]
            cache.set(f"season_{media_id}_{season_number}", season_data)
            data[season_key] = season_data

    return data


def tv(media_id):
    """Return the metadata for the selected tv show from The Movie Database."""
    data = cache.get(f"tv_{media_id}")

    if data is None:
        url = f"{base_url}/tv/{media_id}"
        params = {
            **base_params,
            "append_to_response": "recommendations,external_ids",
        }
        response = services.api_request("TMDB", "GET", url, params=params)
        data = process_tv(response)
        cache.set(f"tv_{media_id}", data)

    return data


def process_tv(response):
    """Process the metadata for the selected tv show from The Movie Database."""
    num_episodes = response["number_of_episodes"]
    return {
        "media_id": response["id"],
        "source": "tmdb",
        "media_type": "tv",
        "title": response["name"],
        "max_progress": num_episodes,
        "image": get_image_url(response["poster_path"]),
        "backdrop": get_backdrop_url(response),
        "synopsis": get_synopsis(response["overview"]),
        "genres": get_genres(response["genres"]),
        "details": {
            "format": "TV",
            "first_air_date": get_start_date(response["first_air_date"]),
            "last_air_date": response["last_air_date"],
            "status": response["status"],
            "seasons": response["number_of_seasons"],
            "episodes": num_episodes,
            "runtime": get_runtime_tv(response["episode_run_time"]),
            "studios": get_companies(response["production_companies"]),
            "country": get_country(response["production_countries"]),
            "languages": get_languages(response["spoken_languages"]),
        },
        "related": {
            "seasons": get_related(response["seasons"], "season", response),
            "recommendations": get_related(
                response["recommendations"]["results"][:15],
                "tv",
            ),
        },
        "external_ids": response["external_ids"],
    }


def process_season(response):
    """Process the metadata for the selected season from The Movie Database."""
    num_episodes = len(response["episodes"])
    return {
        "source": "tmdb",
        "media_type": "season",
        "season_title": response["name"],
        "max_progress": num_episodes,
        "image": get_image_url(response["poster_path"]),
        "season_number": response["season_number"],
        "synopsis": get_synopsis(response["overview"]),
        "details": {
            "first_air_date": get_start_date(response["air_date"]),
            "last_air_date": get_end_date(response),
            "episodes": num_episodes,
            "runtime": average_season_runtime(response),
            "total_runtime": total_season_runtime(response),
        },
        "episodes": response["episodes"],
    }


def get_format(media_type):
    """Return media_type capitalized."""
    if media_type == "tv":
        return "TV"
    return "Movie"


def get_image_url(path):
    """Return the image URL for the media."""
    # when no image, value from response is null
    # e.g movie: 445290
    if path:
        return f"https://image.tmdb.org/t/p/w500{path}"
    return settings.IMG_NONE


def get_backdrop_url(response):
    """Return the backdrop URL for the media."""
    # when no image, value from response is null
    # e.g movie: 445290
    if response["backdrop_path"]:
        return f"https://image.tmdb.org/t/p/w1280{response['backdrop_path']}"
    if response["poster_path"]:
        return f"https://image.tmdb.org/t/p/w1280{response['poster_path']}"

    return None


def get_title(response):
    """Return the title for the media."""
    # tv shows have name instead of title
    try:
        return response["title"]
    except KeyError:
        return response["name"]


def get_start_date(date):
    """Return the start date for the media."""
    # when unknown date, value from response is empty string
    # e.g movie: 445290
    if date == "":
        return None
    return date


def get_end_date(response):
    """Return the last air date for the season."""
    if response["episodes"]:
        return response["episodes"][-1]["air_date"]

    return None


def get_synopsis(text):
    """Return the synopsis for the media."""
    # when unknown synopsis, value from response is empty string
    # e.g movie: 445290
    if text == "":
        return "No synopsis available."
    return text


def get_readable_duration(duration):
    """Convert duration in minutes to a readable format."""
    # if unknown movie runtime, value from response is 0
    # e.g movie: 274613
    if duration:
        hours, minutes = divmod(int(duration), 60)
        return f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"
    return None


def get_runtime_tv(runtime):
    """Return the runtime for the tv show."""
    # when unknown runtime, value from response is empty list
    # e.g: tv:66672
    if runtime:
        return get_readable_duration(runtime[0])
    return None


def average_season_runtime(response):
    """Return the average runtime for the season."""
    # when unknown runtime, value from response is null
    episodes_with_runtime = [
        episode for episode in response["episodes"] if episode["runtime"] is not None
    ]

    if not episodes_with_runtime:
        return None

    return get_readable_duration(
        sum(episode["runtime"] for episode in episodes_with_runtime)
        / len(episodes_with_runtime),
    )


def total_season_runtime(response):
    """Return the total runtime for the season."""
    # when unknown runtime, value from response is null
    return get_readable_duration(
        sum(
            episode["runtime"]
            for episode in response["episodes"]
            if episode["runtime"] is not None
        ),
    )


def get_genres(genres):
    """Return the genres for the media."""
    # when unknown genres, value from response is empty list
    # e.g tv: 24795
    if genres:
        return [genre["name"] for genre in genres]
    return None


def get_country(countries):
    """Return the production country for the media."""
    # when unknown production country, value from response is empty list
    # e.g tv: 24795
    if countries:
        return countries[0]["name"]
    return None


def get_languages(languages):
    """Return the languages for the media."""
    # when unknown spoken languages, value from response is empty list
    # e.g tv: 24795
    if languages:
        return [language["english_name"] for language in languages]
    return None


def get_companies(companies):
    """Return the production companies for the media."""
    # when unknown production companies, value from response is empty list
    # e.g tv: 24795
    if companies:
        return [company["name"] for company in companies[:3]]
    return None


def get_related(related_medias, media_type, parent_response=None):
    """Return list of related media for the selected media."""
    related = []
    for media in related_medias:
        data = {
            "source": "tmdb",
            "media_type": media_type,
            "image": get_image_url(media["poster_path"]),
        }
        if media_type == "season":
            data["media_id"] = parent_response["id"]
            data["title"] = parent_response["name"]
            data["season_number"] = media["season_number"]
            data["season_title"] = media["name"]
            data["first_air_date"] = get_start_date(media["air_date"])
            data["max_progress"] = media["episode_count"]
        else:
            data["media_id"] = media["id"]
            data["title"] = get_title(media)
        related.append(data)
    return related


def process_episodes(season_metadata, episodes_in_db):
    """Process the episodes for the selected season."""
    episodes_metadata = []

    # Convert the queryset to a dictionary for efficient lookups
    tracked_episodes = {ep["item__episode_number"]: ep for ep in episodes_in_db}

    for episode in season_metadata["episodes"]:
        episode_number = episode["episode_number"]
        watched = episode_number in tracked_episodes

        episodes_metadata.append(
            {
                "media_id": season_metadata["media_id"],
                "season_number": season_metadata["season_number"],
                "media_type": "episode",
                "source": "tmdb",
                "episode_number": episode_number,
                "air_date": episode["air_date"],  # when unknown, response returns null
                "image": get_image_url(episode["still_path"]),
                "title": episode["name"],
                "overview": episode["overview"],
                "watched": watched,
                "end_date": (
                    tracked_episodes[episode_number]["end_date"] if watched else None
                ),
                "repeats": (
                    tracked_episodes[episode_number]["repeats"] if watched else None
                ),
            },
        )

    return episodes_metadata


def find_next_episode(episode_number, episodes_metadata):
    """Find the next episode number."""
    # Find the current episode in the sorted list
    current_episode_index = None
    for index, episode in enumerate(episodes_metadata):
        if episode["episode_number"] == episode_number:
            current_episode_index = index
            break

    # If the current episode is not the last episode, return the next episode number
    if current_episode_index + 1 < len(
        episodes_metadata,
    ):
        return episodes_metadata[current_episode_index + 1]["episode_number"]

    return None


def find_from_external(external_id, external_source):
    """Find the media from an external source."""
    cache_key = f"{external_id}_{external_source}"
    cached_result = cache.get(cache_key)

    if cached_result:
        return cached_result

    url = f"https://api.themoviedb.org/3/find/{external_id}"
    params = {
        "api_key": settings.TMDB_API,
        "language": settings.TMDB_LANG,
        "external_source": f"{external_source}_id",
    }

    data = services.api_request("TMDB", "GET", url, params=params)
    cache.set(cache_key, data)

    return data


def episode(media_id, season_number, episode_number):
    """Return the metadata for the selected episode from The Movie Database."""
    tv_metadata = tv_with_seasons(media_id, [season_number])
    season_metadata = tv_metadata[f"season/{season_number}"]

    for episode in season_metadata["episodes"]:
        if episode["episode_number"] == int(episode_number):
            return {
                "title": season_metadata["title"],
                "season_title": season_metadata["season_title"],
                "episode_title": episode["name"],
                "image": get_image_url(episode["still_path"]),
            }

    return None
