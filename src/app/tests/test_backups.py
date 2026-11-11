import gzip
import json
import sqlite3
import tempfile
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings

from app import backups
from app.models import Item, MediaTypes, Movie, Sources, Status
from app.tasks import back_up_database

NOW = datetime(2026, 11, 11, 3, 0, tzinfo=UTC)
WEBHOOK = "discord://1234567890/very-secret-token"


def name_at(days_ago, suffix=backups.DATABASE_SUFFIX):
    """Return the name of a backup taken ``days_ago`` days before NOW."""
    return backups.backup_name(NOW - timedelta(days=days_ago), suffix)


class NamingAndRetentionTests(SimpleTestCase):
    """File names carry the time; retention only ever touches those names."""

    def test_name_round_trip(self):
        """A generated name parses back to its time and kind."""
        name = backups.backup_name(NOW, backups.DATABASE_SUFFIX)

        self.assertEqual(name, "mytrackz-20261111T030000Z.sqlite3.gz")
        self.assertEqual(
            backups.parse_backup_name(name),
            (NOW, backups.DATABASE_SUFFIX),
        )

    def test_foreign_names_are_not_backups(self):
        """Nothing but a finished backup file matches."""
        for name in (
            "db.sqlite3",
            "status.json",
            "mytrackz-20261111T030000Z.sqlite3.gz.part",
            "mytrackz-20261111T030000Z.sqlite3.gz.tmp",
            "mytrackz-20269999T030000Z.sqlite3.gz",
            "../mytrackz-20261111T030000Z.sqlite3.gz",
        ):
            self.assertIsNone(backups.parse_backup_name(name), name)

    def test_expired_names(self):
        """Only copies older than the retention are picked, of either kind."""
        names = [
            name_at(0),
            name_at(13),
            name_at(15),
            name_at(15, backups.EXPORTS_SUFFIX),
            "notes.txt",
        ]

        self.assertEqual(
            backups.expired_names(names, NOW, 14),
            sorted([name_at(15), name_at(15, backups.EXPORTS_SUFFIX)]),
        )

    def test_zero_retention_keeps_everything(self):
        """A retention of 0 never deletes."""
        self.assertEqual(backups.expired_names([name_at(900)], NOW, 0), [])


class BackupTestCase(TestCase):
    """A real SQLite file to back up and a temporary backup directory."""

    notification_urls = ()
    instance_name = ""

    def setUp(self):
        """Point the backup at a throwaway database file and directory."""
        cache.clear()
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.backup_dir = self.tmp / "backups"

        self.db_path = self.tmp / "live.sqlite3"
        live = sqlite3.connect(self.db_path)
        live.execute("PRAGMA journal_mode=WAL")
        live.execute("CREATE TABLE shows (title TEXT)")
        live.execute("INSERT INTO shows VALUES ('Severance')")
        live.commit()
        live.close()
        self.enterContext(
            patch.dict(connection.settings_dict, {"NAME": str(self.db_path)}),
        )
        self.enterContext(
            override_settings(
                BACKUP_ENABLED=True,
                BACKUP_DIR=self.backup_dir,
                BACKUP_RETENTION_DAYS=14,
                BACKUP_USER_EXPORTS=True,
                BACKUP_INSTANCE_NAME=self.instance_name,
                BACKUP_NOTIFICATION_URLS=list(self.notification_urls),
                BACKUP_NOTIFY_ON_SUCCESS=True,
            ),
        )

    def steps(self, report):
        """Return a report's step statuses by name."""
        return {step["name"]: step["status"] for step in report["steps"]}

    def old_copy(self, days_ago, suffix=backups.DATABASE_SUFFIX):
        """Leave a copy from ``days_ago`` days ago in the backup directory."""
        self.backup_dir.mkdir(exist_ok=True)
        path = self.backup_dir / backups.backup_name(
            datetime.now(UTC) - timedelta(days=days_ago),
            suffix,
        )
        path.write_bytes(b"old")
        return path


class BackupRunTests(BackupTestCase):
    """The copy, the exports and retention."""

    def test_copy_restores(self):
        """The compressed copy opens as a database holding the live rows."""
        report = backups.run_backup()

        self.assertTrue(report["ok"])
        database_file = self.backup_dir / report["files"][0]["name"]
        self.assertTrue(database_file.name.endswith(backups.DATABASE_SUFFIX))
        restored = self.tmp / "restored.sqlite3"
        restored.write_bytes(gzip.decompress(database_file.read_bytes()))
        rows = sqlite3.connect(restored).execute("SELECT title FROM shows").fetchall()
        self.assertEqual(rows, [("Severance",)])

    def test_files_are_private_and_nothing_is_left_over(self):
        """Backups are owner-only and no temporary file survives."""
        report = backups.run_backup()

        names = sorted(path.name for path in self.backup_dir.iterdir())
        self.assertEqual(
            names,
            sorted([entry["name"] for entry in report["files"]] + ["status.json"]),
        )
        for entry in report["files"]:
            mode = (self.backup_dir / entry["name"]).stat().st_mode & 0o777
            self.assertEqual(mode, 0o600)

    def test_user_exports(self):
        """Each account gets its own CSV in the exports archive."""
        user = get_user_model().objects.create_user(username="bob/../x")
        with patch("app.tasks.cache_item_image.delay"):
            item = Item.objects.create(
                media_id="1",
                source=Sources.MANUAL.value,
                media_type=MediaTypes.MOVIE.value,
                title="Heat",
                image="http://example.com/a.jpg",
            )
        Movie.objects.create(item=item, user=user, status=Status.PLANNING.value)

        report = backups.run_backup()

        self.assertEqual(self.steps(report)[backups.STEP_EXPORTS], backups.OK)
        archive_name = next(
            entry["name"]
            for entry in report["files"]
            if entry["name"].endswith(backups.EXPORTS_SUFFIX)
        )
        with zipfile.ZipFile(self.backup_dir / archive_name) as archive:
            self.assertEqual(archive.namelist(), [f"user-{user.id}-bob_.._x.csv"])
            self.assertIn("Heat", archive.read(archive.namelist()[0]).decode())

    @override_settings(BACKUP_USER_EXPORTS=False)
    def test_exports_can_be_turned_off(self):
        """Without exports a run writes the database copy only."""
        report = backups.run_backup()

        self.assertTrue(report["ok"])
        self.assertEqual(len(report["files"]), 1)
        self.assertEqual(self.steps(report)[backups.STEP_EXPORTS], backups.SKIPPED)

    def test_failed_exports_keep_the_database_copy(self):
        """A broken export fails the run but not the copy before it."""
        get_user_model().objects.create_user(username="bob")
        with patch(
            "integrations.exports.generate_rows",
            side_effect=RuntimeError("boom"),
        ):
            report = backups.run_backup()

        self.assertFalse(report["ok"])
        self.assertEqual(self.steps(report)[backups.STEP_DATABASE], backups.OK)
        self.assertEqual(self.steps(report)[backups.STEP_EXPORTS], backups.FAILED)
        self.assertEqual(len(backups.list_backups()), 1)

    def test_retention_removes_only_expired_backups(self):
        """Old copies go; recent ones and unrelated files stay."""
        expired = self.old_copy(15)
        expired_exports = self.old_copy(15, backups.EXPORTS_SUFFIX)
        recent = self.old_copy(13)
        stranger = self.backup_dir / "notes.txt"
        stranger.write_text("mine")

        report = backups.run_backup()

        self.assertTrue(report["ok"])
        self.assertFalse(expired.exists())
        self.assertFalse(expired_exports.exists())
        self.assertTrue(recent.exists())
        self.assertTrue(stranger.exists())
        self.assertEqual(report["steps"][-1]["detail"], "2 old removed, 2 kept")

    def test_failed_copy_deletes_nothing(self):
        """When the copy fails, expired backups are kept."""
        expired = self.old_copy(30)
        with patch("app.backups.copy_database", side_effect=OSError("disk full")):
            report = backups.run_backup()

        self.assertFalse(report["ok"])
        self.assertEqual(report["files"], [])
        self.assertEqual(
            self.steps(report),
            {
                backups.STEP_DATABASE: backups.FAILED,
                backups.STEP_EXPORTS: backups.SKIPPED,
                backups.STEP_RETENTION: backups.SKIPPED,
            },
        )
        self.assertTrue(expired.exists())

    def test_corrupt_copy_is_rejected(self):
        """A copy that fails its integrity check is not published."""
        with patch(
            "app.backups._integrity_verdict",
            return_value="row 3 missing from index",
        ):
            report = backups.run_backup()

        self.assertFalse(report["ok"])
        self.assertIn("integrity check", report["steps"][0]["detail"])
        self.assertEqual(backups.list_backups(), [])

    def test_other_databases_are_refused(self):
        """Anything but SQLite is reported as unsupported."""
        with patch.object(connection, "vendor", "postgresql"):
            report = backups.run_backup()

        self.assertFalse(report["ok"])
        self.assertIn("not supported", report["steps"][0]["detail"])

    def test_unfinished_files_are_swept(self):
        """Partial files from a killed run are removed by the next one."""
        self.backup_dir.mkdir()
        leftover = self.backup_dir / (name_at(1) + ".part")
        leftover.write_bytes(b"half")

        backups.run_backup()

        self.assertFalse(leftover.exists())

    def test_only_one_backup_runs_at_a_time(self):
        """A second run while one holds the lock does nothing."""
        cache.add(backups.LOCK_KEY, "1", 60)

        self.assertIsNone(backups.run_backup())
        self.assertFalse(self.backup_dir.exists())

    def test_status(self):
        """The Admin page status reflects the last run and the copies on disk."""
        self.assertIsNone(backups.get_status()["last"])

        report = backups.run_backup()
        status = backups.get_status()

        self.assertEqual(status["copy_count"], 1)
        self.assertEqual(status["total_size"], report["size"])
        self.assertTrue(status["last"]["ok"])
        self.assertTrue(status["supported"])
        self.assertFalse(status["running"])
        saved = json.loads((self.backup_dir / backups.STATUS_FILE).read_text())
        self.assertEqual(saved["steps"], report["steps"])


class NotificationTests(BackupTestCase):
    """One message per run, for both outcomes."""

    notification_urls = (WEBHOOK,)
    instance_name = "prod"

    def run_with_notifier(self, **patches):
        """Run a backup; return the report and the notify() keyword arguments."""
        with patch("app.backups.apprise.Apprise") as apprise_class:
            notifier = apprise_class.return_value
            notifier.notify.return_value = True
            for target, effect in patches.items():
                self.enterContext(patch(target, side_effect=effect))
            report = backups.run_backup()
        notifier.add.assert_called_once_with(WEBHOOK)
        notifier.notify.assert_called_once()
        return report, notifier.notify.call_args.kwargs

    def test_completed_message(self):
        """Success says what, how big, how long and how many copies exist."""
        report, message = self.run_with_notifier()

        self.assertTrue(report["ok"])
        self.assertEqual(message["title"], "MyTrackz backup completed (prod)")
        self.assertIn("SQLite database + CSV exports for 0 users", message["body"])
        self.assertIn("Size:", message["body"])
        self.assertIn(f"Took: {report['duration']} seconds", message["body"])
        self.assertIn("Copies on the server: 0 old removed, 1 kept", message["body"])
        self.assertNotIn("secret", message["title"] + message["body"])

    def test_failed_copy_message(self):
        """A failed copy names the database step and its error."""
        _, message = self.run_with_notifier(
            **{"app.backups.copy_database": OSError("disk full")},
        )

        self.assertEqual(message["title"], "MyTrackz backup FAILED (prod)")
        self.assertIn("Failed step: Database copy", message["body"])
        self.assertIn("disk full", message["body"])
        self.assertIn("No new backup was written", message["body"])

    def test_failed_later_step_message(self):
        """A failure after the copy says the copy was kept."""
        _, message = self.run_with_notifier(
            **{"app.backups.prune": OSError("read-only file system")},
        )

        self.assertIn("Failed step: Retention cleanup", message["body"])
        self.assertIn("database copy succeeded and was kept", message["body"])

    @override_settings(BACKUP_NOTIFY_ON_SUCCESS=False)
    def test_success_can_be_silenced(self):
        """With success messages off, only a failure is sent."""
        with patch("app.backups.apprise.Apprise") as apprise_class:
            backups.run_backup()
        apprise_class.assert_not_called()

        _, message = self.run_with_notifier(
            **{"app.backups.copy_database": OSError("disk full")},
        )
        self.assertIn("FAILED", message["title"])

    def test_broken_notification_does_not_fail_the_backup(self):
        """A notifier that raises is logged without its URL and ignored."""
        with (
            patch("app.backups.apprise.Apprise") as apprise_class,
            self.assertLogs("app.backups", level="ERROR") as logs,
        ):
            apprise_class.return_value.notify.side_effect = RuntimeError(
                f"could not reach {WEBHOOK}",
            )
            report = backups.run_backup()

        self.assertTrue(report["ok"])
        self.assertNotIn("very-secret-token", "\n".join(logs.output))

    @override_settings(BACKUP_NOTIFICATION_URLS=[])
    def test_no_urls_no_notification(self):
        """Without URLs nothing is sent."""
        with patch("app.backups.apprise.Apprise") as apprise_class:
            backups.run_backup()

        apprise_class.assert_not_called()


class BackupTaskTests(BackupTestCase):
    """The Celery task wrapping a run."""

    def test_success_result(self):
        """A good run returns a readable summary."""
        result = back_up_database()

        self.assertTrue(result.startswith("Backup completed: 2 file(s)"))

    def test_failed_run_raises(self):
        """A failed step fails the task, after the report is saved."""
        with (
            patch("app.backups.copy_database", side_effect=OSError("disk full")),
            self.assertRaisesMessage(backups.BackupError, "disk full"),
        ):
            back_up_database()

        self.assertFalse(backups.load_report()["ok"])

    @override_settings(BACKUP_ENABLED=False)
    def test_disabled(self):
        """Turned off, the task does nothing."""
        self.assertIn("turned off", back_up_database())
        self.assertFalse(self.backup_dir.exists())

    def test_other_databases_do_nothing(self):
        """On PostgreSQL the task reports it and neither runs nor fails."""
        with patch.object(connection, "vendor", "postgresql"):
            self.assertIn("only support SQLite", back_up_database())

        self.assertFalse(self.backup_dir.exists())

    def test_already_running(self):
        """A run that loses the lock reports that instead of failing."""
        cache.add(backups.LOCK_KEY, "1", 60)

        self.assertIn("already running", back_up_database())
