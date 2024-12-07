import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import aiohttp
import requests
from bs4 import BeautifulSoup
from django.conf import settings
from django.core.cache import cache

from app.providers import services

base_url = "https://openlibrary.org/api"
search_url = "https://openlibrary.org/search.json"


def search(query):
    """Search for books on Open Library."""
    data = cache.get(f"search_books_{query}")

    if data is None:
        params = {
            "q": query,
            "fields": "cover_edition_key,edition_key,title,cover_i",
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
                "media_id": media_id,
                "source": "openlibrary",
                "media_type": "book",
                "title": doc["title"],
                "image": get_image_url(doc),
            }
            for doc in response.get("docs", [])
            if (media_id := get_media_id(doc)) and "title" in doc
        ]

        cache.set(f"search_books_{query}", data)
    return data


def get_media_id(doc):
    """Get media ID from document with fallback logic."""
    if "cover_edition_key" in doc:
        return doc["cover_edition_key"]

    # Fallback to first edition_key if available
    if doc.get("edition_key"):
        return doc["edition_key"][0]

    return None


def book(media_id):
    """Get metadata for a book from Open Library."""
    return asyncio.run(async_book(media_id))


async def async_book(media_id):
    """Asynchronous implementation of book metadata retrieval."""
    data = cache.get(f"book_{media_id}")

    if data is None:
        book_url = f"https://openlibrary.org/books/{media_id}.json"

        response_book = services.api_request(
            "OpenLibrary",
            "GET",
            book_url,
        )

        works = response_book.get("works", [])
        if works:
            work = works[0]
            work_url = (
                f"https://openlibrary.org/works/{work['key'].split('/')[-1]}.json"
            )

            response_work = services.api_request(
                "OpenLibrary",
                "GET",
                work_url,
            )
        else:
            response_work = {}

        # Run authors and recommendations concurrently
        authors_task = asyncio.create_task(
            get_authors(response_work),
        )

        data = {
            "media_id": media_id,
            "source": "openlibrary",
            "media_type": "book",
            "title": response_book["title"],
            "max_progress": response_book.get("number_of_pages"),
            "image": get_cover_image_url(response_book),
            "synopsis": get_description(response_book, response_work),
            "details": {
                "physical_format": get_physical_format(response_book),
                "number_of_pages": response_book.get("number_of_pages"),
                "publish_date": get_publish_date(response_book),
                "author": await authors_task,
                "genres": get_subjects(response_work),
                "publishers": get_publishers(response_book),
                "isbn": get_isbns(response_book),
            },
            "related": {
                "other_editions": get_editions(response_book, response_work),
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


def get_description(response_book, response_work):
    """Extract and clean up the book description."""
    if "description" in response_book:
        description = response_book["description"]
    elif "description" in response_work:
        description = response_work["description"]
    else:
        description = "No synopsis available."

    # sometimes the description is a dict
    # like {'type': '/type/text', 'value': '...'}
    if isinstance(description, dict):
        description = description["value"]

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


def get_publish_date(response):
    """Get the first publication date."""
    if "publish_date" in response:
        publish_date = response["publish_date"]
        if publish_date.startswith("cop. "):
            publish_date = publish_date[5:]
        try:
            parsed_date = datetime.strptime(publish_date, "%b %d, %Y").replace(
                tzinfo=ZoneInfo("UTC"),
            )
            return parsed_date.strftime("%Y-%m-%d")
        except ValueError:
            return publish_date
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


def get_editions(resposnse_book, response_work):
    """Get list of editions."""
    book_id = resposnse_book.get("key", "").split("/")[-1]
    work_id = response_work.get("key", "").split("/")[-1]

    url = f"https://openlibrary.org/works/{work_id}/editions.json"
    response = services.api_request(
        "OpenLibrary",
        "GET",
        url,
    )
    return [
        {
            "media_id": edition["key"].split("/")[-1],
            "title": edition.get("title"),
            "image": get_cover_image_url(edition),
        }
        for edition in response["entries"][:10]
        if edition["key"].split("/")[-1] != book_id
    ]
