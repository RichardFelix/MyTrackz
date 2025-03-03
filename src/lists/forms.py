from django import forms
from django_select2 import forms as s2forms

from app.models import MediaTypes
from lists.models import CustomList


class CollaboratorsWidget(s2forms.ModelSelect2MultipleWidget):
    """Custom widget for selecting multiple users."""

    search_fields = ["username__icontains"]


class CustomListForm(forms.ModelForm):
    """Form for creating new custom lists."""

    class Meta:
        """Bind form to model."""

        model = CustomList
        fields = ["name", "description", "collaborators"]
        widgets = {
            "collaborators": CollaboratorsWidget(
                attrs={
                    "data-minimum-input-length": 1,
                    "data-placeholder": "Search users to add...",
                    "data-allow-clear": "false",
                },
            ),
        }


class FilterListItemsForm(forms.Form):
    """Form for filtering media on media list view."""

    media_type = forms.ChoiceField(
        choices=[("all", "All")]
        + [(choice.value, choice.label) for choice in MediaTypes],
        initial="all",
    )

    sort = forms.ChoiceField(
        choices=[
            ("title", "Title"),
            ("date_added", "Date Added"),
        ],
        initial="date_added",
    )
