import calendar
import logging
from datetime import date, timedelta

from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from events import tasks
from events.models import Event

logger = logging.getLogger(__name__)


@require_GET
def release_calendar(request):
    """Display the calendar page."""
    # Handle view type
    view_type = request.user.update_preference(
        "calendar_layout",
        request.GET.get("view"),
    )

    month = request.GET.get("month")
    year = request.GET.get("year")

    try:
        current_date = (
            date(int(year), int(month), 1) if month and year else timezone.now().date()
        )
        month, year = current_date.month, current_date.year
    except (ValueError, TypeError):
        logging.warning("Invalid month or year provided: %s, %s", month, year)
        current_date = timezone.now().date()
        month, year = current_date.month, current_date.year

    # Calculate navigation dates
    is_december = month == 12  # noqa: PLR2004
    is_january = month == 1

    prev_month = 12 if is_january else month - 1
    prev_year = year - 1 if is_january else year

    next_month = 1 if is_december else month + 1
    next_year = year + 1 if is_december else year

    # Calculate date range for events
    first_day = date(year, month, 1)
    last_day = date(
        year + 1 if is_december else year,
        1 if is_december else month + 1,
        1,
    ) - timedelta(days=1)

    # Get calendar data
    cal = calendar.monthcalendar(year, month)
    month_name = calendar.month_name[month]

    # Get events and organize by day
    releases = Event.objects.get_user_events(request.user, first_day, last_day)

    release_dict = {}
    for release in releases:
        day = release.date.day
        if day not in release_dict:
            release_dict[day] = []
        release_dict[day].append(release)

    # Get today's date for highlighting
    today = timezone.now().date()

    context = {
        "calendar": cal,
        "month": month,
        "month_name": month_name,
        "year": year,
        "prev_month": prev_month,
        "prev_year": prev_year,
        "next_month": next_month,
        "next_year": next_year,
        "release_dict": release_dict,
        "today": today,
        "view_type": view_type,
    }
    return render(request, "events/calendar.html", context)


@require_POST
def reload_calendar(request):
    """Refresh the calendar with the latest dates."""
    tasks.reload_calendar.delay(request.user)
    messages.info(request, "The task to refresh upcoming releases has been queued.")
    return redirect("release_calendar")
