"""The Admin page: instance-wide management for the instance owner.

Deliberately small and purpose-built. Django admin stays off; nothing here
edits arbitrary database rows.
"""

import json
import logging
import secrets
from datetime import timedelta
from functools import wraps
from pathlib import Path

from celery import current_app
from django.apps import apps
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.forms import SetPasswordForm
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from django_celery_beat.models import PeriodicTask
from django_celery_results.models import TaskResult

from app.models import Item, MediaTypes
from app.tasks import count_prunable_items, get_image_cache_stats
from users.models import User

logger = logging.getLogger(__name__)

# Shown once to the admin after a reset, then dropped from the session.
TEMP_PASSWORD_SESSION_KEY = "admin_temporary_password"  # noqa: S105
TEMP_PASSWORD_BYTES = 9  # 12 url-safe characters

# Scheduled tasks that message every user; running them by hand would send
# duplicates, so they are listed but have no "run now".
NOT_MANUALLY_RUNNABLE = {"send_release_notifications", "send_daily_digest"}

# Seasons and episodes are parts of a tracked show, not items of their own.
COUNTED_MEDIA_TYPES = [
    media_type
    for media_type in MediaTypes.values
    if media_type not in (MediaTypes.SEASON.value, MediaTypes.EPISODE.value)
]

RESULT_PREVIEW_LENGTH = 160
CELERY_PING_TIMEOUT = 1.0


def instance_admin_required(view):
    """Refuse the view to everyone but instance admins."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_instance_admin:
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return wrapper


def get_user_rows():
    """Return every account with its tracked-item count, oldest first."""
    item_counts = {}
    for media_type in COUNTED_MEDIA_TYPES:
        model = apps.get_model("app", media_type)
        per_user = (
            model.objects.order_by().values("user_id").annotate(total=Count("id"))
        )
        for row in per_user:
            item_counts[row["user_id"]] = (
                item_counts.get(row["user_id"], 0) + row["total"]
            )

    users = list(User.objects.order_by("id"))
    for user in users:
        user.item_count = item_counts.get(user.id, 0)
    return users


def _format_interval(seconds):
    """Describe a repeat interval in the largest unit that divides it."""
    seconds = int(seconds)
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= size and seconds % size == 0:
            count = seconds // size
            return f"Every {count} {unit}s" if count > 1 else f"Every {unit}"
    return f"Every {seconds} seconds"


def _describe_schedule(schedule):
    """Return a short human description of a Celery schedule."""
    if isinstance(schedule, int | float):
        return _format_interval(schedule)
    run_every = getattr(schedule, "run_every", None)
    if run_every is not None:
        return _format_interval(run_every.total_seconds())
    hour = getattr(schedule, "_orig_hour", None)
    minute = getattr(schedule, "_orig_minute", None)
    if str(hour).isdigit() and str(minute).isdigit():
        return f"Daily at {int(hour):02d}:{int(minute):02d}"
    return str(schedule)


def _result_preview(task_result):
    """Return a short, readable version of a stored task result."""
    if task_result.status == "FAILURE":
        try:
            detail = json.loads(task_result.result or "{}")
            message = detail.get("exc_message") or detail.get("exc_type") or ""
            if isinstance(message, list):
                message = " ".join(str(part) for part in message)
        except (json.JSONDecodeError, AttributeError):
            message = task_result.result or ""
    else:
        try:
            message = json.loads(task_result.result or "null")
        except json.JSONDecodeError:
            message = task_result.result
    if message is None:
        return ""
    message = str(message)
    if len(message) > RESULT_PREVIEW_LENGTH:
        message = message[: RESULT_PREVIEW_LENGTH - 1] + "…"
    return message


def get_scheduled_tasks():
    """Return last run, next run and last result for each beat task."""
    periodic_tasks = {
        task.name: task
        for task in PeriodicTask.objects.filter(
            name__in=settings.CELERY_BEAT_SCHEDULE.keys(),
        ).select_related("interval", "crontab")
    }
    now = timezone.now()

    rows = []
    for key, entry in settings.CELERY_BEAT_SCHEDULE.items():
        task_name = entry["task"]
        periodic_task = periodic_tasks.get(key)
        last_result = (
            TaskResult.objects.filter(task_name=task_name)
            .order_by("-date_done")
            .first()
        )

        last_run = periodic_task.last_run_at if periodic_task else None
        if last_result and (last_run is None or last_result.date_done > last_run):
            last_run = last_result.date_done

        next_run = None
        if periodic_task is not None and periodic_task.enabled:
            try:
                is_due = periodic_task.schedule.is_due(
                    periodic_task.last_run_at or now,
                )
                next_run = now + timedelta(seconds=max(is_due.next, 0))
            except Exception:  # noqa: BLE001 a display nicety, never worth a 500
                next_run = None

        rows.append(
            {
                "key": key,
                "name": task_name,
                "frequency": _describe_schedule(entry["schedule"]),
                "last_run": last_run,
                "next_run": next_run,
                "status": last_result.status if last_result else None,
                "result": _result_preview(last_result) if last_result else "",
                "runnable": key not in NOT_MANUALLY_RUNNABLE,
            },
        )
    # tasks without a "run now" go last, so the buttons line up at the top
    rows.sort(key=lambda row: not row["runnable"])
    return rows


def ping_celery_workers():
    """Return the names of the Celery workers that answer a ping."""
    try:
        replies = current_app.control.ping(timeout=CELERY_PING_TIMEOUT, limit=1)
    except Exception:  # noqa: BLE001 any broker failure just means "not reachable"
        logger.warning("Celery worker ping failed", exc_info=True)
        return []
    return [name for reply in replies or [] for name in reply]


def _database_size():
    """Return the database size in bytes, or None when it can't be read."""
    try:
        if connection.vendor == "sqlite":
            db_path = Path(connection.settings_dict["NAME"])
            return sum(
                path.stat().st_size
                for path in (db_path, db_path.with_name(db_path.name + "-wal"))
                if path.exists()
            )
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_database_size(current_database())")
                return cursor.fetchone()[0]
    except Exception:  # noqa: BLE001 a display nicety, never worth a 500
        logger.warning("Could not read the database size", exc_info=True)
    return None


def _redis_ok():
    """Return whether the cache answers a write and a read."""
    try:
        cache.set("admin_health_probe", "ok", 30)
        return cache.get("admin_health_probe") == "ok"
    except Exception:  # noqa: BLE001 any cache failure just means "not reachable"
        logger.warning("Redis health probe failed", exc_info=True)
        return False


def get_health():
    """Return the state of the services the app depends on."""
    workers = ping_celery_workers()
    return {
        "redis_ok": _redis_ok(),
        "celery_ok": bool(workers),
        "celery_workers": workers,
        "database_vendor": connection.vendor,
        "database_size": _database_size(),
        "version": settings.VERSION,
    }


@require_GET
@instance_admin_required
def instance_admin(request):
    """Render the Admin page."""
    image_cache_size_bytes, image_cache_file_count = get_image_cache_stats()
    image_cache_max_bytes = settings.IMAGE_CACHE_MAX_BYTES
    context = {
        "user_rows": get_user_rows(),
        "temporary_password": request.session.pop(TEMP_PASSWORD_SESSION_KEY, None),
        "registration_open": settings.REGISTRATION,
        "image_cache_size_bytes": image_cache_size_bytes,
        "image_cache_file_count": image_cache_file_count,
        "image_cache_max_bytes": image_cache_max_bytes,
        "image_cache_percent": (
            min(100, round(image_cache_size_bytes * 100 / image_cache_max_bytes))
            if image_cache_max_bytes
            else 0
        ),
        "item_count": Item.objects.count(),
        "prunable_item_count": count_prunable_items(),
        "scheduled_tasks": get_scheduled_tasks(),
        "health": get_health(),
    }
    return render(request, "users/instance_admin.html", context)


def _set_active(request, target, *, active):
    """Activate or deactivate an account."""
    target.is_active = active
    target.save(update_fields=["is_active"])
    state = "reactivated" if active else "deactivated"
    messages.success(request, f"{target.username} was {state}.")
    logger.info("%s %s account %s", request.user.username, state, target.username)


def _set_admin(request, target, *, admin):
    """Promote an account to instance admin, or take that away."""
    if admin:
        if target.is_demo:
            messages.error(request, "A demo account cannot be an admin.")
            return
        target.is_staff = True
        target.save(update_fields=["is_staff"])
        messages.success(request, f"{target.username} is now an admin.")
    else:
        target.is_staff = False
        target.is_superuser = False
        target.save(update_fields=["is_staff", "is_superuser"])
        messages.success(request, f"{target.username} is no longer an admin.")
    logger.info(
        "%s set admin=%s on account %s",
        request.user.username,
        admin,
        target.username,
    )


def _reset_password(request, target):
    """Give an account a temporary password it must replace at next login."""
    if target.is_demo:
        messages.error(request, "The demo account's password cannot be reset.")
        return
    temporary_password = secrets.token_urlsafe(TEMP_PASSWORD_BYTES)
    # set_password also invalidates the account's existing sessions
    target.set_password(temporary_password)
    target.must_change_password = True
    target.save(update_fields=["password", "must_change_password"])
    request.session[TEMP_PASSWORD_SESSION_KEY] = {
        "username": target.username,
        "password": temporary_password,
    }
    logger.info(
        "%s issued a temporary password for %s",
        request.user.username,
        target.username,
    )


@require_POST
@instance_admin_required
def instance_admin_user_action(request, user_id):
    """Apply one account action from the Admin page's user list."""
    target = get_object_or_404(User, id=user_id)
    action = request.POST.get("user_action")

    if target.pk == request.user.pk:
        # Also what guarantees the instance always keeps at least one admin.
        messages.error(
            request,
            "You can't change your own account here. Use Settings → Account, "
            "or ask another admin.",
        )
    elif action == "deactivate":
        _set_active(request, target, active=False)
    elif action == "activate":
        _set_active(request, target, active=True)
    elif action == "promote":
        _set_admin(request, target, admin=True)
    elif action == "demote":
        _set_admin(request, target, admin=False)
    elif action == "reset_password":
        _reset_password(request, target)
    else:
        messages.error(request, "Unknown action.")

    return redirect("instance_admin")


@require_POST
@instance_admin_required
def instance_admin_run_task(request):
    """Queue one of the scheduled tasks to run now."""
    key = request.POST.get("task")
    entry = settings.CELERY_BEAT_SCHEDULE.get(key)
    if entry is None or key in NOT_MANUALLY_RUNNABLE:
        messages.error(request, "That task can't be run from here.")
        return redirect("instance_admin")

    # Only names from the beat schedule get this far, never arbitrary tasks.
    current_app.tasks[entry["task"]].delay()
    messages.success(
        request,
        f"“{entry['task']}” started. Its result shows here when it finishes.",
    )
    logger.info("%s ran scheduled task %s", request.user.username, entry["task"])
    return redirect("instance_admin")


@require_http_methods(["GET", "POST"])
def change_temporary_password(request):
    """Make an account replace the temporary password an admin gave it."""
    if not request.user.must_change_password:
        return redirect("home")

    form = SetPasswordForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save(commit=False)
        user.must_change_password = False
        user.save(update_fields=["password", "must_change_password"])
        update_session_auth_hash(request, user)
        messages.success(request, "Your password has been updated.")
        return redirect("home")

    return render(request, "users/change_temporary_password.html", {"form": form})
