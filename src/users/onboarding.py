"""The setup walkthrough shown to a new account, at /welcome.

Every step saves when it is submitted, so leaving halfway loses nothing and
the summary can simply show what is stored. The same pages stay reachable
afterwards (Settings links here), so there is no second copy of these forms.
"""

from dataclasses import dataclass

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from app import provider_keys
from app.models import MediaTypes
from app.templatetags.app_tags import media_type_readable_plural
from users.models import (
    THEME_GROUND_COLORS,
    THEME_SWATCHES,
    DateFormatChoices,
    HomeLayoutChoices,
    PlanningTermChoices,
    ThemeChoices,
    TimeFormatChoices,
    theme_color_scheme,
    theme_html_classes,
)

WELCOME = "welcome"
PROVIDERS = "providers"
MEDIA_TYPES = "media-types"
SUMMARY = "summary"

# Seasons follow TV and episodes are never listed on their own.
MEDIA_TYPE_CHOICES = [
    media_type
    for media_type in MediaTypes.values
    if media_type not in (MediaTypes.SEASON.value, MediaTypes.EPISODE.value)
]

ON = "on"
OFF = "off"


@dataclass(frozen=True)
class Option:
    """One answer to a preference."""

    value: str
    label: str
    description: str = ""
    html_classes: str = ""
    swatches: tuple = ()
    color_scheme: str = ""
    ground_color: str = ""


@dataclass(frozen=True)
class Setting:
    """One user preference field and the answers offered for it."""

    field: str
    label: str
    options: tuple
    boolean: bool = False

    def current(self, user):
        """Return the option value matching what the user has stored."""
        value = getattr(user, self.field)
        if self.boolean:
            return ON if value else OFF
        return value

    def current_label(self, user):
        """Return the label of the stored answer."""
        current = self.current(user)
        return next(
            (option.label for option in self.options if option.value == current),
            current,
        )


@dataclass(frozen=True)
class PreferenceStep:
    """One preference screen: a question, a one-line explanation, the answers."""

    slug: str
    title: str
    explanation: str
    settings: tuple = ()


def _options(
    choices,
    descriptions=None,
    html_classes=None,
    swatches=None,
    color_scheme=None,
    ground_colors=None,
):
    descriptions = descriptions or {}
    return tuple(
        Option(
            value,
            label,
            descriptions.get(value, ""),
            html_classes(value) if html_classes else "",
            (swatches or {}).get(value, ()),
            color_scheme(value) if color_scheme else "",
            (ground_colors or {}).get(value, ""),
        )
        for value, label in choices
    )


PREFERENCE_STEPS = (
    PreferenceStep(
        slug=MEDIA_TYPES,
        title="What do you want to track?",
        explanation=(
            "Anything you turn off disappears from the sidebar, Home and search."
        ),
    ),
    PreferenceStep(
        slug="theme",
        title="Pick a look",
        explanation="The colour of the app's background and cards.",
        settings=(
            Setting(
                "theme",
                "Theme",
                _options(
                    ThemeChoices.choices,
                    {
                        ThemeChoices.DARK: "Black background, strongest contrast.",
                        ThemeChoices.GREY: "Softer charcoal surfaces.",
                        ThemeChoices.MIDNIGHT: (
                            "Deep navy with an amber accent and its own fonts."
                        ),
                        ThemeChoices.BEACHFRONT: (
                            "Sand ground, lavender cards, olive accent."
                        ),
                        ThemeChoices.PUMPKIN_SPICE: (
                            "Burnt orange ground with cinnamon and espresso."
                        ),
                        ThemeChoices.TROPICAL_PUNCH: (
                            "Orange ground, pink cards, teal accent."
                        ),
                        ThemeChoices.WATERMELON: (
                            "Watermelon pink ground, mint cards, charcoal accent."
                        ),
                        ThemeChoices.COTTON_CANDY: (
                            "Lilac ground, sky-blue cards, candy-pink controls."
                        ),
                        ThemeChoices.PASTEL_GARDEN: (
                            "Rose ground, blush cards, sage and cocoa."
                        ),
                        ThemeChoices.LUSH_FOREST: (
                            "Forest green with mint highlights."
                        ),
                    },
                    html_classes=theme_html_classes,
                    swatches=THEME_SWATCHES,
                    color_scheme=theme_color_scheme,
                    ground_colors=THEME_GROUND_COLORS,
                ),
            ),
        ),
    ),
    PreferenceStep(
        slug="home-layout",
        title="How should Home show your media?",
        explanation="Home lists what you are in the middle of, one row per type.",
        settings=(
            Setting(
                "home_layout",
                "Home layout",
                (
                    Option(
                        HomeLayoutChoices.GRID,
                        "Cards",
                        "Large posters with progress buttons on each.",
                    ),
                    Option(
                        HomeLayoutChoices.LIST,
                        "Compact list",
                        "Small poster rows; more fits on a phone screen.",
                    ),
                ),
            ),
        ),
    ),
    PreferenceStep(
        slug="planning-term",
        title="What do you call things saved for later?",
        explanation="This name is used for the planning status everywhere.",
        settings=(
            Setting(
                "planning_term",
                "Saved for later",
                _options(PlanningTermChoices.choices),
            ),
        ),
    ),
    PreferenceStep(
        slug="aired-only",
        title="Hide shows you are caught up on?",
        explanation=(
            "Applies to TV and anime on Home. You can also flip it from Home."
        ),
        settings=(
            Setting(
                "home_aired_only",
                "Caught-up shows",
                (
                    Option(
                        OFF,
                        "Keep them on Home",
                        "Every show in progress stays visible.",
                    ),
                    Option(
                        ON,
                        "Hide until the next episode airs",
                        "Home only lists what you can watch right now.",
                    ),
                ),
                boolean=True,
            ),
        ),
    ),
    PreferenceStep(
        slug="dates",
        title="How should dates and times look?",
        explanation="Used on the calendar, history and release dates.",
        settings=(
            Setting("date_format", "Date", _options(DateFormatChoices.choices)),
            Setting("time_format", "Time", _options(TimeFormatChoices.choices)),
        ),
    ),
)
PREFERENCE_STEPS_BY_SLUG = {step.slug: step for step in PREFERENCE_STEPS}


def get_steps(user):
    """Return the step slugs this account goes through, in order."""
    steps = [WELCOME]
    if user.is_instance_admin:
        # keys are instance-wide, so only an admin is asked for them
        steps.append(PROVIDERS)
    steps.extend(step.slug for step in PREFERENCE_STEPS)
    steps.append(SUMMARY)
    return steps


def _step_url(step, origin=""):
    url = reverse("onboarding_step", args=[step])
    return f"{url}?from={origin}" if origin else url


def _origin(request):
    """Return where a step was opened from, when not walking straight through."""
    origin = request.GET.get("from", "")
    if origin == "admin" and not request.user.is_instance_admin:
        return ""
    return origin if origin in (SUMMARY, "admin") else ""


def _after(request, step):
    """Redirect to wherever a saved step leads."""
    origin = _origin(request)
    if origin == SUMMARY:
        return redirect(_step_url(SUMMARY))
    if origin == "admin":
        return redirect("instance_admin")
    steps = get_steps(request.user)
    return redirect(_step_url(steps[steps.index(step) + 1]))


def _base_context(request, step):
    steps = get_steps(request.user)
    index = steps.index(step)
    origin = _origin(request)
    if origin == SUMMARY:
        back_url = _step_url(SUMMARY)
    elif origin == "admin":
        back_url = reverse("instance_admin")
    elif index:
        back_url = _step_url(steps[index - 1])
    else:
        back_url = ""
    return {
        "step": step,
        "step_number": index + 1,
        "step_count": len(steps),
        "progress_percent": round((index + 1) * 100 / len(steps)),
        "back_url": back_url,
        "origin": origin,
        "submit_label": "Save" if origin else "Continue",
    }


def _summary_preferences(user):
    """Return ``(step, value shown)`` rows for the summary."""
    rows = []
    for step in PREFERENCE_STEPS:
        if step.slug == MEDIA_TYPES:
            enabled = [
                media_type_readable_plural(media_type)
                for media_type in MEDIA_TYPE_CHOICES
                if getattr(user, f"{media_type}_enabled")
            ]
            value = ", ".join(enabled) or "Nothing selected"
        else:
            value = " · ".join(setting.current_label(user) for setting in step.settings)
        rows.append({"step": step, "value": value})
    return rows


def _save_media_types(request):
    user = request.user
    checked = request.POST.getlist("media_types")
    tv_was_enabled = user.tv_enabled
    for media_type in MEDIA_TYPE_CHOICES:
        setattr(user, f"{media_type}_enabled", media_type in checked)
    if user.tv_enabled != tv_was_enabled:
        user.season_enabled = user.tv_enabled
    user.save()


def _save_settings(request, step):
    user = request.user
    fields = []
    for setting in step.settings:
        value = request.POST.get(setting.field)
        if value not in [option.value for option in setting.options]:
            continue
        setattr(user, setting.field, value == ON if setting.boolean else value)
        fields.append(setting.field)
    if fields:
        user.save(update_fields=fields)


def _save_providers(request):
    cleared = request.POST.getlist("clear")
    for provider in provider_keys.PROVIDERS:
        if provider.slug in cleared:
            provider_keys.clear(provider)
        else:
            provider_keys.save(provider, request.POST)


def _render_step(request, step):
    context = _base_context(request, step)
    user = request.user
    if step == WELCOME:
        template = "users/onboarding/welcome.html"
        context["asks_for_keys"] = user.is_instance_admin
        context["preference_count"] = len(PREFERENCE_STEPS)
    elif step == PROVIDERS:
        template = "users/onboarding/providers.html"
        context["providers"] = [
            provider_keys.status(provider) for provider in provider_keys.PROVIDERS
        ]
    elif step == SUMMARY:
        template = "users/onboarding/summary.html"
        context["preferences"] = _summary_preferences(user)
        if user.is_instance_admin:
            context["providers"] = [
                provider_keys.status(provider) for provider in provider_keys.PROVIDERS
            ]
    else:
        template = "users/onboarding/preference.html"
        preference = PREFERENCE_STEPS_BY_SLUG[step]
        context["preference"] = preference
        if step == MEDIA_TYPES:
            context["media_types"] = [
                {
                    "value": media_type,
                    "enabled": getattr(user, f"{media_type}_enabled"),
                }
                for media_type in MEDIA_TYPE_CHOICES
            ]
        else:
            context["settings"] = [
                {"setting": setting, "current": setting.current(user)}
                for setting in preference.settings
            ]
    return render(request, template, context)


@require_GET
def onboarding(request):  # noqa: ARG001
    """Open the walkthrough at its first step."""
    return redirect(_step_url(WELCOME))


@require_http_methods(["GET", "POST"])
def onboarding_step(request, step):
    """Show one step, or save it and move on."""
    if request.user.is_demo:
        return redirect("home")
    if step not in get_steps(request.user):
        raise Http404

    if request.method == "GET":
        return _render_step(request, step)

    if step == PROVIDERS:
        _save_providers(request)
    elif step == MEDIA_TYPES:
        _save_media_types(request)
    elif step in PREFERENCE_STEPS_BY_SLUG:
        _save_settings(request, PREFERENCE_STEPS_BY_SLUG[step])
    elif step == SUMMARY:
        return _leave(
            request,
            "You're all set. Anything here can be changed later in Settings.",
        )
    return _after(request, step)


def _leave(request, message):
    """Mark the walkthrough as dealt with and drop the user into the app."""
    if request.user.needs_onboarding:
        request.user.needs_onboarding = False
        request.user.save(update_fields=["needs_onboarding"])
    messages.success(request, message)
    return redirect("home")


@require_POST
def onboarding_skip(request):
    """Leave the walkthrough without finishing it."""
    return _leave(
        request,
        "Setup skipped. You can run it any time from Settings → Preferences.",
    )


@require_POST
def onboarding_test_provider(request, slug):
    """Try a provider's keys as typed, before they are saved."""
    if not request.user.is_instance_admin:
        raise PermissionDenied
    provider = provider_keys.PROVIDERS_BY_SLUG.get(slug)
    if provider is None:
        raise Http404
    ok, message = provider_keys.test_connection(provider, request.POST)
    return render(
        request,
        "users/onboarding/provider_test_result.html",
        {"ok": ok, "message": message},
    )
