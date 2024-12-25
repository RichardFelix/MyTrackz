import csv
import logging

from django.apps import apps
from django.db.models import Field

from app import helpers
from app.models import Item

logger = logging.getLogger(__name__)


def db_to_csv(response, user):
    """Export a CSV file of the user's media."""
    fields = {
        "item": get_model_fields(Item),
        "track": get_track_fields(),
    }

    writer = csv.writer(response, quoting=csv.QUOTE_ALL)
    writer.writerow(fields["item"] + fields["track"])

    media_types = Item.MediaTypes.values
    for media_type in media_types:
        model = apps.get_model("app", media_type)
        if media_type == "episode":
            queryset = model.objects.filter(related_season__user=user)
        else:
            queryset = model.objects.filter(user=user)
        write_model_to_csv(writer, fields, queryset, media_type)

    return response


def get_model_fields(model):
    """Get a list of fields names from a model."""
    return [
        field.name
        for field in model._meta.get_fields()  # noqa: SLF001
        if isinstance(field, Field) and not field.auto_created and not field.is_relation
    ]


def get_track_fields():
    """Get a list of all track fields from all media models."""
    all_fields = []

    for media_type in Item.MediaTypes.values:
        model = apps.get_model("app", media_type)
        for field in get_model_fields(model):
            if field not in all_fields:
                all_fields.append(field)

    # Put start_date and end_date next to each other
    # happens because Episode has end_date but not start_date
    if "start_date" in all_fields and "end_date" in all_fields:
        end_idx = all_fields.index("end_date")

        # Remove both dates
        all_fields.remove("start_date")
        all_fields.remove("end_date")
        # Insert them in the correct order at the earlier index
        all_fields.insert(end_idx, "end_date")
        all_fields.insert(end_idx, "start_date")

    return list(all_fields)


def write_model_to_csv(writer, fields, queryset, media_type):
    """Export entries from a model to a CSV file."""
    logger.info("Adding %ss to CSV", media_type)

    for media in queryset:
        # row with item and track fields
        row = [getattr(media.item, field, "") for field in fields["item"]] + [
            getattr(media, field, "") for field in fields["track"]
        ]

        if media_type == "game":
            # calculate index of progress field
            progress_index = fields["track"].index("progress")
            row[progress_index + len(fields["item"])] = helpers.minutes_to_hhmm(
                media.progress,
            )

        writer.writerow(row)
