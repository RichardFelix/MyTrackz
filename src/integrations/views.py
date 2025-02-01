"""Contains views for importing and exporting media data from various sources."""

import json
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_not_required
from django.core.exceptions import ObjectDoesNotExist
from django.http import HttpResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

import users
from integrations import exports, tasks
from integrations.imports import simkl
from integrations.webhooks import jellyfin

logger = logging.getLogger(__name__)


@require_GET
def import_trakt(request):
    """View for importing anime and manga data from Trakt."""
    username = request.GET.get("trakt")

    if not username:
        messages.error(request, "Trakt username is required.")
        return redirect("profile")

    tasks.import_trakt.delay(username, request.user)
    messages.success(request, "Trakt import task queued.")
    return redirect("profile")


@require_GET
def simkl_oauth(request):
    """View for initiating the SIMKL OAuth2 authorization flow."""
    domain = request.get_host()
    scheme = request.scheme
    url = "https://simkl.com/oauth/authorize"

    return redirect(
        f"{url}?client_id={settings.SIMKL_ID}&redirect_uri={scheme}://{domain}/import/simkl&response_type=code",
    )


@require_GET
def import_simkl(request):
    """View for getting the SIMKL OAuth2 token."""
    token = simkl.get_token(request)
    tasks.import_simkl.delay(token, request.user)
    messages.success(request, "SIMKL import task queued.")
    return redirect("profile")


@require_GET
def import_mal(request):
    """View for importing anime and manga data from MyAnimeList."""
    username = request.GET.get("mal")
    if not username:
        messages.error(request, "MyAnimeList username is required.")
        return redirect("profile")

    mode = request.GET["mode"]
    tasks.import_mal.delay(username, request.user, mode)
    messages.success(request, "MyAnimeList import task queued.")
    return redirect("profile")


@require_POST
def import_tmdb(request):
    """View for importing TMDB movie and TV watchlist."""
    file = request.FILES.get("tmdb")

    if not file:
        messages.error(request, "TMDB CSV file is required.")
        return redirect("profile")

    if request.POST.get("type") == "ratings":
        tasks.import_tmdb.delay(file, request.user, "Completed")
        messages.success(request, "TMDB ratings import task queued.")
    else:
        tasks.import_tmdb.delay(file, request.user, "Planning")
        messages.success(request, "TMDB watchlist import task queued.")

    return redirect("profile")


@require_GET
def import_anilist(request):
    """View for importing anime and manga data from AniList."""
    username = request.GET.get("anilist")
    if not username:
        messages.error(request, "AniList username is required.")
        return redirect("profile")

    mode = request.GET["mode"]
    tasks.import_anilist.delay(username, request.user, mode)
    messages.success(request, "AniList import task queued.")
    return redirect("profile")


@require_GET
def import_kitsu(request):
    """View for importing anime and manga data from Kitsu by user ID."""
    user_id = request.GET.get("kitsu")

    if not user_id:
        messages.error(request, "Kitsu user ID is required.")
        return redirect("profile")

    mode = request.GET["mode"]
    tasks.import_kitsu_id.delay(user_id, request.user, mode)
    messages.success(request, "Kitsu import task queued.")
    return redirect("profile")


@require_POST
def import_yamtrack(request):
    """View for importing anime and manga data from Yamtrack CSV."""
    file = request.FILES.get("yamtrack_csv")

    if not file:
        messages.error(request, "Yamtrack CSV file is required.")
        return redirect("profile")

    mode = request.POST["mode"]
    tasks.import_yamtrack.delay(request.FILES["yamtrack_csv"], request.user, mode)
    messages.success(request, "Yamtrack import task queued.")
    return redirect("profile")


@require_GET
def export_csv(request):
    """View for exporting all media data to a CSV file."""
    today = timezone.now().strftime("%Y-%m-%d")
    # Create the HttpResponse object with the appropriate CSV header.
    response = HttpResponse(
        content_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="yamtrack_{today}.csv"'},
    )

    response = exports.db_to_csv(response, request.user)

    logger.info("User %s successfully exported their data", request.user.username)

    return response


@login_not_required
@csrf_exempt
@require_POST
def jellyfin_webhook(request, token):
    """Handle Jellyfin webhook notifications for media playback."""
    try:
        user = users.models.User.objects.get(token=token)
    except ObjectDoesNotExist:
        logger.warning(
            "Could not process Jellyfin webhook: Invalid token: %s",
            token,
        )
        return HttpResponse(status=401)

    payload = json.loads(request.body)
    jellyfin.process_payload(payload, user)
    return HttpResponse(status=200)
