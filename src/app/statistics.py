import calendar
import datetime
import heapq
import itertools
import logging
from collections import defaultdict

from dateutil.relativedelta import relativedelta
from django.apps import apps
from django.core.cache import cache
from django.db import models
from django.db.models import (
    Count,
    Min,
    Prefetch,
    Q,
)
from django.db.models.functions import TruncDate

from app import helpers
from app.models import TV, BasicMedia, Colors, Episode, Media, MediaTypes, Season
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


def get_earliest_media_start_date(user):
    """Get the earliest start date across all media types for a user."""
    earliest_date = None

    # Get all media types except season and tv which are handled differently
    media_types = [mt for mt in MediaTypes.values if mt not in ["season", "tv"]]

    # Process Episode model separately due to its unique relationship structure
    episode_model = apps.get_model("app", "episode")
    earliest_episode_date = episode_model.objects.filter(
        related_season__related_tv__user=user,
        end_date__isnull=False,
    ).aggregate(earliest=Min("end_date"))["earliest"]

    if earliest_episode_date:
        earliest_date = earliest_episode_date
        logger.info(
            "%s - Found earliest TV start_date via episodes: %s",
            user.username,
            earliest_date,
        )

    # Process all other media types
    for media_type in media_types:
        # Skip Episode as it's already processed
        if media_type == "episode":
            continue

        model = apps.get_model("app", media_type)
        earliest_start = model.objects.filter(
            user=user,
            start_date__isnull=False,
        ).aggregate(earliest=Min("start_date"))["earliest"]

        if earliest_start and (earliest_date is None or earliest_start < earliest_date):
            earliest_date = earliest_start
            logger.info(
                "%s - Found earlier start_date: %s from model %s",
                user.username,
                earliest_date,
                model.__name__,
            )

    return earliest_date


def get_earliest_historical_date(user):
    """Get the earliest historical record date for a user across all models."""
    earliest_date = None

    # Check historical records for earliest history date
    historical_models = BasicMedia.objects.get_historical_models()

    for model_name in historical_models:
        try:
            historical_model = apps.get_model("app", model_name)
            earliest_history = historical_model.objects.filter(
                history_user_id=user.id,
                history_date__isnull=False,
            ).aggregate(earliest=Min("history_date"))["earliest"]

            if earliest_history:
                # Convert datetime to date for comparison if needed
                history_date = (
                    earliest_history.date()
                    if hasattr(earliest_history, "date")
                    else earliest_history
                )

                if earliest_date is None or history_date < earliest_date:
                    earliest_date = history_date
                    logger.info(
                        "Found earlier history_date: %s from model %s",
                        earliest_date,
                        model_name,
                    )
        except LookupError:
            logger.warning("Historical model %s not found", model_name)
        except Exception:
            logger.exception("Error checking historical model %s", model_name)

    # Also check Episode historical records
    try:
        earliest_episode_history = Episode.history.filter(
            history_user_id=user.id,
            history_date__isnull=False,
        ).aggregate(earliest=Min("history_date"))["earliest"]

        if earliest_episode_history:
            episode_history_date = (
                earliest_episode_history.date()
                if hasattr(earliest_episode_history, "date")
                else earliest_episode_history
            )

            if earliest_date is None or episode_history_date < earliest_date:
                earliest_date = episode_history_date
                logger.info("Found earlier episode history_date: %s", earliest_date)
    except Exception:
        logger.exception("Error checking Episode history")

    return earliest_date


def get_first_interaction_date(user):
    """
    Get the first interaction date with the app for a specific user.

    This function checks both the earliest historical record date and the earliest
    start_date across all media types to determine when the user first interacted
    with the application.

    Args:
        user: The user to check first interaction for

    Returns:
        datetime.date: The earliest interaction date or None if no interactions found
    """
    cache_key = f"first_interaction_date_{user.id}"

    cached_result = cache.get(cache_key)
    if cached_result:
        return cached_result

    logger.info("Getting first interaction date for user: %s", user)

    # Get earliest dates from both sources
    earliest_media_date = get_earliest_media_start_date(user)
    earliest_history_date = get_earliest_historical_date(user)

    # Determine the overall earliest date
    if earliest_media_date and earliest_history_date:
        earliest_date = min(earliest_media_date, earliest_history_date)
    elif earliest_media_date:
        earliest_date = earliest_media_date
    else:
        earliest_date = earliest_history_date

    if earliest_date:
        logger.info("First interaction date for user %s: %s", user, earliest_date)
    else:
        earliest_date = user.date_joined.date()
        logger.info("No interaction found for user %s", user)

    cache.set(cache_key, earliest_date, 60 * 60 * 24)
    return earliest_date


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
        model for model in user.get_active_media_types() if model != Episode
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
        model_name = model.__name__.lower()
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
        user_media[model_name] = queryset
        count = queryset.count()
        media_count[model_name] = count
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
                helpers.tailwind_to_hex(
                    Colors[media_type.upper()]
                    .value.replace("text-", "")
                    .replace("-400", "-500"),
                ),
            )
    return chart_data


def get_status_distribution(user_media):
    """Get status distribution for each media type within date range."""
    distribution = {}
    total_completed = 0
    # Define status order to ensure consistent stacking
    status_order = list(Media.Status.values)
    for model_name, media_list in user_media.items():
        status_counts = dict.fromkeys(status_order, 0)
        counts = media_list.values("status").annotate(count=models.Count("id"))
        for count_data in counts:
            status_counts[count_data["status"]] = count_data["count"]
            if count_data["status"] == Media.Status.COMPLETED.value:
                total_completed += count_data["count"]

        distribution[model_name] = status_counts

    # Format the response for charting
    return {
        "labels": [app_tags.media_type_readable(x) for x in distribution],
        "datasets": [
            {
                "label": status,
                "data": [
                    distribution[model_name][status] for model_name in distribution
                ],
                "background_color": get_status_color(status),
                "total": sum(
                    distribution[model_name][status] for model_name in distribution
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

    for model_name, media_list in user_media.items():
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

        distribution[model_name] = score_counts

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
                "label": app_tags.media_type_readable(model_name),
                "data": [distribution[model_name][score] for score in score_range],
                "background_color": helpers.tailwind_to_hex(
                    Colors[model_name.upper()]
                    .value.replace("text-", "")
                    .replace("-400", "-500"),
                ),
            }
            for model_name in distribution
        ],
        "average_score": average_score,
        "total_scored": total_scored,
        "top_rated": top_rated,
    }


def get_status_color(status):
    """Get the color for the status of the media."""
    colors = {
        Media.Status.IN_PROGRESS.value: helpers.tailwind_to_hex("indigo-500"),
        Media.Status.COMPLETED.value: helpers.tailwind_to_hex("emerald-500"),
        Media.Status.REPEATING.value: helpers.tailwind_to_hex("purple-500"),
        Media.Status.PLANNING.value: helpers.tailwind_to_hex("blue-500"),
        Media.Status.PAUSED.value: helpers.tailwind_to_hex("orange-500"),
        Media.Status.DROPPED.value: helpers.tailwind_to_hex("red-500"),
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
                    key = f"{month_name} {year}"

                    timeline[key].append(media)

                    # Move to next month
                    current_date += relativedelta(months=1)
                    current_date = current_date.replace(day=1)
            else:
                # If no end date, only add to the start month
                year = media.start_date.year
                month = media.start_date.month
                month_name = calendar.month_name[month]
                key = f"{month_name} {year}"

                timeline[key].append(media)

    # Convert to sorted dictionary with media sorted by start date
    # Create a list of (key, media_list) sorted by year and month in reverse order
    sorted_items = []
    for key, media_list in timeline.items():
        month_name, year_str = key.split()
        year = int(year_str)
        month = list(calendar.month_name).index(month_name)
        sorted_items.append((key, media_list, year, month))

    # Sort by year and month in reverse chronological order
    sorted_items.sort(key=lambda x: (x[2], x[3]), reverse=True)

    # Create the final result dictionary
    result = {}
    for key, media_list, _, _ in sorted_items:
        result[key] = sorted(media_list, key=lambda x: x.start_date)

    return result
