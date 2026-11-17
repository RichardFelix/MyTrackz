import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.test import SimpleTestCase

from config import secret_key

MIN_KEY_LENGTH = 50


class SecretKeyFileTests(SimpleTestCase):
    """Test the generated secret key that stands in for a missing SECRET."""

    def setUp(self):
        """Create a folder to hold the key."""
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "db" / "secret_key"

    def test_key_is_generated_once_and_reused(self):
        """Test the first call creates a private key file and later calls read it."""
        with self.assertLogs("config.secret_key", level="WARNING") as logs:
            key = secret_key.load_or_create(self.path)

        self.assertGreaterEqual(len(key), MIN_KEY_LENGTH)
        self.assertEqual(self.path.read_text(), key)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertIn("SECRET is not set", logs.output[0])

        with self.assertNoLogs("config.secret_key", level="WARNING"):
            self.assertEqual(secret_key.load_or_create(self.path), key)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_each_install_gets_its_own_key(self):
        """Test two installs never end up with the same key."""
        other = self.path.with_name("other_key")

        self.assertNotEqual(
            secret_key.load_or_create(self.path),
            secret_key.load_or_create(other),
        )

    def test_existing_key_is_kept(self):
        """Test a key already on disk is used as it is."""
        self.path.parent.mkdir()
        self.path.write_text("existing-key\n")

        self.assertEqual(secret_key.load_or_create(self.path), "existing-key")

    def test_empty_file_is_replaced(self):
        """Test a key file left empty by an interrupted start is filled in."""
        self.path.parent.mkdir()
        self.path.touch()

        key = secret_key.load_or_create(self.path)

        self.assertGreaterEqual(len(key), MIN_KEY_LENGTH)
        self.assertEqual(self.path.read_text(), key)

    def test_losing_a_startup_race_uses_the_winning_key(self):
        """Test a process that loses the race adopts the other process's key."""
        self.path.parent.mkdir()
        real_link = secret_key.os.link

        def link_after_other_process(source, target):
            Path(target).write_text("winner-key")
            real_link(source, target)

        with mock.patch.object(secret_key.os, "link", link_after_other_process):
            key = secret_key.load_or_create(self.path)

        self.assertEqual(key, "winner-key")
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])


class SecretKeySettingTests(SimpleTestCase):
    """Test how settings choose the secret key."""

    def settings_secret_key(self, environment=None):
        """Return SECRET_KEY as a fresh process computes it."""
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in {"SECRET", "SECRET_FILE"}
        }
        env.update(environment or {})
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import config.settings as s; print(s.SECRET_KEY)",
            ],
            cwd=settings.BASE_DIR,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip().splitlines()[-1]

    def test_secret_from_environment_wins(self):
        """Test SECRET is used as given."""
        self.assertEqual(self.settings_secret_key({"SECRET": "from-env"}), "from-env")

    def test_secret_from_file(self):
        """Test SECRET_FILE is read when SECRET is not set."""
        with tempfile.TemporaryDirectory() as folder:
            secret_file = Path(folder) / "secret"
            secret_file.write_text("from-file\n")

            self.assertEqual(
                self.settings_secret_key({"SECRET_FILE": str(secret_file)}),
                "from-file",
            )

    def test_missing_secret_uses_generated_key(self):
        """Test an install with no SECRET uses the key stored beside the database."""
        key = self.settings_secret_key()

        stored = Path(settings.BASE_DIR, "db", "secret_key").read_text().strip()
        self.assertGreaterEqual(len(key), MIN_KEY_LENGTH)
        self.assertEqual(key, stored)

    def test_published_example_secret_is_ignored(self):
        """Test the example value from older Compose files is not used as a key."""
        key = self.settings_secret_key({"SECRET": "longstring"})

        stored = Path(settings.BASE_DIR, "db", "secret_key").read_text().strip()
        self.assertEqual(key, stored)

    def test_no_key_is_built_in(self):
        """Test the source code carries no default secret key."""
        source = Path(settings.BASE_DIR, "config", "settings.py").read_text()

        self.assertNotIn("ifx7bdUWo5EwC2NQNihjRjOrW00Cdv5Y", source)
