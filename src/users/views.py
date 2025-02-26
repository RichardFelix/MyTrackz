import logging
import secrets

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_not_required
from django.contrib.auth.views import LoginView
from django.db import IntegrityError
from django.shortcuts import redirect, render
from django.utils.decorators import method_decorator
from django.views.decorators.http import require_http_methods, require_POST
from django_celery_beat.models import PeriodicTask

import app
from users import helpers
from users.forms import (
    PasswordChangeForm,
    UserLoginForm,
    UserRegisterForm,
    UserUpdateForm,
)

logger = logging.getLogger(__name__)


@login_not_required
@require_http_methods(["GET", "POST"])
def register(request):
    """Register a new user."""
    form = UserRegisterForm(request.POST or None)

    if form.is_valid():
        form.save()
        messages.success(request, "Your account has been created, you can now log in!")
        logger.info(
            "New user registered: %s at %s",
            form.cleaned_data.get("username"),
            helpers.get_client_ip(request),
        )
        return redirect("login")

    return render(request, "users/register.html", {"form": form})


@method_decorator(login_not_required, name="dispatch")
class CustomLoginView(LoginView):
    """Custom login view with logging."""

    form_class = UserLoginForm
    template_name = "users/login.html"
    http_method_names = ["get", "post"]

    def form_valid(self, form):
        """Log the user in."""
        logger.info(
            "User logged in as: %s at %s",
            self.request.POST["username"],
            helpers.get_client_ip(self.request),
        )
        return super().form_valid(form)

    def form_invalid(self, form):
        """Log the failed login attempt."""
        logger.error(
            "Failed login attempt for: %s at %s",
            self.request.POST["username"],
            helpers.get_client_ip(self.request),
        )
        return super().form_invalid(form)


@require_http_methods(["GET", "POST"])
def account(request):
    """Update the user's account and import/export data."""
    if request.method == "POST":
        if "username" in request.POST:
            user_form = UserUpdateForm(request.POST, instance=request.user)

            if user_form.is_valid():
                user_form.save()
                messages.success(request, "Your username has been updated!")
                logger.info("Successful username change to %s", request.user.username)
            else:
                logger.error(
                    "Failed username change for %s: %s",
                    request.user.username,
                    user_form.errors.as_json(),
                )
                for errors in user_form.errors.values():
                    for error in errors:
                        messages.error(request, error)

        elif "old_password" in request.POST:
            password_form = PasswordChangeForm(request.user, request.POST)
            if password_form.is_valid():
                password = password_form.save()
                update_session_auth_hash(request, password)
                messages.success(request, "Your password has been updated!")
                logger.info(
                    "Successful password change for: %s",
                    request.user.username,
                )
            else:
                logger.error(
                    "Failed password change for %s: %s",
                    request.user.username,
                    password_form.errors.as_json(),
                )
                for errors in password_form.errors.values():
                    for error in errors:
                        messages.error(request, error)

        return redirect("account")

    return render(request, "users/account.html")


@require_http_methods(["GET", "POST"])
def sidebar(request):
    """Render the sidebar settings page."""
    media_types = app.models.Item.MediaTypes.values
    media_types.remove("episode")

    if request.method == "POST":
        request.user.hide_from_search = "hide_disabled" in request.POST
        media_types_checked = request.POST.getlist("media_types_checkboxes")

        for media_type in media_types:
            setattr(
                request.user,
                f"{media_type}_enabled",
                media_type in media_types_checked,
            )
        request.user.save()

        messages.success(request, "Settings updated.")
        return redirect("sidebar")
    return render(request, "users/sidebar.html", {"media_types": media_types})


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
    return redirect("profile")


@require_POST
def regenerate_token(request):
    """Regenerate the token for the user."""
    while True:
        try:
            request.user.token = secrets.token_urlsafe(24)
            request.user.save(update_fields=["token"])
            break
        except IntegrityError:
            continue
    return redirect("profile")
