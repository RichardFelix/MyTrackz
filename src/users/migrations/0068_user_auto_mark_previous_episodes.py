from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0067_user_show_all_home_items"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="auto_mark_previous_episodes",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Mark earlier TV episodes watched when tracking a later episode"
                ),
            ),
        ),
    ]
