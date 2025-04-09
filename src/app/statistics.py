import calendar
import datetime
import heapq
import itertools
import logging
from collections import defaultdict

from dateutil.relativedelta import relativedelta
from django.apps import apps
from django.db import models
from django.db.models import (
    Count,
    Prefetch,
    Q,
)
from django.db.models.functions import TruncDate

from app import media_type_config
from app.models import TV, BasicMedia, Episode, Media, MediaTypes, Season
from app.templatetags import app_tags

logger = logging.getLogger(__name__)


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
        end_date,
    )

    # Create complete date range including padding days
    activity_data = [
        {
            "date": current_date.strftime("%Y-%m-%d"),
            "count": date_counts.get(current_date, 0),
            "level": get_level(date_counts.get(current_date, 0)),
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
        "calendar_weeks": calendar_weeks,
        "months": list(zip(months, mondays_per_month, strict=False)),
        "stats": {
            "most_active_day": most_active_day,
            "most_active_day_percentage": day_percentage,
            "current_streak": current_streak,
            "longest_streak": longest_streak,
        },
    }


def get_level(count):
    """Calculate intensity level (0-4) based on count."""
    thresholds = [0, 3, 6, 9]
    for i, threshold in enumerate(thresholds):
        if count <= threshold:
            return i
    return 4


def get_filtered_historical_data(start_date, end_date, user):
    """Get historical data filtered by date range."""
    historical_models = BasicMedia.objects.get_historical_models()
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


def calculate_streaks(date_counts, end_date):
    """Calculate current and longest activity streaks."""
    # Get active dates and sort them in descending order (newest first)
    active_dates = sorted(
        [date for date, count in date_counts.items() if count > 0],
        reverse=True,
    )

    if not active_dates:
        return 0, 0

    longest_streak = 1
    streak_count = 1

    # Check if the most recent active date is today/end_date
    is_current = active_dates[0] == end_date

    current_streak = 1 if is_current else 0

    for i in range(1, len(active_dates)):
        # Check if this date is consecutive with the previous one
        if (active_dates[i - 1] - active_dates[i]).days == 1:
            streak_count += 1

            if is_current:
                current_streak += 1
        else:
            longest_streak = max(longest_streak, streak_count)
            streak_count = 1

            if is_current:
                is_current = False

    # Check final streak for longest calculation
    # needed if the last date is today/end_date
    longest_streak = max(longest_streak, streak_count)

    return current_streak, longest_streak


def get_user_media(user, start_date, end_date):
    """Get all media items and their counts for a user within date range."""
    media_models = [
        apps.get_model(app_label="app", model_name=media_type)
        for media_type in user.get_enabled_media_types()
        if media_type != MediaTypes.EPISODE.value
    ]
    user_media = {}
    media_count = {"total": 0}

    # Cache the base episodes query
    base_episodes = None
    if TV in media_models or Season in media_models:
        base_episodes = Episode.objects.filter(
            related_season__user=user,
            end_date__range=(start_date, end_date),
        )

    for model in media_models:
        media_type = model.__name__.lower()
        queryset = None

        if model == TV:
            tv_ids = base_episodes.values_list(
                "related_season__related_tv",
                flat=True,
            ).distinct()
            queryset = TV.objects.filter(id__in=tv_ids).prefetch_related(
                Prefetch(
                    "seasons",
                    queryset=Season.objects.select_related(
                        "item",
                    ).prefetch_related(
                        Prefetch(
                            "episodes",
                            queryset=base_episodes.filter(
                                related_season__related_tv__in=tv_ids,
                            ),
                        ),
                    ),
                ),
            )
        elif model == Season:
            season_ids = base_episodes.values_list(
                "related_season",
                flat=True,
            ).distinct()
            queryset = Season.objects.filter(
                id__in=season_ids,
            ).prefetch_related(
                Prefetch("episodes", queryset=base_episodes),
            )
        else:
            queryset = model.objects.filter(
                user=user,
                start_date__isnull=False,  # Exclude records with null start_date
                start_date__gte=start_date,
                start_date__lte=end_date,  # Ensure start_date is within range
            ).filter(
                # Either end_date is null OR end_date is within range
                Q(end_date__isnull=True)
                | Q(end_date__gte=start_date, end_date__lte=end_date),
            )

        queryset = queryset.select_related("item")
        user_media[media_type] = queryset
        count = queryset.count()
        media_count[media_type] = count
        media_count["total"] += count

    logger.info("%s - Retrieved media from %s to %s", user, start_date, end_date)
    return user_media, media_count


def get_media_type_distribution(media_count):
    """Get data formatted for Chart.js pie chart."""
    # Define colors for each media type
    # Format for Chart.js
    chart_data = {
        "labels": [],
        "datasets": [
            {
                "data": [],
                "backgroundColor": [],
            },
        ],
    }

    # Only include media types with counts > 0
    for media_type, count in media_count.items():
        if media_type != "total" and count > 0:
            # Format label with first letter capitalized
            label = app_tags.media_type_readable(media_type)
            chart_data["labels"].append(label)
            chart_data["datasets"][0]["data"].append(count)
            chart_data["datasets"][0]["backgroundColor"].append(
                media_type_config.get_stats_color(media_type),
            )
    return chart_data


def get_status_distribution(user_media):
    """Get status distribution for each media type within date range."""
    distribution = {}
    total_completed = 0
    # Define status order to ensure consistent stacking
    status_order = list(Media.Status.values)
    for media_type, media_list in user_media.items():
        status_counts = dict.fromkeys(status_order, 0)
        counts = media_list.values("status").annotate(count=models.Count("id"))
        for count_data in counts:
            status_counts[count_data["status"]] = count_data["count"]
            if count_data["status"] == Media.Status.COMPLETED.value:
                total_completed += count_data["count"]

        distribution[media_type] = status_counts

    # Format the response for charting
    return {
        "labels": [app_tags.media_type_readable(x) for x in distribution],
        "datasets": [
            {
                "label": status,
                "data": [
                    distribution[media_type][status] for media_type in distribution
                ],
                "background_color": get_status_color(status),
                "total": sum(
                    distribution[media_type][status] for media_type in distribution
                ),
            }
            for status in status_order
        ],
        "total_completed": total_completed,
    }


def get_status_pie_chart_data(status_distribution):
    """Get status distribution as a pie chart."""
    # Format for Chart.js pie chart
    chart_data = {
        "labels": [],
        "datasets": [
            {
                "data": [],
                "backgroundColor": [],
            },
        ],
    }

    # Process each status dataset
    for dataset in status_distribution["datasets"]:
        status_label = dataset["label"]
        status_count = dataset["total"]
        status_color = dataset["background_color"]

        # Only include statuses with counts > 0
        if status_count > 0:
            chart_data["labels"].append(status_label)
            chart_data["datasets"][0]["data"].append(status_count)
            chart_data["datasets"][0]["backgroundColor"].append(status_color)

    return chart_data


def get_score_distribution(user_media):
    """Get score distribution for each media type within date range."""
    distribution = {}
    total_scored = 0
    total_score_sum = 0

    # Use heapq to maintain top items efficiently
    top_rated = []
    top_rated_count = 12
    counter = itertools.count()  # For unique identifiers

    # Define score range (0-10)
    score_range = range(11)

    for media_type, media_list in user_media.items():
        # Initialize score counts for this media type
        score_counts = dict.fromkeys(score_range, 0)

        # Get all scored media with their scores
        scored_media = media_list.exclude(score__isnull=True).select_related("item")

        # Process each media item
        for media in scored_media:
            # Update top rated using heap
            item_data = {
                "title": media.item.__str__(),
                "image": media.item.image,
                "score": media.score,
                "url": app_tags.media_url(media.item),
            }

            # Use negative score for max heap (heapq implements min heap)
            # Add counter as tiebreaker
            if len(top_rated) < top_rated_count:
                heapq.heappush(
                    top_rated,
                    (float(media.score), next(counter), item_data),
                )
            else:
                heapq.heappushpop(
                    top_rated,
                    (float(media.score), next(counter), item_data),
                )

            # Bin the score
            binned_score = int(media.score)
            score_counts[binned_score] += 1

            # Update totals with exact score
            total_scored += 1
            total_score_sum += media.score

        distribution[media_type] = score_counts

    # Calculate average score
    average_score = (
        round(total_score_sum / total_scored, 2) if total_scored > 0 else None
    )

    # Convert heap to sorted list of top rated items
    top_rated = [
        item_data for _, _, item_data in sorted(top_rated, key=lambda x: (-x[0], x[1]))
    ]

    return {
        "labels": [str(score) for score in score_range],  # 0-10 as labels
        "datasets": [
            {
                "label": app_tags.media_type_readable(media_type),
                "data": [distribution[media_type][score] for score in score_range],
                "background_color": media_type_config.get_stats_color(media_type),
            }
            for media_type in distribution
        ],
        "average_score": average_score,
        "total_scored": total_scored,
        "top_rated": top_rated,
    }


def get_status_color(status):
    """Get the color for the status of the media."""
    colors = {
        Media.Status.IN_PROGRESS.value: media_type_config.get_stats_color(
            MediaTypes.EPISODE.value,
        ),
        Media.Status.COMPLETED.value: media_type_config.get_stats_color(
            MediaTypes.TV.value,
        ),
        Media.Status.REPEATING.value: media_type_config.get_stats_color(
            MediaTypes.SEASON.value,
        ),
        Media.Status.PLANNING.value: media_type_config.get_stats_color(
            MediaTypes.ANIME.value,
        ),
        Media.Status.PAUSED.value: media_type_config.get_stats_color(
            MediaTypes.MOVIE.value,
        ),
        Media.Status.DROPPED.value: media_type_config.get_stats_color(
            MediaTypes.MANGA.value,
        ),
    }
    return colors.get(status, "rgba(201, 203, 207)")


def get_timeline(user_media):
    """Build a timeline of media consumption organized by month-year."""
    timeline = defaultdict(list)

    # Process each media type
    for media_type, queryset in user_media.items():
        if media_type == "tv":
            continue
        for media in queryset:
            # If there's an end date, add media to all months between start and end
            if media.end_date:
                current_date = media.start_date
                while current_date <= media.end_date:
                    year = current_date.year
                    month = current_date.month
                    month_name = calendar.month_name[month]
                    month_year = f"{month_name} {year}"

                    timeline[month_year].append(media)

                    # Move to next month
                    current_date += relativedelta(months=1)
                    current_date = current_date.replace(day=1)
            else:
                # If no end date, only add to the start month
                year = media.start_date.year
                month = media.start_date.month
                month_name = calendar.month_name[month]
                month_year = f"{month_name} {year}"

                timeline[month_year].append(media)

    # Convert to sorted dictionary with media sorted by start date
    # Create a list sorted by year and month in reverse order
    sorted_items = []
    for month_year, media_list in timeline.items():
        month_name, year_str = month_year.split()
        year = int(year_str)
        month = list(calendar.month_name).index(month_name)
        sorted_items.append((month_year, media_list, year, month))

    # Sort by year and month in reverse chronological order
    sorted_items.sort(key=lambda x: (x[2], x[3]), reverse=True)

    # Create the final result dictionary
    result = {}
    for month_year, media_list, _, _ in sorted_items:
        result[month_year] = sorted(media_list, key=lambda x: x.start_date)

    return result
