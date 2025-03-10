import logging
import secrets

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.db import IntegrityError
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from django_celery_beat.models import PeriodicTask

import app
from users.forms import (
    PasswordChangeForm,
    UserUpdateForm,
)

logger = logging.getLogger(__name__)


@require_http_methods(["GET", "POST"])
def account(request):
    """Update the user's account and import/export data."""
    user_form = UserUpdateForm(instance=request.user)
    password_form = PasswordChangeForm(user=request.user)

    if request.method == "POST":
        # Handle username update
        if "username" in request.POST:
            user_form = UserUpdateForm(request.POST, instance=request.user)

            if user_form.is_valid():
                user_form.save()
                messages.success(request, "Your username has been updated!")
                logger.info(
                    "Successful username change for user: %s",
                    request.user.username,
                )
                return redirect("account")
            logger.warning(
                "Failed username change for user: %s - %s",
                request.user.username,
                list(user_form.errors.keys()),
            )

        # Handle password update
        elif any(
            key in request.POST
            for key in ["old_password", "new_password1", "new_password2"]
        ):
            password_form = PasswordChangeForm(user=request.user, data=request.POST)

            if password_form.is_valid():
                user = password_form.save()
                update_session_auth_hash(
                    request,
                    user,
                )
                messages.success(request, "Your password has been updated!")
                logger.info(
                    "Successful password change for user: %s",
                    request.user.username,
                )
                return redirect("account")
            logger.warning(
                "Failed password change for user: %s - %s",
                request.user.username,
                list(password_form.errors.keys()),
            )

    context = {
        "user_form": user_form,
        "password_form": password_form,
    }

    return render(request, "users/account.html", context)


@require_http_methods(["GET", "POST"])
def sidebar(request):
    """Render the sidebar settings page."""
    media_types = app.models.MediaTypes.values
    media_types.remove("episode")

    if request.method != "POST":
        return render(request, "users/sidebar.html", {"media_types": media_types})

    # Prevent demo users from updating preferences
    if request.user.is_demo:
        messages.error(request, "This section is view-only for demo accounts.")
        return redirect("sidebar")

    # Process form submission
    request.user.hide_from_search = "hide_disabled" in request.POST
    media_types_checked = request.POST.getlist("media_types_checkboxes")

    # Update user preferences for each media type
    for media_type in media_types:
        setattr(
            request.user,
            f"{media_type}_enabled",
            media_type in media_types_checked,
        )

    # Save changes and redirect
    request.user.save()
    messages.success(request, "Settings updated.")

    return redirect("sidebar")


@require_GET
def integrations(request):
    """Render the integrations settings page."""
    return render(request, "users/integrations.html")


@require_GET
def import_data(request):
    """Render the import data settings page."""
    import_tasks = request.user.get_import_tasks()
    return render(request, "users/import_data.html", {"import_tasks": import_tasks})


@require_GET
def export_data(request):
    """Render the export data settings page."""
    return render(request, "users/export_data.html")


@require_POST
def delete_import_schedule(request):
    """Delete an import schedule."""
    task_name = request.POST.get("task_name")
    try:
        task = PeriodicTask.objects.get(
            name=task_name,
            kwargs__contains=f'"user_id": {request.user.id}',
        )
        task.delete()
        messages.success(request, "Import schedule deleted.")
    except PeriodicTask.DoesNotExist:
        messages.error(request, "Import schedule not found.")
    return redirect("import_data")


@require_POST
def regenerate_token(request):
    """Regenerate the token for the user."""
    while True:
        try:
            request.user.token = secrets.token_urlsafe(24)
            request.user.save(update_fields=["token"])
            messages.success(request, "Token regenerated successfully.")
            break
        except IntegrityError:
            continue
    return redirect("integrations")
