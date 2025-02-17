from decimal import Decimal

from django import template
from django.utils.html import format_html
from unidecode import unidecode

from app import helpers, models

register = template.Library()


@register.filter
def addslashes_double(arg1):
    """Add slashes before double quotes."""
    return arg1.replace('"', '\\"')


@register.filter()
def no_underscore(arg1):
    """Return the title case of the string."""
    return arg1.replace("_", " ")


@register.filter()
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


@register.filter()
def format_time(total_minutes):
    """Convert total minutes to HH:MM format."""
    return helpers.minutes_to_hhmm(total_minutes)


@register.filter()
def is_list(arg1):
    """Return True if the object is a list."""
    return isinstance(arg1, list)


@register.filter()
def media_type_readable(media_type):
    """Return the readable media type."""
    return models.Item.MediaTypes(media_type).label


@register.filter()
def media_type_readable_plural(media_type):
    """Return the readable media type in plural form."""
    singular = models.Item.MediaTypes(media_type).label

    # Special cases that don't change in plural form
    if singular.lower() in ["anime", "manga"]:
        return singular

    return f"{singular}s"


@register.filter
def media_color(media_type):
    """Return the color associated with the media type."""
    return models.Item.Colors[media_type.upper()].value


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


@register.simple_tag
def icon(name, is_active):
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
                      class="w-5 h-5 {active_class}">
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
        "profile": (
            """<path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"></path>
               <circle cx="12" cy="7" r="4"></circle>"""
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

    active_class = "text-indigo-400" if is_active else ""
    svg = base_svg.format(
        content=content,
        active_class=active_class,
    )

    return format_html(svg)


@register.filter
def str_equals(value, arg):
    """Return True if the string value is equal to the argument."""
    return str(value) == str(arg)
