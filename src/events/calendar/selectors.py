import logging

from django.db.models import Exists, OuterRef, Q, Subquery
from django.utils import timezone

from app.models import Item, MediaTypes, Sources
from app.providers import services, tmdb
from events.models import Event

logger = logging.getLogger(__name__)


def get_items_to_process(user=None):
    """Get items to process for the calendar."""
    media_types = [
        choice.value
        for choice in MediaTypes
        if choice not in [MediaTypes.SEASON, MediaTypes.EPISODE]
    ]

    query = Q()

    for media_type in media_types:
        media_query = Q(**{f"{media_type}__isnull": False})

        if user:
            media_query &= Q(**{f"{media_type}__user": user})

        query |= media_query

    query &= ~Q(source=Sources.MANUAL.value)

    items = Item.objects.filter(query).distinct()

    return filter_items_to_fetch(items)


def filter_items_to_fetch(items):
    """Filter items that need calendar events according to specific rules."""
    now = timezone.now()
    one_year_ago = now - timezone.timedelta(days=365)

    tv_items = items.filter(
        media_type=MediaTypes.TV.value,
        source=Sources.TMDB.value,
    )
    tv_items_to_include = get_tv_items_to_include(tv_items)
    movie_items = items.filter(
        media_type=MediaTypes.MOVIE.value,
        source=Sources.TMDB.value,
    )
    movie_items_to_include = get_movie_items_to_include(movie_items)

    future_events = Event.objects.filter(
        item=OuterRef("pk"),
        datetime__gte=now,
    )

    latest_comic_event = Event.objects.filter(
        item=OuterRef("pk"),
        item__media_type=MediaTypes.COMIC.value,
    ).order_by("-datetime")

    annotated = items.annotate(
        has_future_events=Exists(future_events),
        latest_comic_event_datetime=Subquery(latest_comic_event.values("datetime")[:1]),
    )

    tv_q = Q(id__in=tv_items_to_include)
    movie_q = Q(id__in=movie_items_to_include)

    comic_q = Q(media_type=MediaTypes.COMIC.value) & (
        Q(event__isnull=True) | Q(latest_comic_event_datetime__gte=one_year_ago)
    )

    other_q = (
        ~Q(media_type__in=[MediaTypes.TV.value, MediaTypes.COMIC.value])
        & ~Q(media_type=MediaTypes.MOVIE.value, source=Sources.TMDB.value)
        & (Q(event__isnull=True) | Q(has_future_events=True))
    )

    return annotated.filter(tv_q | movie_q | comic_q | other_q).distinct()


def get_tv_items_to_include(tv_items):
    """Return tracked TMDB TV item ids that should be refreshed."""
    if not tv_items.exists():
        return []

    changed_tv_ids = get_changed_tmdb_tv_ids()
    season_events = Event.objects.filter(
        item__media_id=OuterRef("media_id"),
        item__source=OuterRef("source"),
        item__media_type=MediaTypes.SEASON.value,
    )

    return list(
        tv_items.annotate(
            has_season_events=Exists(season_events),
        )
        .filter(
            Q(media_id__in=changed_tv_ids) | Q(has_season_events=False),
        )
        .values_list("id", flat=True),
    )


def get_movie_items_to_include(movie_items):
    """Return tracked TMDB movie item ids that should be refreshed."""
    if not movie_items.exists():
        return []

    changed_movie_ids = get_changed_tmdb_movie_ids()

    return list(
        movie_items.filter(
            Q(media_id__in=changed_movie_ids) | Q(event__isnull=True),
        ).values_list("id", flat=True),
    )


def get_changed_tmdb_tv_ids():
    """Return changed TMDB TV ids, tolerating provider errors."""
    try:
        return tmdb.tv_changes()
    except services.ProviderAPIError:
        logger.warning("Failed to fetch TMDB TV changes")
        return set()


def get_changed_tmdb_movie_ids():
    """Return changed TMDB movie ids, tolerating provider errors."""
    try:
        return tmdb.movie_changes()
    except services.ProviderAPIError:
        logger.warning("Failed to fetch TMDB movie changes")
        return set()
