"""Scheduled database backups: a daily copy on the server, retention, alerts.

One run walks three steps and records each one:

1. Database copy - the SQLite online backup API, which gives a consistent
   snapshot while the app keeps reading and writing (safe with WAL). The copy
   is integrity-checked before it is kept.
2. User exports - every account's library as CSV, zipped, so one user can be
   restored without the whole database.
3. Retention cleanup - copies past their retention are deleted.

A later step failing never undoes an earlier one. Retention only runs behind a
good new copy, so a broken backup can never age out the last good ones.

There is deliberately no off-site push: the backup folder sits on the server's
disk and travels with whatever backs that server up.

Backup files hold the whole database. Treat them as sensitive.
"""

import contextlib
import gzip
import json
import logging
import re
import shutil
import sqlite3
import time
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import apprise
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.template.defaultfilters import filesizeformat
from django.utils import timezone

logger = logging.getLogger(__name__)

TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"
DATABASE_SUFFIX = ".sqlite3.gz"
EXPORTS_SUFFIX = "-exports.zip"
# Only files matching this are ever listed, counted or deleted.
BACKUP_NAME_RE = re.compile(r"^mytrackz-(\d{8}T\d{6}Z)(\.sqlite3\.gz|-exports\.zip)$")
# Unfinished files; anything left behind by a killed run is swept next time.
TEMP_SUFFIXES = (".part", ".tmp", ".tmp-wal", ".tmp-shm")
STATUS_FILE = "status.json"

STEP_DATABASE = "Database copy"
STEP_EXPORTS = "User exports"
STEP_RETENTION = "Retention cleanup"

OK = "ok"
FAILED = "failed"
SKIPPED = "skipped"

LOCK_KEY = "backup_running"
LOCK_TIMEOUT = 60 * 60 * 2
ERROR_MAX_LENGTH = 300
COPY_CHUNK_BYTES = 1024 * 1024


class BackupError(Exception):
    """A backup step failed; the message is safe to show and to send."""


def supported():
    """Return whether this database can be backed up (SQLite only)."""
    return connection.vendor == "sqlite"


def backup_name(timestamp, suffix):
    """Return the file name for a backup taken at ``timestamp``."""
    return f"mytrackz-{timestamp.astimezone(UTC).strftime(TIMESTAMP_FORMAT)}{suffix}"


def parse_backup_name(name):
    """Return ``(taken_at, suffix)`` for a backup file name, or None."""
    match = BACKUP_NAME_RE.match(name)
    if not match:
        return None
    try:
        taken_at = datetime.strptime(match.group(1), TIMESTAMP_FORMAT).replace(
            tzinfo=UTC,
        )
    except ValueError:
        return None
    return taken_at, match.group(2)


def expired_names(names, now, retention_days):
    """Return the backup names older than the retention, oldest first."""
    if retention_days <= 0:
        return []
    cutoff = now - timedelta(days=retention_days)
    expired = []
    for name in names:
        parsed = parse_backup_name(name)
        if parsed and parsed[0] < cutoff:
            expired.append(name)
    return sorted(expired)


def _short_error(error):
    """Return one trimmed line describing an error."""
    text = " ".join(str(error).split()) or type(error).__name__
    if len(text) > ERROR_MAX_LENGTH:
        text = text[: ERROR_MAX_LENGTH - 1] + "…"
    return text


def _backup_dir():
    """Return the backup directory, created private if missing."""
    directory = Path(settings.BACKUP_DIR)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    return directory


def _sweep_unfinished(directory):
    """Delete partial files a killed run left behind."""
    for path in directory.iterdir():
        if path.name.startswith("mytrackz-") and path.name.endswith(TEMP_SUFFIXES):
            path.unlink(missing_ok=True)


def _publish(part, final):
    """Move a finished file into place, readable only by the app user."""
    part.chmod(0o600)
    part.replace(final)


# Step 1: database copy


def _integrity_verdict(path):
    """Return SQLite's integrity verdict for a database file ("ok" when sound)."""
    database = sqlite3.connect(path)
    try:
        return database.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        database.close()


def copy_database(directory, timestamp):
    """Write a verified, compressed copy of the database; return its path."""
    if not supported():
        msg = f"{connection.vendor} databases are not supported"
        raise BackupError(msg)

    final = directory / backup_name(timestamp, DATABASE_SUFFIX)
    raw = final.with_name(final.name + ".tmp")
    part = final.with_name(final.name + ".part")
    try:
        source = sqlite3.connect(connection.settings_dict["NAME"], timeout=60)
        target = sqlite3.connect(raw)
        try:
            # The online backup API: a consistent snapshot while the app
            # keeps reading and writing.
            source.backup(target)
        finally:
            target.close()
            source.close()
        verdict = _integrity_verdict(raw)
        if verdict != "ok":
            msg = f"the copy failed its integrity check ({verdict})"
            raise BackupError(msg)

        with raw.open("rb") as plain, gzip.open(part, "wb", compresslevel=6) as packed:
            shutil.copyfileobj(plain, packed, COPY_CHUNK_BYTES)
        _publish(part, final)
    finally:
        for leftover in (raw, part, *(Path(f"{raw}{ext}") for ext in ("-wal", "-shm"))):
            leftover.unlink(missing_ok=True)
    return final


# Step 2: per-user CSV exports


def export_users(directory, timestamp):
    """Zip every account's library as CSV; return ``(path, user_count)``."""
    # imported here: integrations imports app, not the other way round
    from integrations import exports  # noqa: PLC0415

    final = directory / backup_name(timestamp, EXPORTS_SUFFIX)
    part = final.with_name(final.name + ".part")
    users = list(get_user_model().objects.order_by("id"))
    try:
        with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED) as archive:
            for user in users:
                safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", user.username)
                with archive.open(f"user-{user.id}-{safe_name}.csv", "w") as member:
                    for row in exports.generate_rows(user):
                        member.write(row.encode())
        _publish(part, final)
    finally:
        part.unlink(missing_ok=True)
    return final, len(users)


# Step 3: retention


def list_backups(directory=None):
    """Return the finished backup files, oldest first."""
    directory = Path(directory or settings.BACKUP_DIR)
    if not directory.is_dir():
        return []
    return sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and parse_backup_name(path.name)
    )


def prune(directory, now):
    """Delete copies past their retention; return how many."""
    names = [path.name for path in list_backups(directory)]
    expired = expired_names(names, now, settings.BACKUP_RETENTION_DAYS)
    for name in expired:
        (directory / name).unlink(missing_ok=True)
    return len(expired)


# The run


def _step(steps, name, status, detail=""):
    steps.append({"name": name, "status": status, "detail": detail})


def _run_step(steps, name, action):
    """Run one step, recording its outcome; return its value or None."""
    try:
        value, detail = action()
    except BackupError as error:
        _step(steps, name, FAILED, _short_error(error))
    except Exception as error:  # noqa: BLE001 every failure must reach the report
        logger.warning("Backup step %r failed", name, exc_info=True)
        _step(steps, name, FAILED, _short_error(error))
    else:
        _step(steps, name, OK, detail)
        return value
    return None


def _perform(directory, started):
    """Walk the three steps; return ``(steps, files)``."""
    steps = []
    files = []

    def run_database_copy():
        path = copy_database(directory, started)
        return path, path.name

    database_copy = _run_step(steps, STEP_DATABASE, run_database_copy)
    if database_copy is None:
        reason = "not run because the database copy failed"
        _step(steps, STEP_EXPORTS, SKIPPED, reason)
        _step(steps, STEP_RETENTION, SKIPPED, reason)
        return steps, files
    files.append(database_copy)

    if settings.BACKUP_USER_EXPORTS:

        def run_exports():
            path, user_count = export_users(directory, started)
            return path, f"{user_count} user{'s' if user_count != 1 else ''}"

        exports_file = _run_step(steps, STEP_EXPORTS, run_exports)
        if exports_file is not None:
            files.append(exports_file)
    else:
        _step(steps, STEP_EXPORTS, SKIPPED, "turned off")

    def run_retention():
        removed = prune(directory, started)
        kept = sum(
            1 for path in list_backups(directory) if path.name.endswith(DATABASE_SUFFIX)
        )
        return True, f"{removed} old removed, {kept} kept"

    _run_step(steps, STEP_RETENTION, run_retention)
    return steps, files


def run_backup():
    """Take a backup now and return its report.

    Returns None when another backup is already running.
    """
    if not cache.add(LOCK_KEY, "1", LOCK_TIMEOUT):
        logger.info("Backup skipped: another one is already running")
        return None

    started = timezone.now()
    clock = time.monotonic()
    try:
        try:
            directory = _backup_dir()
            _sweep_unfinished(directory)
            steps, files = _perform(directory, started)
        except Exception as error:  # noqa: BLE001 every failure must reach the report
            logger.warning("Backup could not start", exc_info=True)
            steps = [
                {
                    "name": STEP_DATABASE,
                    "status": FAILED,
                    "detail": f"backup directory: {_short_error(error)}",
                },
            ]
            files = []

        report = {
            "started": started.isoformat(),
            "duration": round(time.monotonic() - clock, 1),
            "ok": all(step["status"] != FAILED for step in steps),
            "files": [
                {"name": path.name, "size": path.stat().st_size}
                for path in files
                if path.exists()
            ],
            "steps": steps,
        }
        report["size"] = sum(entry["size"] for entry in report["files"])

        _save_report(report)
        if report["ok"]:
            logger.info("Backup completed: %s", summarize(report))
        else:
            logger.error("Backup failed: %s", summarize(report))
        send_notification(report)
        return report
    finally:
        cache.delete(LOCK_KEY)


def failed_steps(report):
    """Return the steps of a report that failed."""
    return [step for step in report["steps"] if step["status"] == FAILED]


def summarize(report):
    """Return a one-line outcome, used for logs and the task result."""
    if report["ok"]:
        return (
            f"{len(report['files'])} file(s), {filesizeformat(report['size'])}, "
            f"{report['duration']}s"
        )
    return "; ".join(
        f"{step['name']} failed: {step['detail']}" for step in failed_steps(report)
    )


# Status for the Admin page


def _save_report(report):
    """Keep the last run's report next to the backups."""
    try:
        directory = Path(settings.BACKUP_DIR)
        part = directory / f"{STATUS_FILE}.part"
        part.write_text(json.dumps(report, indent=2))
        part.replace(directory / STATUS_FILE)
    except OSError:
        logger.warning("Could not save the backup status", exc_info=True)


def load_report():
    """Return the last run's report, or None."""
    try:
        report = json.loads((Path(settings.BACKUP_DIR) / STATUS_FILE).read_text())
        report["started"] = datetime.fromisoformat(report["started"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return report


def get_status():
    """Return what the Admin page shows about backups."""
    copies = []
    with contextlib.suppress(OSError):
        copies = [
            (parse_backup_name(path.name), path.stat().st_size)
            for path in list_backups()
        ]
    database_copies = [
        taken_at for (taken_at, suffix), _ in copies if suffix == DATABASE_SUFFIX
    ]
    return {
        "supported": supported(),
        "enabled": settings.BACKUP_ENABLED,
        "hour": settings.BACKUP_HOUR,
        "retention_days": settings.BACKUP_RETENTION_DAYS,
        "copy_count": len(database_copies),
        "oldest_copy": min(database_copies, default=None),
        "total_size": sum(size for _, size in copies),
        "notifications_configured": bool(settings.BACKUP_NOTIFICATION_URLS),
        "notify_on_success": settings.BACKUP_NOTIFY_ON_SUCCESS,
        "running": cache.get(LOCK_KEY) is not None,
        "last": load_report(),
    }


# Notifications


def build_notification(report):
    """Return ``(title, body)`` describing a backup run."""
    instance = settings.BACKUP_INSTANCE_NAME
    label = f" ({instance})" if instance else ""
    by_name = {step["name"]: step for step in report["steps"]}

    if report["ok"]:
        contents = "SQLite database"
        exports = by_name.get(STEP_EXPORTS)
        if exports and exports["status"] == OK:
            contents += f" + CSV exports for {exports['detail']}"
        lines = [
            f"Backed up: {contents}",
            f"Size: {filesizeformat(report['size'])} in {len(report['files'])} file(s)",
            f"Took: {report['duration']} seconds",
        ]
        retention = by_name.get(STEP_RETENTION)
        if retention and retention["detail"]:
            lines.append(f"Copies on the server: {retention['detail']}")
        return f"MyTrackz backup completed{label}", "\n".join(lines)

    lines = [
        f"Failed step: {step['name']}\nError: {step['detail']}"
        for step in failed_steps(report)
    ]
    database = by_name.get(STEP_DATABASE)
    if database and database["status"] == OK:
        lines.append(
            "The database copy succeeded and was kept "
            f"({filesizeformat(report['size'])}).",
        )
    else:
        lines.append("No new backup was written. Earlier copies were left alone.")
    return f"MyTrackz backup FAILED{label}", "\n".join(lines)


def send_notification(report):
    """Tell the instance's backup channels how a run went.

    Never raises: a notification problem must not fail the backup.
    """
    urls = [url for url in settings.BACKUP_NOTIFICATION_URLS if url]
    if not urls or (report["ok"] and not settings.BACKUP_NOTIFY_ON_SUCCESS):
        return False
    try:
        title, body = build_notification(report)
        notifier = apprise.Apprise()
        for url in urls:
            notifier.add(url)
        delivered = notifier.notify(
            title=title,
            body=body,
            notify_type=(
                apprise.NotifyType.SUCCESS
                if report["ok"]
                else apprise.NotifyType.FAILURE
            ),
        )
    except Exception as error:  # noqa: BLE001 a notification must never fail the backup
        # No traceback and no message: either could carry the webhook URL.
        logger.error(  # noqa: TRY400
            "Backup notification raised %s",
            type(error).__name__,
        )
        return False
    if not delivered:
        logger.error("Backup notification could not be delivered")
    return bool(delivered)
