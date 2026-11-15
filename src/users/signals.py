from allauth.account.signals import user_signed_up
from django.dispatch import receiver


@receiver(user_signed_up)
def send_new_account_to_onboarding(request, user, **kwargs):  # noqa: ARG001
    """Flag an account that just signed up for the setup walkthrough."""
    if user.is_demo:
        return
    user.needs_onboarding = True
    user.save(update_fields=["needs_onboarding"])
