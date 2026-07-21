from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0063_user_home_layout"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="home_aired_only",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Hide caught-up episodic shows from the home screen until "
                    "their next episode airs."
                ),
            ),
        ),
    ]
