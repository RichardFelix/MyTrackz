from simple_history.utils import bulk_create_with_history, bulk_update_with_history

import app


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
