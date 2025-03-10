from allauth.account.forms import SignupForm
from django import forms
from django.contrib.auth.forms import (
    PasswordChangeForm,
)

from .models import User


class CustomSignupForm(SignupForm):
    """Custom signup form for django-allauth."""

    def __init__(self, *args, **kwargs):
        """Remove email field and change password2 label."""
        super().__init__(*args, **kwargs)

        # Remove email field
        if "email" in self.fields:
            del self.fields["email"]

        # Change label and placeholder for password2 field
        if "password2" in self.fields:
            self.fields["password2"].label = "Confirm Password"
            self.fields["password2"].widget.attrs["placeholder"] = (
                "Confirm your password"
            )


class UserUpdateForm(forms.ModelForm):
    """Custom form for updating username."""

    def clean(self):
        """Check if the user is demo before changing the password."""
        cleaned_data = super().clean()
        if self.instance.is_demo:
            msg = "Changing the username is not allowed for the demo account."
            self.add_error("username", msg)
        return cleaned_data

    def __init__(self, *args, **kwargs):
        """Add crispy form helper to add submit button."""
        super().__init__(*args, **kwargs)
        self.fields["username"].help_text = None

    class Meta:
        """Only allow updating username."""

        model = User
        fields = ["username"]


class PasswordChangeForm(PasswordChangeForm):
    """Custom form for changing password."""

    def clean(self):
        """Check if the user is demo before changing the password."""
        cleaned_data = super().clean()
        if self.user.is_demo:
            msg = "Changing the password is not allowed for the demo account."
            self.add_error("new_password2", msg)
        return cleaned_data

    def __init__(self, *args, **kwargs):
        """Remove autofocus from password change form."""
        super().__init__(*args, **kwargs)
        self.fields["old_password"].widget.attrs.pop("autofocus", None)
        self.fields["new_password1"].help_text = None
