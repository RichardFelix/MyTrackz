import datetime
import json

from django.contrib import messages
from django.utils import timezone
from django_celery_beat.models import CrontabSchedule, PeriodicTask
from simple_history.utils import bulk_create_with_history, bulk_update_with_history

import app


def update_season_references(seasons, user):
    """Update season references with actual TV instances.

    When bulk_create skips existing TV shows, seasons would still reference
    the unsaved TV instances. This updates those references to point to
    the existing TV shows in the database, preventing the ValueError about
    unsaved related objects during bulk creation of seasons.
    """
    # Get existing TV shows from database
    existing_tv = {
        tv.item.media_id: tv
        for tv in app.models.TV.objects.filter(
            user=user,
            item__media_id__in=[season.item.media_id for season in seasons],
        )
    }

    # Update references
    for season in seasons:
        media_id = season.item.media_id
        if media_id in existing_tv:
            season.related_tv = existing_tv[media_id]


def update_episode_references(episodes, user):
    """Update episode references with actual Season instances.

    When bulk_create skips existing seasons, episodes would still reference
    the unsaved season instances. This updates those references to point to
    the existing seasons in the database, preventing the ValueError about
    unsaved related objects during bulk creation of episodes.
    """
    # Create mapping of season instances
    existing_seasons = {
        (season.item.media_id, season.item.season_number): season
        for season in app.models.Season.objects.filter(
            user=user,
            item__media_id__in={episode.item.media_id for episode in episodes},
        )
    }

    # Update references
    for episode in episodes:
        season_key = (
            episode.item.media_id,
            episode.item.season_number,
        )
        if season_key in existing_seasons:
            episode.related_season = existing_seasons[season_key]


def bulk_chunk_import(bulk_media, model, user, mode):
    """Bulk import media in chunks."""
    if mode == "new":
        num_imported = bulk_create_new_with_history(bulk_media, model, user)

    elif mode == "overwrite":
        num_imported = bulk_create_update_with_history(
            bulk_media,
            model,
            user,
        )

    return num_imported


def bulk_create_new_with_history(bulk_media, model, user):
    """Filter out existing records and bulk create only new ones."""
    # Get existing records' unique IDs since bulk_create_with_history
    # returns all objects even if they weren't created due to conflicts
    unique_fields = app.database.get_unique_constraint_fields(model)
    existing_combos = set(
        model.objects.values_list(*unique_fields),
    )

    new_records = [
        record
        for record in bulk_media
        if tuple(getattr(record, field + "_id") for field in unique_fields)
        not in existing_combos
    ]

    bulk_create_with_history(
        new_records,
        model,
        batch_size=500,
        default_user=user,
    )

    return len(new_records)


def bulk_create_update_with_history(
    bulk_media,
    model,
    user,
):
    """Bulk create new records and update existing ones with history tracking."""
    unique_fields = app.database.get_unique_constraint_fields(model)
    model_fields = app.database.get_fields(model)
    update_fields = [
        field for field in model_fields if field not in unique_fields and field != "id"
    ]

    # Get existing objects with their unique fields and id
    existing_objs = model.objects.filter(
        **{
            f"{field}__in": [getattr(obj, field + "_id") for obj in bulk_media]
            for field in unique_fields
        },
    ).values(*unique_fields, "id")

    # Create lookup dictionary using unique field combinations
    existing_lookup = {
        tuple(obj[field] for field in unique_fields): obj["id"] for obj in existing_objs
    }

    # Split records into new and existing based on unique constraints
    create_objs = []
    update_objs = []

    for record in bulk_media:
        record_key = tuple(getattr(record, field + "_id") for field in unique_fields)
        if record_key in existing_lookup:
            # Set the primary key for update
            record.id = existing_lookup[record_key]
            update_objs.append(record)
        else:
            create_objs.append(record)

    # Bulk create new records
    num_created = 0
    if create_objs:
        created_objs = bulk_create_with_history(
            create_objs,
            model,
            batch_size=500,
            default_user=user,
        )
        num_created = len(created_objs)

    # Bulk update existing records
    num_updated = 0
    if update_objs and update_fields:
        num_updated = bulk_update_with_history(
            update_objs,
            model,
            fields=update_fields,
            batch_size=500,
            default_user=user,
        )

    return num_created + num_updated


def create_import_schedule(username, request, mode, frequency, import_time, source):
    """Create an import schedule."""
    try:
        import_time = (
            datetime.datetime.strptime(import_time, "%H:%M")
            .astimezone(
                timezone.get_default_timezone(),
            )
            .time()
        )
    except ValueError:
        messages.error(request, "Invalid import time.")
        return

    task_name = f"Import from {source} for {username} at {import_time} {frequency}"
    if PeriodicTask.objects.filter(name=task_name).exists():
        messages.error(
            request,
            "The same import task is already scheduled.",
        )
        return

    crontab = CrontabSchedule.objects.create(
        hour=import_time.hour,
        minute=import_time.minute,
        day_of_week="*" if frequency == "daily" else "*/2",
        timezone=timezone.get_default_timezone(),
    )
    # Create new periodic task
    PeriodicTask.objects.create(
        name=task_name,
        task=f"Import from {source}",
        crontab=crontab,
        kwargs=json.dumps(
            {
                "username": username,
                "user_id": request.user.id,
                "mode": mode,
            },
        ),
        start_time=timezone.now(),
    )
    messages.success(request, f"{source} import task scheduled.")
