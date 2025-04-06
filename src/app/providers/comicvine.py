from bs4 import BeautifulSoup
from django.conf import settings
from django.core.cache import cache

from app.providers import services

base_url = "https://comicvine.gamespot.com/api"
headers = {
    "User-Agent": "Mozilla/5.0",
}


def search(query):
    """Search for comics on Comic Vine."""
    cache_key = f"search_comicvine_{query}"
    data = cache.get(cache_key)

    if data is None:
        params = {
            "api_key": settings.COMICVINE_API,
            "format": "json",
            "query": query,
            "resources": "volume",
            "field_list": "id,name,image",
            "limit": 20,
        }

        response = services.api_request(
            "ComicVine",
            "GET",
            f"{base_url}/search/",
            params=params,
            headers=headers,
        )

        data = [
            {
                "media_id": str(item["id"]),
                "source": "comicvine",
                "media_type": "comic",
                "title": item["name"],
                "image": get_image(item),
            }
            for item in response["results"]
        ]

        cache.set(cache_key, data)

    return data


def comic(media_id):
    """Return the metadata for the selected comic volume from Comic Vine."""
    cache_key = f"comicvine_volume_{media_id}"
    data = cache.get(cache_key)

    if data is None:
        params = {
            "api_key": settings.COMICVINE_API,
            "format": "json",
            "field_list": (
                "publisher,site_detail_url,name,last_issue,image,issues,"
                "description,concepts,start_year,count_of_issues,people,"
            ),
        }

        response = services.api_request(
            "ComicVine",
            "GET",
            f"{base_url}/volume/4050-{media_id}/",
            params=params,
            headers=headers,
        )

        response = response.get("results", {})
        publisher_id = response["publisher"]["id"]
        recommendations = []
        if publisher_id:
            recommendations = get_similar_comics(publisher_id, media_id)

        data = {
            "media_id": media_id,
            "source": "comicvine",
            "source_url": response["site_detail_url"],
            "media_type": "comic",
            "title": response["name"],
            "max_progress": get_last_issue_number(response),
            "image": get_image(response),
            "synopsis": get_synopsis(response),
            "genres": get_genres(response),
            "details": {
                "start_date": get_start_year(response),
                "publisher": get_publisher_name(response),
                "issues_count": get_issues_count(response),
                "last_issue_name": get_last_issue_name(response),
                "last_issue_number": get_last_issue_number(response),
                "people": get_people(response),
            },
            "related": {
                "from_the_same_publisher": recommendations,
            },
        }

        cache.set(cache_key, data)

    return data


def get_readable_status(status):
    """Convert API status to readable format."""
    status_map = {
        None: "Unknown",
        "": "Unknown",
        "Completed": "Completed",
        "Ongoing": "Ongoing",
    }
    return status_map.get(status, "Unknown")


def get_image(response):
    """Return the image URL."""
    if "image" in response:
        return response["image"]["medium_url"]
    return settings.IMG_NONE


def get_synopsis(response):
    """Return the synopsis."""
    if "description" not in response:
        return "No synopsis available"

    soup = BeautifulSoup(response["description"], "html.parser")
    text = soup.get_text(separator=" ")
    return " ".join(text.split())


def get_genres(response):
    """Return the list of genres."""
    if "concepts" in response:
        return [concept["name"] for concept in response["concepts"]]
    return None


def get_start_year(response):
    """Return the start year of the comic volume."""
    return response.get("start_year")


def get_publisher_name(response):
    """Return the publisher name of the comic volume."""
    publisher = response.get("publisher")
    if publisher and isinstance(publisher, dict):
        return publisher.get("name")
    return None


def get_issues_count(response):
    """Return the count of issues in the comic volume."""
    return response.get("count_of_issues")


def get_last_issue_name(response):
    """Return the name of the last issue in the comic volume."""
    last_issue = response.get("last_issue")
    if last_issue and isinstance(last_issue, dict):
        return last_issue.get("name")
    return None


def get_last_issue_number(response):
    """Return the last issue number."""
    last_issue = response.get("last_issue")
    if last_issue and isinstance(last_issue, dict):
        return int(last_issue.get("issue_number"))
    return None


def get_people(response):
    """Return the people associated with the comic volume."""
    people = response.get("people", [])
    return [person["name"] for person in people[:5] if isinstance(person, dict)]


def get_similar_comics(publisher_id, current_id, limit=10):
    """Get similar comics from the same publisher."""
    cache_key = f"comicvine_similar_{publisher_id}_{current_id}"
    data = cache.get(cache_key)

    if data is None:
        params = {
            "api_key": settings.COMICVINE_API,
            "format": "json",
            "field_list": "id,name,image,start_year,publisher",
            "filter": f"publisher:{publisher_id}",
            "limit": limit + 1,  # Get one extra to account for current comic
        }

        response = services.api_request(
            "ComicVine",
            "GET",
            f"{base_url}/volumes/",
            params=params,
            headers=headers,
        )

        # Filter out the current comic and format the response
        data = [
            {
                "media_id": str(item["id"]),
                "source": "comicvine",
                "media_type": "comic",
                "title": item["name"],
                "image": get_image(item),
            }
            for item in response["results"]
            if str(item["id"]) != current_id
        ][:limit]

        cache.set(cache_key, data)

    return data
