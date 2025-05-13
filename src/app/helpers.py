from urllib.parse import parse_qsl, urlencode, urlparse

from django.contrib import messages
from django.http import HttpResponseRedirect
from django.shortcuts import redirect
from django.template.defaultfilters import pluralize
from django.utils.encoding import iri_to_uri
from django.utils.http import url_has_allowed_host_and_scheme

from app import media_type_config
from app.models import Media, MediaTypes


def minutes_to_hhmm(total_minutes):
    """Convert total minutes to HH:MM format."""
    hours = int(total_minutes / 60)
    minutes = int(total_minutes % 60)
    if hours == 0:
        return f"{minutes}min"
    return f"{hours}h {minutes:02d}min"


def redirect_back(request):
    """Redirect to the previous page, removing the 'page' parameter if present."""
    if url_has_allowed_host_and_scheme(request.GET.get("next"), None):
        next_url = request.GET["next"]

        # Parse the URL
        parsed_url = urlparse(next_url)

        # Get the query parameters and remove params we don't want
        query_params = dict(parse_qsl(parsed_url.query))
        query_params.pop("page", None)
        query_params.pop("load_media_type", None)

        # Reconstruct the URL
        new_query = urlencode(query_params)
        new_parts = list(parsed_url)
        new_parts[4] = new_query  # index 4 is the query part

        # Convert back to a URL string
        clean_url = iri_to_uri(parsed_url._replace(query=new_query).geturl())

        return HttpResponseRedirect(clean_url)

    return redirect("home")


def format_description(field_name, old_value, new_value, media_type=None):  # noqa: C901, PLR0911, PLR0912
    """Format change description in a human-readable way.

    Provides natural language descriptions for various types of changes,
    taking into account the media type and status transitions.
    """
    # If old_value is None, treat it as an initial setting
    if old_value is None:
        if field_name == "status":
            verb = media_type_config.get_verb(media_type, past_tense=False)
            if new_value == Media.Status.IN_PROGRESS.value:
                return f"Started {verb}ing"
            if new_value == Media.Status.COMPLETED.value:
                return f"Finished {verb}ing"
            if new_value == Media.Status.PLANNING.value:
                return f"Added to {verb}ing list"
            if new_value == Media.Status.DROPPED.value:
                return f"Stopped {verb}ing"
            if new_value == Media.Status.PAUSED.value:
                return f"Paused {verb}ing"
            return f"Status set to {new_value}"

        if field_name == "score":
            return f"Rated {new_value}/10"

        if field_name == "progress" and media_type != MediaTypes.MOVIE.value:
            verb = media_type_config.get_verb(media_type, past_tense=True).title()
            if media_type == MediaTypes.GAME.value:
                return f"{verb} for {minutes_to_hhmm(new_value)}"
            unit = media_type_config.get_unit(media_type, short=False).lower()
            return f"{verb} {new_value} {unit}{pluralize(new_value)}"

        if field_name == "repeats":
            verb = media_type_config.get_verb(media_type, past_tense=True)
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
        verb = media_type_config.get_verb(media_type, past_tense=False)
        # Status transitions
        transitions = {
            (
                Media.Status.PLANNING.value,
                Media.Status.IN_PROGRESS.value,
            ): f"Started {verb}ing",
            (
                Media.Status.IN_PROGRESS.value,
                Media.Status.COMPLETED.value,
            ): f"Finished {verb}ing",
            (
                Media.Status.IN_PROGRESS.value,
                Media.Status.PAUSED.value,
            ): f"Paused {verb}ing",
            (
                Media.Status.PAUSED.value,
                Media.Status.IN_PROGRESS.value,
            ): f"Resumed {verb}ing",
            (
                Media.Status.IN_PROGRESS.value,
                Media.Status.DROPPED.value,
            ): f"Stopped {verb}ing",
            (
                Media.Status.COMPLETED.value,
                Media.Status.REPEATING.value,
            ): f"Started re{verb}ing",
            (
                Media.Status.REPEATING.value,
                Media.Status.COMPLETED.value,
            ): f"Finished re{verb}ing",
        }
        return transitions.get(
            (old_value, new_value),
            f"Changed status from {old_value} to {new_value}",
        )

    if field_name == "score":
        if old_value == 0:
            return f"Rated {new_value}/10"
        return f"Changed rating from {old_value} to {new_value}"

    if field_name == "progress" and media_type != MediaTypes.MOVIE.value:
        diff = new_value - old_value
        diff_abs = abs(diff)

        if media_type == MediaTypes.GAME.value:
            if diff > 0:
                return f"Added {minutes_to_hhmm(diff_abs)} of playtime"
            return f"Removed {minutes_to_hhmm(diff_abs)} of playtime"

        unit = (
            f"{media_type_config.get_unit(media_type, short=False).lower()}"
            f"{pluralize(diff_abs)}"
        )

        verb = media_type_config.get_verb(media_type, past_tense=True).title()
        if diff < 0:
            verb = "Removed"  # Override with "Removed" for negative changes

        return f"{verb} {diff_abs} {unit}"

    if field_name == "repeats":
        verb = media_type_config.get_verb(media_type, past_tense=True)
        if new_value > old_value:
            return f"{verb.title()} again (#{new_value + 1})"
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


def form_error_messages(form, request):
    """Display form errors as messages."""
    for field, errors in form.errors.items():
        for error in errors:
            messages.error(
                request,
                f"{field.replace('_', ' ').title()}: {error}",
            )
