"""Local, opt-in storage of eBay credentials on the user's own machine.

Saved (only if the user asks) to a file in their home directory, readable by
that user only where the OS supports it. This is the same plaintext-on-your-
own-machine pattern as ``~/.netrc`` or ``~/.aws/credentials`` — convenient,
but it is NOT encryption. Environment variables, if set, always win over the
saved file so a scan can override without touching stored values.
"""

from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path

log = logging.getLogger(__name__)

CONFIG_PATH = Path.home() / ".arbfinder-credentials.json"


def _read_file() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Could not read %s: %s", CONFIG_PATH, exc)
        return {}


def load_credentials() -> dict:
    """Return {'ebay_client_id', 'ebay_client_secret'}, env vars overriding the file."""
    saved = _read_file()
    return {
        "ebay_client_id": os.environ.get("EBAY_CLIENT_ID") or saved.get("ebay_client_id", ""),
        "ebay_client_secret": os.environ.get("EBAY_CLIENT_SECRET") or saved.get("ebay_client_secret", ""),
    }


def has_saved_secret() -> bool:
    return bool(_read_file().get("ebay_client_secret"))


def save_credentials(client_id: str, client_secret: str) -> Path:
    """Persist credentials to the home-dir file, locked to the owner (0600)."""
    CONFIG_PATH.write_text(
        json.dumps({"ebay_client_id": client_id, "ebay_client_secret": client_secret}),
        encoding="utf-8",
    )
    try:  # best effort — no-op / limited on Windows
        os.chmod(CONFIG_PATH, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return CONFIG_PATH


def clear_credentials() -> bool:
    """Delete the saved-credentials file. Returns True if a file was removed."""
    if CONFIG_PATH.exists():
        try:
            CONFIG_PATH.unlink()
            return True
        except OSError as exc:
            log.warning("Could not delete %s: %s", CONFIG_PATH, exc)
    return False
