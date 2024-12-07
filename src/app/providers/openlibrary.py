import asyncio

import aiohttp
import requests
from bs4 import BeautifulSoup
from django.conf import settings
from django.core.cache import cache

from app.providers import services

base_url = "https://openlibrary.org/api"
search_url = "https://openlibrary.org/search.json"


def search(query):
    """
    Search for books on Open Library.

    Args:
        query (str): The search query string

    Returns:
        list: List of dictionaries containing book information
    """
    data = cache.get(f"search_books_{query}")

    if data is None:
        params = {
            "q": query,
            "fields": "key,title,cover_i,author_name,first_publish_year",
            "limit": 25,
        }

        response = services.api_request(
            "OpenLibrary",
            "GET",
            search_url,
            params=params,
        )

        data = [
            {
                "media_id": doc["key"].split("/")[-1],
                "source": "openlibrary",
                "media_type": "book",
                "title": doc["title"],
                "image": get_image_url(doc),
            }
            for doc in response.get("docs", [])
            if "key" and "title" in doc
        ]

        cache.set(f"search_books_{query}", data)
    return data


def book(media_id):
    """Get metadata for a book from Open Library."""
    return asyncio.run(async_book(media_id))


async def async_book(media_id):
    """Asynchronous implementation of book metadata retrieval."""
    data = cache.get(f"book_{media_id}")

    if data is None:
        book_url = f"https://openlibrary.org/works/{media_id}.json"

        response = services.api_request(
            "OpenLibrary",
            "GET",
            book_url,
        )

        # Run authors and recommendations concurrently
        authors_task = asyncio.create_task(
            get_authors(response),
        )
        recommendations_task = asyncio.create_task(
            get_recommendations(response),
        )

        data = {
            "media_id": media_id,
            "source": "openlibrary",
            "media_type": "book",
            "title": response["title"],
            "max_progress": None,
            "image": get_cover_image_url(response),
            "synopsis": get_description(response),
            "details": {
                "author": await authors_task,
                "genres": get_subjects(response),
            },
            "related": {
                "recommendations": await recommendations_task,
            },
        }

        cache.set(f"book_{media_id}", data)

    return data


def get_image_url(doc):
    """Get the cover image URL for a book."""
    # when no picture, cover_i is not present in the response
    # e.g book: OL31949778W
    cover_id = doc.get("cover_i")
    if cover_id:
        return f"https://covers.openlibrary.org/b/id/{cover_id}-L.jpg"
    return settings.IMG_NONE


def get_cover_image_url(response):
    """Get the cover image URL from a work response."""
    covers = response.get("covers", [])
    if covers:
        return f"https://covers.openlibrary.org/b/id/{covers[0]}-L.jpg"
    return settings.IMG_NONE


def get_description(response):
    """Extract and clean up the book description.

    Convert HTML content to a single paragraph of plain text.
    """
    description = response.get("description", "No synopsis available.")
    if isinstance(description, dict):
        description = description.get("value", "No synopsis available.")

        if description != "No synopsis available.":
            soup = BeautifulSoup(description, "html.parser")
            text = soup.get_text(separator=" ")
            description = " ".join(text.split())

    return description


def get_physical_format(response):
    """Get the physical format of the book."""
    format_value = response.get("physical_format")
    if format_value:
        return format_value.title()
    return None


async def get_authors(response):
    """Get list of author names asynchronously."""
    authors = []
    author_entries = response.get("authors", [])

    async with aiohttp.ClientSession() as session:
        tasks = []
        for author in author_entries:
            if isinstance(author, dict) and "author" in author:
                author_key = author["author"]["key"]
                author_url = f"https://openlibrary.org{author_key}.json"
                tasks.append(fetch_author_data(session, author_url))

        author_data_list = await asyncio.gather(*tasks)
        authors = [
            data.get("name", "Unknown Author") for data in author_data_list if data
        ]

    return ", ".join(authors) if authors else None


async def fetch_author_data(session, url):
    """Fetch author data asynchronously."""
    async with session.get(url) as response:
        if response.status == requests.codes.ok:
            return await response.json()

    return None


def get_subjects(response):
    """Get list of subjects/genres."""
    if "subjects" in response:
        return ", ".join(subject for subject in response["subjects"][:5])
    return None


def get_publishers(response):
    """Get list of publishers."""
    if "publishers" in response:
        return ", ".join(publisher for publisher in response.get("publishers", [])[:5])
    return None


def get_isbns(response):
    """Get list of ISBNs."""
    isbn_13 = response.get("isbn_13", [])
    isbn_10 = response.get("isbn_10", [])
    isbns = isbn_13 + isbn_10

    if isbns:
        return ", ".join(isbn for isbn in isbns)
    return None


async def get_recommendations(response):
    """Get recommended books based on subjects asynchronously."""
    media_id = response.get("key", "").split("/")[-1]

    url = "https://openlibrary.org/partials.json"
    params = {
        "workid": media_id,
        "_component": "RelatedWorkCarousel",
    }

    async with (
        aiohttp.ClientSession() as session,
        session.get(url, params=params) as recommendations_response,
    ):
        if recommendations_response.status == requests.codes.ok:
            html_text = await recommendations_response.json(content_type="text/html")
            html_content = html_text.get("0", "")

    soup = BeautifulSoup(html_content, "html.parser")

    carousel_items = soup.select(".book.carousel__item")

    data = []
    for item in carousel_items:
        # Get book identifier from href
        link = item.select_one("a[href]")
        if not link:
            continue

        href = link.get("href", "")
        book_id = href.split("/")[-1]

        # Get book image and title
        img = item.select_one("img.bookcover")
        if not img:
            continue

        title = img.get("alt", "").split(" by ")[0].strip()
        image_url = img.get("src")

        # Handle lazy-loaded images
        if not image_url or image_url.startswith("data:"):
            image_url = img.get("data-lazy")

        if title and book_id and image_url:
            data.append(
                {
                    "media_id": book_id,
                    "source": "openlibrary",
                    "media_type": "book",
                    "title": title,
                    "image": image_url,
                },
            )

    return data
