"""Fallback secret key for installs that do not set ``SECRET``.

A key shipped in the source code is public, and anyone who knows it can forge
session cookies. An install without ``SECRET`` therefore gets its own random
key, generated on first start and kept next to the database.
"""

import contextlib
import logging
import os
import secrets
from pathlib import Path

logger = logging.getLogger(__name__)

KEY_BYTES = 50

# Example values that were published in the Compose files and documentation.
PLACEHOLDERS = frozenset({"longstring"})


def load_or_create(path):
    """Return the key stored at ``path``, generating it on first use.

    Several processes (web workers, Celery) can start at once, so the file is
    put in place atomically: whichever process links its key first wins and
    every process reads that same key back.
    """
    path = Path(path)
    key = _read(path)
    if key:
        return key

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as file:
            file.write(secrets.token_urlsafe(KEY_BYTES))
        try:
            os.link(temporary, path)
        except FileExistsError:
            # Another process created the key first, or an empty file was
            # left behind; an empty one is replaced below.
            if not _read(path):
                temporary.replace(path)
        else:
            logger.warning(
                "SECRET is not set: generated a secret key and saved it to %s. "
                "Set SECRET to manage the key yourself.",
                path,
            )
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()

    return _read(path)


def _read(path):
    """Return the stored key, or an empty string when there is none."""
    try:
        return path.read_text().strip()
    except FileNotFoundError:
        return ""
