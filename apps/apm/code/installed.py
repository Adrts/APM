"""Installed application records."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import messages as msg, paths
from .errors import ApmError
from .util import is_reparse_dir, read_json, write_json

META_FILE = "install.json"
MANIFEST_FILE = "manifest.json"


@dataclass
class InstalledApp:
    app: str
    version: str
    bucket: str | None = None
    hold: bool = False


def meta_path(app: str) -> Path:
    return paths.app_dir(app) / META_FILE


def manifest_copy_path(app: str) -> Path:
    return paths.app_dir(app) / MANIFEST_FILE


def read_meta(app: str) -> dict[str, Any] | None:
    path = meta_path(app)
    if not path.is_file():
        return None
    try:
        data = read_json(path)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_meta(app: str, meta: dict[str, Any]) -> None:
    write_json(meta_path(app), meta)


def _version_from_link(app: str) -> str | None:
    current = paths.current_dir(app)
    if not current.exists():
        return None
    target = Path(os.path.realpath(current))
    name = target.name
    return name if name and name != "current" else None


def installed_version(app: str) -> str | None:
    meta = read_meta(app)
    if meta and meta.get("version"):
        return str(meta["version"])
    return _version_from_link(app)


def is_installed(app: str) -> bool:
    return paths.current_dir(app).exists()


def set_hold(app: str, hold: bool) -> bool:
    """Set the hold flag of an installed app; return True when the state changed."""
    if not is_installed(app):
        raise ApmError(msg.MSG_APP_NOT_INSTALLED.format(app=app))
    meta = read_meta(app)
    if meta is None:
        raise ApmError(msg.ERR_APP_RECORD_MISSING.format(app=app))
    if bool(meta.get("hold")) == hold:
        return False
    meta["hold"] = hold
    write_meta(app, meta)
    return True


def installed_apps() -> list[InstalledApp]:
    if not paths.APPS_DIR.is_dir():
        return []
    result: list[InstalledApp] = []
    for entry in sorted(paths.APPS_DIR.iterdir(), key=lambda p: p.name.lower()):
        if not entry.is_dir() or entry.name.lower() == "apm":
            continue
        if not paths.current_dir(entry.name).exists():
            continue
        meta = read_meta(entry.name) or {}
        version = str(meta.get("version") or _version_from_link(entry.name) or "")
        result.append(
            InstalledApp(
                app=entry.name,
                version=version,
                bucket=str(meta["bucket"]) if meta.get("bucket") else None,
                hold=bool(meta.get("hold")),
            )
        )
    return result


def version_dirs(app: str) -> list[Path]:
    root = paths.app_dir(app)
    if not root.is_dir():
        return []
    result: list[Path] = []
    for entry in root.iterdir():
        if entry.name in ("current", META_FILE, MANIFEST_FILE):
            continue
        if entry.is_dir() and not is_reparse_dir(entry):
            result.append(entry)
    return sorted(result, key=lambda p: p.name.lower())
