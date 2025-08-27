from urllib.parse import parse_qsl, urlencode, urlparse

from django.contrib import messages
from django.http import HttpResponseRedirect
from django.shortcuts import redirect
from django.utils.encoding import iri_to_uri
from django.utils.http import url_has_allowed_host_and_scheme

from app.models import BasicMedia, Item, MediaTypes

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


def form_error_messages(form, request):
    """Display form errors as messages."""
    for field, errors in form.errors.items():
        for error in errors:
            messages.error(
                request,
                f"{field.replace('_', ' ').title()}: {error}",
            )


def format_search_response(page, per_page, total_results, results):
    """Format the search response for pagination."""
    return {
        "page": page,
        "total_results": total_results,
        "total_pages": total_results // per_page + 1,
        "results": results,
    }


def enrich_items_with_user_data(request, items):
    """Enrich a list of items with user tracking data."""
    enriched_items = []
    for item in items:
        # Try to find existing Item and user's media tracking data
        try:
            db_item = Item.objects.get(
                media_id=item["media_id"],
                source=item["source"],
                media_type=item["media_type"],
                season_number=item.get("season_number"),
                episode_number=item.get("episode_number"),
            )
            
            # Get user's tracking data for this item
            if item["media_type"] == MediaTypes.SEASON.value:
                media = BasicMedia.objects.filter_media_prefetch(
                    request.user,
                    item["media_id"],
                    item["media_type"],
                    item["source"],
                    season_number=item.get("season_number"),
                )
            else:
                media = BasicMedia.objects.filter_media_prefetch(
                    request.user,
                    item["media_id"],
                    item["media_type"],
                    item["source"],
                )
            
            # Create enriched result with both item and tracking data
            enriched_item = {
                "item": db_item,
                "media": media[0] if media else None,
                "title": item.get("season_title", item["title"]),
                # Preserve other properties that might be needed
                **{k: v for k, v in item.items() if k not in ["media_id", "source", "media_type", "title"]}
            }
            enriched_items.append(enriched_item)
            
        except Item.DoesNotExist:
            # Item doesn't exist in our database yet, use raw data
            enriched_item = {
                "item": item,  # Raw metadata
                "media": None,   # No tracking data
                "title": item.get("season_title", item["title"]),
                # Preserve other properties that might be needed
                **{k: v for k, v in item.items() if k not in ["title"]}
            }
            enriched_items.append(enriched_item)
    
    return enriched_items
