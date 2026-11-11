"""Filesystem locations used by APM.

APM_ROOT is the Git clone directory.  The launcher exports APM_ROOT; when the
package is started directly (for development) it is derived from this file.
"""

from __future__ import annotations

import os
from pathlib import Path


def _detect_root() -> Path:
    env_root = os.environ.get("APM_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()
    # <root>/apps/apm/code/paths.py
    return Path(__file__).resolve().parents[3]


ROOT = _detect_root()
APPS_DIR = ROOT / "apps"
SHIM_DIR = ROOT / "shims"
BUCKETS_DIR = ROOT / "buckets"
CACHE_DIR = ROOT / "cache"
PERSIST_DIR = ROOT / "persist"
SUPPORTING_DIR = APPS_DIR / "apm" / "supporting"
VENDORED_SHIM = SUPPORTING_DIR / "shims" / "kiennq" / "shim.exe"
VENDORED_SHIM_CHECKSUM = SUPPORTING_DIR / "shims" / "kiennq" / "checksum.sha256"

CONFIG_DIR = Path.home() / ".config" / "apm"
CONFIG_FILE = CONFIG_DIR / "config.json"

START_MENU_APPS = (
    Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
    / "Microsoft"
    / "Windows"
    / "Start Menu"
    / "Programs"
    / "APM Apps"
)


def app_dir(app: str) -> Path:
    return APPS_DIR / app


def version_dir(app: str, version: str) -> Path:
    return app_dir(app) / version


def current_dir(app: str) -> Path:
    return app_dir(app) / "current"


def persist_app_dir(app: str) -> Path:
    return PERSIST_DIR / app
