import datetime
from collections import defaultdict

from django.apps import apps
from django.db import models
from django.db.models import (
    Case,
    Count,
    F,
    FloatField,
    IntegerField,
    Max,
    Min,
    Prefetch,
    Q,
    When,
)
from django.db.models.functions import Cast, TruncDate
from django.utils import timezone

from app.models import Episode, Item, Media, MediaTypes, Season


def get_media_list(user, media_type, status_filter, sort_filter, search=None):
    """Get media list based on filters, sorting and search."""
    model = apps.get_model(app_label="app", model_name=media_type)
    queryset = model.objects.filter(user=user.id)

    if "All" not in status_filter:
        queryset = queryset.filter(status__in=status_filter)

    if search:
        queryset = queryset.filter(item__title__icontains=search)

    queryset = queryset.select_related("item")

    # Apply prefetch related based on media type
    if media_type == "tv":
        # For TV, prefetch seasons and their episodes with their items
        queryset = queryset.prefetch_related(
            Prefetch(
                "seasons",
                queryset=Season.objects.select_related("item"),
            ),
            Prefetch(
                "seasons__episodes",
                queryset=Episode.objects.select_related("item"),
            ),
        )
    elif media_type == "season":
        # For Season, prefetch episodes with their items
        queryset = queryset.prefetch_related(
            Prefetch(
                "episodes",
                queryset=Episode.objects.select_related("item"),
            ),
        )

    sort_is_property = sort_filter in get_properties(model)
    sort_is_item_field = sort_filter in get_fields(Item)

    if media_type in ("tv", "season") and sort_is_property:
        # For date fields, handle None values specially
        if sort_filter in ("start_date", "end_date"):
            # Convert queryset to list for manual sorting
            result_list = list(queryset)

            # Split into items with dates and without dates
            with_dates = [
                item for item in result_list if getattr(item, sort_filter) is not None
            ]
            without_dates = [
                item for item in result_list if getattr(item, sort_filter) is None
            ]

            # Sort items with dates
            if sort_filter == "start_date":
                # For start_date, sort ascending (earliest first)
                sorted_with_dates = sorted(
                    with_dates,
                    key=lambda x: getattr(x, sort_filter),
                )
            else:
                # For other date fields, sort descending (latest first)
                sorted_with_dates = sorted(
                    with_dates,
                    key=lambda x: getattr(x, sort_filter),
                    reverse=True,
                )

            # Combine lists - items with dates first, then items without dates
            return sorted_with_dates + without_dates
        # For non-date fields, use the original logic
        return sorted(queryset, key=lambda x: getattr(x, sort_filter), reverse=True)

    if sort_is_item_field:
        sort_field = f"item__{sort_filter}"
        return queryset.order_by(
            F(sort_field).asc() if sort_filter == "title" else F(sort_field).desc(),
        )

    return queryset.order_by(F(sort_filter).desc(nulls_last=True))


def get_fields(model):
    """Get fields of a model."""
    return [f.name for f in model._meta.fields]  # noqa: SLF001


def get_unique_constraint_fields(model):
    """Get fields that make up the unique constraint for the model."""
    for constraint in model._meta.constraints:  # noqa: SLF001
        if isinstance(constraint, models.UniqueConstraint):
            return constraint.fields
    return None


def get_properties(model):
    """Get properties of a model."""
    return [name for name in dir(model) if isinstance(getattr(model, name), property)]


def get_historical_models():
    """Return list of historical model names."""
    media_types = MediaTypes.values
    return [f"historical{media_type}" for media_type in media_types]


def get_in_progress(user, sort_by):
    """Get a media list of in progress media by type."""
    today = timezone.now().date()
    list_by_type = {}

    for media_type in MediaTypes.values:
        # dont show tv and episodes in home page
        if media_type not in ("tv", "episode"):
            media_list = get_media_list(
                user=user,
                media_type=media_type,
                status_filter=[
                    Media.Status.IN_PROGRESS.value,
                    Media.Status.REPEATING.value,
                ],
                sort_filter="score",
            )

            if media_list.exists():
                # Add common annotations
                media_list = media_list.annotate(
                    max_progress=Max("item__event__episode_number"),
                    next_episode_number=Min(
                        "item__event__episode_number",
                        filter=Q(item__event__date__gt=today),
                    ),
                    next_episode_date=Min(
                        "item__event__date",
                        filter=Q(item__event__date__gt=today),
                    ),
                )

                # Handle sorting based on model type
                if sort_by == "upcoming":
                    media_list = media_list.order_by(
                        Case(
                            When(next_episode_date__isnull=True, then=1),
                            default=0,
                        ),
                        "next_episode_date",
                        "item__title",
                    )
                elif sort_by == "title":
                    media_list = media_list.order_by("item__title")
                elif sort_by in ["completion", "episodes_left"]:
                    # For Season, we need to evaluate the queryset and sort in Python
                    if media_type == "season":
                        media_list = list(media_list)
                        if sort_by == "completion":
                            media_list.sort(
                                key=lambda x: (
                                    x.max_progress is not None,
                                    (
                                        x.progress / x.max_progress * 100
                                        if x.max_progress
                                        else 0
                                    ),
                                    x.item.title,
                                ),
                                reverse=True,
                            )
                        else:  # episodes_left
                            media_list.sort(
                                key=lambda x: (
                                    x.max_progress is not None,
                                    (
                                        x.max_progress - x.progress
                                        if x.max_progress
                                        else 0
                                    ),
                                    x.item.title,
                                ),
                            )
                    else:
                        # For other media types, use database annotations
                        media_list = media_list.annotate(
                            completion_rate=Case(
                                When(
                                    max_progress__isnull=False,
                                    then=(Cast("progress", FloatField()) * 100.0)
                                    / Cast("max_progress", FloatField()),
                                ),
                                default=0.0,
                                output_field=FloatField(),
                            ),
                            episodes_remaining=Case(
                                When(
                                    max_progress__isnull=False,
                                    then=F("max_progress") - F("progress"),
                                ),
                                default=0,
                                output_field=IntegerField(),
                            ),
                        )
                        if sort_by == "completion":
                            media_list = media_list.order_by(
                                Case(
                                    When(max_progress__isnull=True, then=0),
                                    default=1,
                                ),
                                "-completion_rate",
                                "item__title",
                            )
                        else:  # episodes_left
                            media_list = media_list.order_by(
                                Case(
                                    When(max_progress__isnull=True, then=1),
                                    default=0,
                                ),
                                "episodes_remaining",
                                "item__title",
                            )

                list_by_type[media_type] = media_list

    return list_by_type


def get_media(media_type, item, user):
    """Get user media object given the media type and item."""
    model = apps.get_model(app_label="app", model_name=media_type)
    params = {"item": item}

    if media_type == "episode":
        params["related_season__user"] = user
    else:
        params["user"] = user

    try:
        return model.objects.get(**params)
    except model.DoesNotExist:
        return None


def get_filtered_historical_data(start_date, end_date, user):
    """Get historical data filtered by date range."""
    historical_models = get_historical_models()
    combined_data = []

    for model_name in historical_models:
        historical_model = apps.get_model("app", model_name)

        # Filter historical records
        data = (
            historical_model.objects.filter(
                history_user_id=user,
                history_date__date__gte=start_date,
                history_date__date__lte=end_date,
            )
            .annotate(date=TruncDate("history_date"))
            .values("date")
            .annotate(count=Count("id"))
        )

        combined_data.extend(data)

    return combined_data


def calculate_day_of_week_stats(date_counts, start_date):
    """Calculate the most active day of the week based on activity frequency.

    Returns the day name and its percentage of total activity.
    """
    # Initialize counters for each day of the week
    day_counts = defaultdict(int)
    total_active_days = 0

    # Count occurrences of each day of the week where activity happened
    for date in date_counts:
        if date < start_date:
            continue
        if date_counts[date] > 0:
            day_name = date.strftime("%A")  # Get full day name
            day_counts[day_name] += 1
            total_active_days += 1

    if not total_active_days:
        return None, 0

    # Find the most active day
    most_active_day = max(day_counts.items(), key=lambda x: x[1])
    percentage = (most_active_day[1] / total_active_days) * 100

    return most_active_day[0], round(percentage)


def calculate_streaks(date_counts, start_date, end_date):
    """Calculate current and longest activity streaks.

    Returns tuple of (current_streak, longest_streak).
    """
    if not date_counts:
        return 0, 0

    current_streak = 0
    longest_streak = 0
    temp_streak = 0

    # Convert date_counts to sorted list of dates
    active_dates = sorted(date for date in date_counts if date >= start_date)

    # Calculate streaks
    for i in range(len(active_dates)):
        if i == 0:
            temp_streak = 1
            continue

        if (active_dates[i] - active_dates[i - 1]).days == 1:
            temp_streak += 1
        else:
            longest_streak = max(longest_streak, temp_streak)
            temp_streak = 1

    # Update longest streak one last time
    longest_streak = max(longest_streak, temp_streak)

    # Calculate current streak
    if active_dates:
        current_date = end_date
        current_streak = 0

        while current_date in date_counts and current_date >= start_date:
            current_streak += 1
            current_date -= datetime.timedelta(days=1)

    return current_streak, longest_streak


def get_activity_data(user, start_date, end_date):
    """Get daily activity counts for the last year."""
    # Get the Monday of the week containing start_date (for grid alignment)
    start_date_aligned = start_date - datetime.timedelta(days=start_date.weekday())

    combined_data = get_filtered_historical_data(start_date_aligned, end_date, user)

    # Aggregate counts by date
    date_counts = {}
    for item in combined_data:
        date = item["date"]
        date_counts[date] = date_counts.get(date, 0) + item["count"]

    date_range = [
        start_date_aligned + datetime.timedelta(days=x)
        for x in range((end_date - start_date_aligned).days + 1)
    ]

    # Calculate activity statistics
    most_active_day, day_percentage = calculate_day_of_week_stats(
        date_counts,
        start_date,
    )
    current_streak, longest_streak = calculate_streaks(
        date_counts,
        start_date,
        end_date,
    )

    # Create complete date range including padding days
    activity_data = [
        {
            "date": current_date.strftime("%Y-%m-%d"),
            "count": date_counts.get(current_date, 0),
            "level": get_level(date_counts.get(current_date, 0)),
            "disabled": current_date < start_date,
        }
        for current_date in date_range
    ]

    # Format data into calendar weeks
    calendar_weeks = [activity_data[i : i + 7] for i in range(0, len(activity_data), 7)]

    # Generate months list with their Monday counts
    months = []
    mondays_per_month = []
    current_month = date_range[0].strftime("%b")
    monday_count = 0

    for current_date in date_range:
        if current_date.weekday() == 0:  # Monday
            month = current_date.strftime("%b")

            if current_month != month:
                if current_month is not None:
                    if monday_count > 1:
                        months.append(current_month)
                        mondays_per_month.append(monday_count)
                    else:
                        months.append("")
                        mondays_per_month.append(monday_count)
                current_month = month
                monday_count = 0

            monday_count += 1
    # For the last month
    if monday_count > 1:
        months.append(current_month)
        mondays_per_month.append(monday_count)

    return {
        "weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        "calendar_weeks": calendar_weeks,
        "months": list(zip(months, mondays_per_month, strict=False)),
        "stats": {
            "most_active_day": most_active_day,
            "most_active_day_percentage": day_percentage,
            "current_streak": current_streak,
            "compared_to_longest_streak": longest_streak - current_streak,
        },
    }


def get_level(count):
    """Calculate intensity level (0-4) based on count."""
    thresholds = [0, 3, 6, 9]
    for i, threshold in enumerate(thresholds):
        if count <= threshold:
            return i
    return 4
