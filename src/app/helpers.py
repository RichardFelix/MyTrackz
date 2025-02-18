from django.shortcuts import redirect
from django.utils.encoding import iri_to_uri
from django.utils.http import url_has_allowed_host_and_scheme


def minutes_to_hhmm(total_minutes):
    """Convert total minutes to HH:MM format."""
    hours = int(total_minutes / 60)
    minutes = int(total_minutes % 60)
    if hours == 0:
        return f"{minutes}min"
    return f"{hours}h {minutes:02d}min"


def redirect_back(request):
    """Redirect to the previous page."""
    if url_has_allowed_host_and_scheme(request.GET.get("next"), None):
        url = iri_to_uri(request.GET["next"])
        return redirect(url)
    return redirect("home")


def get_media_verb(media_type, past_tense):
    """Get the appropriate verb for the media type."""
    verbs = {
        "tv": ("watch", "watched"),
        "movie": ("watch", "watched"),
        "anime": ("watch", "watched"),
        "manga": ("read", "read"),
        "book": ("read", "read"),
        "game": ("play", "played"),
    }
    return verbs.get(media_type, ("consum", "consumed"))[1 if past_tense else 0]


def format_description(field_name, old_value, new_value, media_type=None):  # noqa: C901, PLR0911, PLR0912
    """Format change description in a human-readable way.

    Provides natural language descriptions for various types of changes,
    taking into account the media type and status transitions.
    """
    # If old_value is None, treat it as an initial setting
    if old_value is None:
        if field_name == "status":
            verb = get_media_verb(media_type, past_tense=False)
            if new_value == "In progress":
                return f"Started {verb}ing"
            if new_value == "Completed":
                return f"Finished {verb}ing"
            if new_value == "Planning":
                return f"Added to {verb}ing list"
            if new_value == "Dropped":
                return f"Stopped {verb}ing"
            if new_value == "Paused":
                return f"Paused {verb}ing"
            return f"Status set to {new_value}"

        if field_name == "score":
            return f"Rated {new_value}/10"

        if field_name == "progress":
            if media_type == "Game":
                return f"Played for {minutes_to_hhmm(new_value)}"
            if media_type == "Book":
                return f"Read {new_value} pages"
            if media_type == "Manga":
                return f"Read {new_value} chapters"
            return f"Watched {new_value} episodes"

        if field_name == "repeats":
            verb = get_media_verb(media_type, past_tense=True)
            return f"{verb.title()} for the first time"

        if field_name in ["start_date", "end_date"]:
            field_display = "Started" if field_name == "start_date" else "Finished"
            return (
                f"{field_display} on {new_value.strftime('%B %-d, %Y')}"
                if new_value
                else f"Removed {field_display.lower()} date"
            )

        if field_name == "notes":
            return "Added notes"

        return f"Set {field_name.replace('_', ' ').lower()} to {new_value}"

    # Regular change (old_value to new_value)
    if field_name == "status":
        verb = get_media_verb(media_type, past_tense=False)
        # Status transitions
        transitions = {
            ("Planning", "In progress"): f"Started {verb}ing",
            ("In progress", "Completed"): f"Finished {verb}ing",
            ("In progress", "Paused"): f"Paused {verb}ing",
            ("Paused", "In progress"): f"Resumed {verb}ing",
            ("In progress", "Dropped"): f"Dropped {verb}ing",
            ("Completed", "Repeating"): f"Started re{verb}ing",
            ("Repeating", "Completed"): f"Finished re{verb}ing",
        }
        return transitions.get(
            (old_value, new_value),
            f"Changed status from {old_value} to {new_value}",
        )

    if field_name == "score":
        if old_value == 0:
            return f"Rated {new_value}/10"
        return f"Changed rating from {old_value} to {new_value}"

    if field_name == "progress":
        if media_type == "Game":
            diff = new_value - old_value
            if diff > 0:
                return f"Added {minutes_to_hhmm(diff)} of playtime"
            return f"Removed {minutes_to_hhmm(abs(diff))} of playtime"
        diff = new_value - old_value
        if media_type == "Book":
            unit = "pages"
        elif media_type == "Manga":
            unit = "chapters"
        else:
            unit = "episodes"
        verb = "Read" if media_type in ["Book", "Manga"] else "Watched"
        return f"{verb} {abs(diff)} {unit}"

    if field_name == "repeats":
        verb = get_media_verb(media_type, past_tense=True)
        if new_value > old_value:
            return f"{verb.title()} again (#{new_value})"
        return f"Adjusted repeat count from {old_value} to {new_value}"

    if field_name in ["start_date", "end_date"]:
        field_display = "Start" if field_name == "start_date" else "End"
        if not new_value:
            return f"Removed {field_display.lower()} date"
        if not old_value:
            return f"{field_display}ed on {new_value.strftime('%B %-d, %Y')}"
        date_str = new_value.strftime("%B %-d, %Y")
        return f"Changed {field_display.lower()} date to {date_str}"

    if field_name == "notes":
        if not old_value:
            return "Added notes"
        if not new_value:
            return "Removed notes"
        return "Updated notes"

    field_label = field_name.replace("_", " ").lower()
    return f"Updated {field_label} from {old_value} to {new_value}"
