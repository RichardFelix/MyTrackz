from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0066_user_planning_term"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="show_all_home_items",
            field=models.BooleanField(
                default=True,
                help_text=(
                    "Show every item in each home screen row without a load button."
                ),
            ),
        ),
    ]
