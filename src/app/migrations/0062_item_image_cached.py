from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0061_episode_item_not_null"),
    ]

    operations = [
        migrations.AddField(
            model_name="item",
            name="image_cached",
            field=models.BooleanField(default=False),
        ),
    ]
