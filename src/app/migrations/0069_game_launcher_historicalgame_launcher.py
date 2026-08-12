from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("app", "0068_alter_anime_status_alter_basicmedia_status_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="game",
            name="launcher",
            field=models.CharField(
                choices=[
                    ("Steam", "Steam"),
                    ("GOG", "GOG"),
                    ("Epic", "Epic"),
                    ("EA", "EA"),
                    ("Ubisoft", "Ubisoft"),
                    ("Blizzard", "Blizzard"),
                    ("Xbox", "Xbox"),
                    ("Emulation", "Emulation"),
                    ("Rockstar", "Rockstar"),
                    ("Amazon", "Amazon"),
                    ("Other", "Other"),
                ],
                default="Steam",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="historicalgame",
            name="launcher",
            field=models.CharField(
                choices=[
                    ("Steam", "Steam"),
                    ("GOG", "GOG"),
                    ("Epic", "Epic"),
                    ("EA", "EA"),
                    ("Ubisoft", "Ubisoft"),
                    ("Blizzard", "Blizzard"),
                    ("Xbox", "Xbox"),
                    ("Emulation", "Emulation"),
                    ("Rockstar", "Rockstar"),
                    ("Amazon", "Amazon"),
                    ("Other", "Other"),
                ],
                default="Steam",
                max_length=10,
            ),
        ),
    ]
