import csv
import io
from unittest.mock import patch

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from app.models import (
    TV,
    Anime,
    Episode,
    Game,
    GameLaunchers,
    Item,
    MediaTypes,
    Movie,
    Season,
    Sources,
    Status,
)
from integrations.exports import generate_rows
from integrations.imports import yamtrack

TRACK_FIELDS = (
    "status",
    "score",
    "progress",
    "launcher",
    "notes",
    "start_date",
    "end_date",
)


class ExportImportRoundTripTests(TestCase):
    """A Yamtrack CSV export must re-import losslessly.

    Guards the export format and the Yamtrack importer against drifting apart:
    every row and every user-editable field must survive an export -> import
    cycle into a fresh account.
    """

    def setUp(self):
        """Create source/destination users and tracked media across types."""
        exporter_credentials = {"username": "exporter", "password": "12345"}
        importer_credentials = {"username": "importer", "password": "12345"}
        self.exporter = get_user_model().objects.create_user(**exporter_credentials)
        self.importer = get_user_model().objects.create_user(**importer_credentials)

        episodes = [{"episode_number": number} for number in range(1, 4)]
        metadata_patcher = patch(
            "app.providers.services.get_media_metadata",
            return_value={
                "max_progress": 10,
                "related": {"seasons": []},
                "season/1": {"episodes": episodes},
                "season/2": {"episodes": episodes},
            },
        )
        metadata_patcher.start()
        self.addCleanup(metadata_patcher.stop)

        now = timezone.now()

        movie_item = Item.objects.create(
            media_id="550",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Fight Club",
            image="http://example.com/fc.jpg",
        )
        Movie.objects.create(
            item=movie_item,
            user=self.exporter,
            status=Status.COMPLETED.value,
            score=8.5,
            notes='Great, movie with "quotes" and, commas',
            start_date=now,
            end_date=now,
        )

        manual_item = Item.objects.create(
            media_id=Item.generate_manual_id(),
            source=Sources.MANUAL.value,
            media_type=MediaTypes.MOVIE.value,
            title="Home Video",
            image=settings.IMG_NONE,
        )
        Movie.objects.create(
            item=manual_item, user=self.exporter, status=Status.PLANNING.value
        )

        game_item = Item.objects.create(
            media_id="1020",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Some Game",
            image="http://example.com/g.jpg",
        )
        # 90 minutes: exported as "1h 30min" text, must parse back to 90.
        Game.objects.create(
            item=game_item,
            user=self.exporter,
            launcher=GameLaunchers.GOG,
            status=Status.IN_PROGRESS.value,
            progress=90,
        )

        anime_item = Item.objects.create(
            media_id="1",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Cowboy Bebop",
            image="http://example.com/cb.jpg",
        )
        Anime.objects.create(
            item=anime_item,
            user=self.exporter,
            status=Status.IN_PROGRESS.value,
            progress=5,
            score=9,
        )

        tv_item = Item.objects.create(
            media_id="1396",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Breaking Bad",
            image="http://example.com/bb.jpg",
        )
        tv = TV.objects.create(
            item=tv_item,
            user=self.exporter,
            status=Status.IN_PROGRESS.value,
            score=9.5,
        )
        for season_number in range(1, 3):
            season_item = Item.objects.create(
                media_id="1396",
                source=Sources.TMDB.value,
                media_type=MediaTypes.SEASON.value,
                season_number=season_number,
                title="Breaking Bad",
                image="http://example.com/bb.jpg",
            )
            season = Season.objects.create(
                item=season_item,
                user=self.exporter,
                related_tv=tv,
                status=Status.IN_PROGRESS.value,
            )
            for episode_number in range(1, 3):
                episode_item = Item.objects.create(
                    media_id="1396",
                    source=Sources.TMDB.value,
                    media_type=MediaTypes.EPISODE.value,
                    season_number=season_number,
                    episode_number=episode_number,
                    title="Breaking Bad",
                    image="http://example.com/bb.jpg",
                )
                Episode.objects.create(
                    item=episode_item, related_season=season, end_date=now
                )

    def _export_csv(self):
        return "".join(generate_rows(self.exporter)).encode()

    def _snapshot(self, user):
        """Return {identity key: tracked fields} for everything a user tracks."""
        snapshot = {}
        for media_type in MediaTypes.values:
            model = apps.get_model("app", media_type)
            filter_kwargs = (
                {"related_season__user": user}
                if media_type == MediaTypes.EPISODE.value
                else {"user": user}
            )
            for media in model.objects.filter(**filter_kwargs).select_related("item"):
                key = (
                    media_type,
                    media.item.media_id,
                    media.item.source,
                    media.item.season_number,
                    media.item.episode_number,
                )
                snapshot[key] = {
                    field: getattr(media, field)
                    for field in TRACK_FIELDS
                    if hasattr(media, field)
                }
                # Movies have no user-facing progress (MovieForm excludes it,
                # completion is status-based), so it isn't round-tripped.
                if media_type == MediaTypes.MOVIE.value:
                    snapshot[key].pop("progress", None)
        return snapshot

    def test_export_reimports_losslessly(self):
        """Every exported row and tracked field must survive a re-import."""
        counts, warnings = yamtrack.importer(
            io.BytesIO(self._export_csv()), self.importer, "new"
        )

        self.assertEqual(warnings, "")
        self.assertEqual(
            counts,
            {"tv": 1, "season": 2, "episode": 4, "movie": 2, "anime": 1, "game": 1},
        )
        self.assertEqual(self._snapshot(self.exporter), self._snapshot(self.importer))

        # Season/episode relations must point at the imported user's own rows.
        imported_tv = TV.objects.get(user=self.importer)
        self.assertEqual(imported_tv.seasons.count(), 2)
        for season in imported_tv.seasons.all():
            self.assertEqual(season.episodes.count(), 2)

    def test_reimport_in_new_mode_does_not_duplicate(self):
        """Importing the same export twice in "new" mode must be a no-op."""
        csv_bytes = self._export_csv()
        yamtrack.importer(io.BytesIO(csv_bytes), self.importer, "new")
        counts, _ = yamtrack.importer(io.BytesIO(csv_bytes), self.importer, "new")

        self.assertEqual(counts, {})
        self.assertEqual(Movie.objects.filter(user=self.importer).count(), 2)
        self.assertEqual(TV.objects.filter(user=self.importer).count(), 1)

    def test_legacy_export_without_launcher_defaults_games_to_steam(self):
        """CSV files created before launcher tracking remain importable."""
        rows = list(csv.reader(io.StringIO(self._export_csv().decode())))
        launcher_index = rows[0].index("launcher")
        for row in rows:
            row.pop(launcher_index)

        legacy_export = io.StringIO()
        csv.writer(legacy_export, quoting=csv.QUOTE_ALL).writerows(rows)

        counts, warnings = yamtrack.importer(
            io.BytesIO(legacy_export.getvalue().encode()),
            self.importer,
            "new",
        )

        self.assertEqual(warnings, "")
        self.assertEqual(counts[MediaTypes.GAME.value], 1)
        self.assertEqual(
            Game.objects.get(user=self.importer).launcher,
            GameLaunchers.STEAM,
        )

    def test_reimport_in_overwrite_mode_replaces_cleanly(self):
        """Overwrite mode must replace rows without duplicating or losing data."""
        csv_bytes = self._export_csv()
        yamtrack.importer(io.BytesIO(csv_bytes), self.importer, "new")
        yamtrack.importer(io.BytesIO(csv_bytes), self.importer, "overwrite")

        self.assertEqual(self._snapshot(self.exporter), self._snapshot(self.importer))
