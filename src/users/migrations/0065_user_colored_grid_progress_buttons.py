from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0064_user_home_aired_only"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="colored_grid_progress_buttons",
            field=models.BooleanField(
                default=True,
                help_text="Show red and green backgrounds on grid progress buttons.",
            ),
        ),
    ]
