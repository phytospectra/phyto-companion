"""Desktop app configuration.

Points at the production orchestrator by default; override with a
``PHYTO_API_BASE`` environment variable (or a config.json next to the
executable) for staging/self-hosted planes.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

DEFAULT_API_BASE = "https://phyto-orchestrator-819429615655.us-central1.run.app"
DEFAULT_WEB_BASE = "https://app.phytospectra.com"
APP_NAME = "Phytospectra Desktop"
APP_VERSION = "0.1.0"

# Files the ingestion layer recognizes as NMR / LC-MS inputs.
NMR_DIR_MARKERS = ("acqu.par", "acqus", "acqu", "pdata")
NMR_FILE_SUFFIXES = (".fid", ".ser", ".zip")
MS_FILE_SUFFIXES = (".mzml", ".mzxml", ".mzml.gz", ".mzxml.gz", ".raw", ".mgf", ".d")


def _bundle_dir() -> Path:
    """Directory the frozen exe (or this file) lives in."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


def _load_overrides() -> dict:
    cfg_path = _bundle_dir() / "config.json"
    if cfg_path.exists():
        try:
            return json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


_OVERRIDES = _load_overrides()


def api_base() -> str:
    return (
        os.environ.get("PHYTO_API_BASE")
        or _OVERRIDES.get("api_base")
        or DEFAULT_API_BASE
    )


def web_base() -> str:
    return (
        os.environ.get("PHYTO_WEB_BASE")
        or _OVERRIDES.get("web_base")
        or DEFAULT_WEB_BASE
    )
