from decimal import Decimal
from urllib.parse import parse_qsl, urlparse

from django import template
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.http import urlencode
from unidecode import unidecode

from app import helpers, models

register = template.Library()


@register.filter
def addslashes_double(arg1):
    """Add slashes before double quotes."""
    return arg1.replace('"', '\\"')


@register.filter
def no_underscore(arg1):
    """Return the title case of the string."""
    return arg1.replace("_", " ")


@register.filter
def slug(arg1):
    """Return the slug of the string.

    Sometimes slugify removes all characters from a string, so we need to
    urlencode the special characters first.
    e.g Anime: 31687
    """
    cleaned = template.defaultfilters.slugify(unidecode(arg1))
    if cleaned == "":
        return template.defaultfilters.slugify(
            template.defaultfilters.urlencode(unidecode(arg1)),
        )
    return cleaned


@register.simple_tag
def set_param(url, param, value):
    """
    Set or replace a query parameter in the given URL.

    Usage: {% set_param request.get_full_path 'sort' 'title' %}
    """
    # Parse the URL
    parsed_url = urlparse(url)

    # Parse the query string into a dictionary
    query_dict = dict(parse_qsl(parsed_url.query))

    # Update or add the parameter
    if value is not None:
        query_dict[param] = value
    elif param in query_dict:
        del query_dict[param]

    # Reconstruct the URL with the updated query string
    new_query = urlencode(query_dict)

    # Return the path with the updated query string
    path = parsed_url.path or "/"
    return f"{path}?{new_query}" if new_query else path


@register.filter
def format_time(total_minutes):
    """Convert total minutes to HH:MM format."""
    return helpers.minutes_to_hhmm(total_minutes)


@register.filter
def is_list(arg1):
    """Return True if the object is a list."""
    return isinstance(arg1, list)


@register.filter
def media_type_readable(media_type):
    """Return the readable media type."""
    return models.MediaTypes(media_type).label


@register.filter
def media_type_readable_plural(media_type):
    """Return the readable media type in plural form."""
    singular = models.MediaTypes(media_type).label

    # Special cases that don't change in plural form
    if singular.lower() in ["anime", "manga"]:
        return singular

    if singular.lower() == "season":
        return "TV Seasons"

    return f"{singular}s"


@register.filter
def default_source(media_type):
    """Return the default source for the media type."""
    media_type_source = {
        "tv": "The Movie Database",
        "season": "The Movie Database",
        "episode": "The Movie Database",
        "movie": "The Movie Database",
        "anime": "MyAnimeList",
        "manga": "MyAnimeList",
        "game": "The Internet Game Database",
        "book": "Open Library",
    }

    return media_type_source[media_type]


@register.filter
def media_past_verb(media_type):
    """Return the past tense verb for the given media type."""
    return helpers.get_media_verb(media_type, past_tense=True)


@register.filter
def sample_search(media_type):
    """Return a sample search URL for the given media type using GET parameters."""
    base_url = reverse("search")

    sample_queries = {
        "tv": "Breaking Bad",
        "movie": "The Shawshank Redemption",
        "anime": "Perfect Blue",
        "manga": "Berserk",
        "game": "Half-Life",
        "book": "The Great Gatsby",
    }

    if media_type in sample_queries:
        query_params = {
            "media_type": media_type,
            "q": sample_queries[media_type],
        }
        return f"{base_url}?{urlencode(query_params)}"

    # Return base search URL if media type not recognized
    return base_url


@register.filter
def media_color(media_type):
    """Return the color associated with the media type."""
    return models.Colors[media_type.upper()].value


@register.filter
def percentage_ratio(value, total):
    """Calculate percentage, showing one decimal place for values between 0 and 1."""
    try:
        if total == 0:
            return "0"

        result = (Decimal(value) / Decimal(total)) * 100

        # If result is between 0 and 1, show one decimal
        if 0 < result < 1:
            return f"{result:.1f}"

        # For all other values, show as integer
        return str(int(round(result)))
    except (TypeError, ValueError):
        return "0"


@register.filter
def naturalday(value):
    """Return the natural day for the date."""
    today = timezone.now().date()
    diff = value - today
    days = diff.days
    days_threshold = 5

    if days == 0:
        return "Today"
    if days == 1:
        return "Tomorrow"
    if days > 1 and days <= days_threshold:
        return f"In {days} days"
    return value.strftime("%b %d, %Y")


@register.filter
def media_url(media):
    """Return the media URL for both metadata and model object cases."""
    is_dict = isinstance(media, dict)

    # Get attributes using either dict access or object attribute
    media_type = media["media_type"] if is_dict else media.media_type
    source = media["source"] if is_dict else media.source
    media_id = media["media_id"] if is_dict else media.media_id
    title = media["title"] if is_dict else media.title

    if media_type in ["season", "episode"]:
        season_number = media["season_number"] if is_dict else media.season_number
        return reverse(
            "season_details",
            kwargs={
                "source": source,
                "media_id": media_id,
                "title": slug(title),
                "season_number": season_number,
            },
        )

    return reverse(
        "media_details",
        kwargs={
            "source": source,
            "media_type": media_type,
            "media_id": media_id,
            "title": slug(title),
        },
    )


@register.simple_tag
def component_id(component_type, media):
    """Return the component ID for both metadata and model object cases."""
    is_dict = isinstance(media, dict)

    # Get base attributes using either dict access or object attribute
    media_type = media["media_type"] if is_dict else media.media_type
    media_id = media["media_id"] if is_dict else media.media_id

    component_id = f"{component_type}-{media_type}-{media_id}"

    # Handle season/episode numbers if they exist
    if is_dict:
        if "season_number" in media:
            component_id += f"-{media['season_number']}"
        if "episode_number" in media:
            component_id += f"-{media['episode_number']}"
    else:
        if media.season_number is not None:
            component_id += f"-{media.season_number}"
        if media.episode_number is not None:
            component_id += f"-{media.episode_number}"

    return component_id


@register.simple_tag
def modal_url(modal_type, media):
    """Return the modal URL for both metadata and model object cases."""
    is_dict = isinstance(media, dict)

    # Build kwargs using either dict access or object attribute
    kwargs = {
        "source": media["source"] if is_dict else media.source,
        "media_type": media["media_type"] if is_dict else media.media_type,
        "media_id": media["media_id"] if is_dict else media.media_id,
    }

    # Handle season/episode numbers if they exist
    if is_dict:
        if "season_number" in media:
            kwargs["season_number"] = media["season_number"]
        if "episode_number" in media:
            kwargs["episode_number"] = media["episode_number"]
    else:
        if media.season_number is not None:
            kwargs["season_number"] = media.season_number
        if media.episode_number is not None:
            kwargs["episode_number"] = media.episode_number

    return reverse(f"{modal_type}_modal", kwargs=kwargs)


@register.simple_tag
def icon(name, is_active, extra_classes=None):
    """Return the SVG icon for the media type."""
    base_svg = """<svg xmlns="http://www.w3.org/2000/svg"
                      width="24"
                      height="24"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      stroke-width="2"
                      stroke-linecap="round"
                      stroke-linejoin="round"
                      class="{active_class}{extra_classes}">
                      {content}
                 </svg>"""

    icons = {
        "home": (
            """<path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"></path>
               <polyline points="9 22 9 12 15 12 15 22"></polyline>"""
        ),
        "tv": (
            """<rect width="20" height="15" x="2" y="7" rx="2" ry="2"></rect>
               <polyline points="17 2 12 7 7 2"></polyline>"""
        ),
        "season": (
            """<path d="m12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91
                a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83Z"></path>
               <path d="m22 17.65-9.17 4.16a2 2 0 0 1-1.66 0L2 17.65"></path>
               <path d="m22 12.65-9.17 4.16a2 2 0 0 1-1.66 0L2 12.65"></path>"""
        ),
        "episode": ("""<polygon points="6 3 20 12 6 21 6 3"></polygon>"""),
        "movie": (
            """<rect width="18" height="18" x="3" y="3" rx="2"></rect>
               <path d="M7 3v18"></path>
               <path d="M3 7.5h4"></path>
               <path d="M3 12h18"></path>
               <path d="M3 16.5h4"></path>
               <path d="M17 3v18"></path>
               <path d="M17 7.5h4"></path>
               <path d="M17 16.5h4"></path>"""
        ),
        "anime": (
            """<circle cx="12" cy="12" r="10"></circle>
               <polygon points="10 8 16 12 10 16 10 8"></polygon>"""
        ),
        "manga": (
            """<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z">
               </path>
               <path d="M14 2v4a2 2 0 0 0 2 2h4"></path>
               <path d="M10 9H8"></path>
               <path d="M16 13H8"></path>
               <path d="M16 17H8"></path>"""
        ),
        "game": (
            """<line x1="6" x2="10" y1="12" y2="12"></line>
               <line x1="8" x2="8" y1="10" y2="14"></line>
               <line x1="15" x2="15.01" y1="13" y2="13"></line>
               <line x1="18" x2="18.01" y1="11" y2="11"></line>
               <rect width="20" height="12" x="2" y="6" rx="2"></rect>"""
        ),
        "book": (
            """<path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1 0-5H20">
               </path>"""
        ),
        "create": (
            """<circle cx="12" cy="12" r="10"></circle>
               <path d="M8 12h8"></path>
               <path d="M12 8v8"></path>"""
        ),
        "statistics": (
            """<line x1="18" x2="18" y1="20" y2="10"></line>
               <line x1="12" x2="12" y1="20" y2="4"></line>
               <line x1="6" x2="6" y1="20" y2="14"></line>"""
        ),
        "lists": (
            """<path d="M12 10v6"></path>
               <path d="M9 13h6"></path>
               <path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9
               L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"></path>"""
        ),
        "calendar": (
            """<path d="M8 2v4"></path>
               <path d="M16 2v4"></path>
               <rect width="18" height="18" x="3" y="4" rx="2"></rect>
               <path d="M3 10h18"></path>"""
        ),
        "settings": (
            """<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2
               2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73
               2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0
               0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2
               2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1
               1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2
               0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2
               2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0
               1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"></path>
               <circle cx="12" cy="12" r="3"></circle>"""
        ),
        "logout": (
            """<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"></path>
               <polyline points="16 17 21 12 16 7"></polyline>
               <line x1="21" x2="9" y1="12" y2="12"></line>"""
        ),
    }

    content = icons.get(name)
    if not content:
        return ""

    active_class = "text-indigo-400 " if is_active else ""
    extra_classes = extra_classes or "w-5 h-5"

    svg = base_svg.format(
        content=content,
        active_class=active_class,
        extra_classes=extra_classes,
    )

    return format_html(svg)


@register.filter
def str_equals(value, arg):
    """Return True if the string value is equal to the argument."""
    return str(value) == str(arg)
