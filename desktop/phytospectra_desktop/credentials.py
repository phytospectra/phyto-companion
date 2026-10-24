"""Credential storage for the desktop app.

Stores the Firebase ID token returned by the web sign-in handshake in the
OS user directory (never the installation directory), mode-restricted.
Tokens are short-lived; refresh re-opens the browser handshake.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path

_CRED_FILE = "phytospectra_credentials.json"


def _cred_path() -> Path:
    base = os.environ.get("PHYTO_DESKTOP_HOME") or tempfile.gettempdir()
    home = Path(base) / ".phytospectra"
    home.mkdir(parents=True, exist_ok=True)
    return home / _CRED_FILE


def save_credentials(payload: dict) -> None:
    path = _cred_path()
    path.write_text(json.dumps(payload), encoding="utf-8")
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def load_credentials() -> dict | None:
    path = _cred_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def clear_credentials() -> None:
    path = _cred_path()
    if path.exists():
        path.unlink(missing_ok=True)
