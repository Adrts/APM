"""Bucket management and manifest lookup."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from . import messages as msg
from . import paths
from .errors import BucketError, ManifestError
from .known_buckets import KNOWN_BUCKETS
from .output import console
from .util import ensure_dir, remove_path, run, sanitary_name, write_json

BUCKET_NAME_RE = re.compile(r"^[\w.\-]+$")
ORDER_FILE = paths.BUCKETS_DIR / ".order.json"


def _read_order() -> list[str]:
    if not ORDER_FILE.exists():
        return []
    try:
        with open(ORDER_FILE, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return []
    return [str(item) for item in data] if isinstance(data, list) else []


def _write_order(names: list[str]) -> None:
    ensure_dir(paths.BUCKETS_DIR)
    write_json(ORDER_FILE, names)


def _remember(name: str) -> None:
    order = _read_order()
    if name not in order:
        order.append(name)
        _write_order(order)


def _forget(name: str) -> None:
    order = [item for item in _read_order() if item != name]
    _write_order(order)


def bucket_names() -> list[str]:
    ensure_dir(paths.BUCKETS_DIR)
    on_disk = {p.name for p in paths.BUCKETS_DIR.iterdir() if p.is_dir()}
    order = _read_order()
    ordered = [name for name in order if name in on_disk]
    ordered.extend(sorted(on_disk - set(ordered)))
    return ordered


def bucket_root(name: str) -> Path:
    return paths.BUCKETS_DIR / name


def manifest_dir(name: str) -> Path:
    root = bucket_root(name)
    nested = root / "bucket"
    return nested if nested.is_dir() else root


def manifest_path(app: str, bucket: str) -> Path:
    return manifest_dir(bucket) / f"{sanitary_name(app)}.json"


def find_app(app: str, bucket: str | None = None) -> tuple[str, Path]:
    app = sanitary_name(app)
    if not app:
        raise ManifestError(msg.ERR_APP_NAME_EMPTY)
    if bucket:
        path = manifest_path(app, bucket)
        if not path.is_file():
            raise ManifestError(
                msg.ERR_APP_NOT_FOUND_IN_BUCKET.format(bucket=bucket, app=app)
            )
        return bucket, path
    if not paths.BUCKETS_DIR.exists():
        raise BucketError(msg.MSG_NO_BUCKETS)
    matches: list[tuple[str, Path]] = []
    for name in bucket_names():
        path = manifest_path(app, name)
        if path.is_file():
            matches.append((name, path))
    if not matches:
        raise ManifestError(msg.ERR_APP_NOT_FOUND.format(app=app))
    if len(matches) > 1:
        names = ", ".join(name for name, _ in matches)
        console.warn(
            msg.MSG_BUCKET_AMBIGUOUS.format(
                app=app, names=names, chosen=matches[0][0]
            )
        )
    return matches[0]


def iter_manifests() -> list[tuple[str, str, Path]]:
    result: list[tuple[str, str, Path]] = []
    for bucket in bucket_names():
        directory = manifest_dir(bucket)
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            result.append((bucket, path.stem, path))
    return result


def bucket_source(name: str) -> str:
    root = bucket_root(name)
    if (root / ".git").exists() and shutil.which("git"):
        try:
            proc = run(["git", "-C", str(root), "config", "--get", "remote.origin.url"])
        except OSError:
            return str(root)
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    return str(root)


def manifest_count(name: str) -> int:
    directory = manifest_dir(name)
    if not directory.is_dir():
        return 0
    try:
        return sum(1 for _ in directory.glob("*.json"))
    except OSError:
        return 0


def add_bucket(name: str, repo: str | None = None) -> None:
    if not BUCKET_NAME_RE.match(name):
        raise BucketError(msg.ERR_BUCKET_NAME_INVALID.format(name=name))
    if not shutil.which("git"):
        raise BucketError(msg.ERR_GIT_MISSING)
    root = bucket_root(name)
    if root.exists():
        raise BucketError(msg.ERR_BUCKET_EXISTS.format(name=name))
    if not repo:
        repo = KNOWN_BUCKETS.get(name)
        if not repo:
            raise BucketError(msg.ERR_BUCKET_UNKNOWN.format(name=name))
    ensure_dir(paths.BUCKETS_DIR)
    console.info(msg.MSG_BUCKET_ADDING.format(name=name, repo=repo))
    proc = run(
        [
            "git",
            "clone",
            "--depth",
            "1",
            "--single-branch",
            "--no-tags",
            repo,
            str(root),
        ]
    )
    if proc.returncode != 0:
        remove_path(root)
        detail = (proc.stderr or proc.stdout).strip()
        raise BucketError(
            msg.ERR_BUCKET_ADD_FAILED.format(name=name, detail=detail)
        )
    _remember(name)
    console.success(
        msg.MSG_BUCKET_ADDED.format(name=name, count=manifest_count(name))
    )


def remove_bucket(name: str) -> None:
    root = bucket_root(name)
    if not root.exists():
        raise BucketError(msg.ERR_BUCKET_NOT_FOUND.format(name=name))
    remove_path(root)
    if root.exists():
        raise BucketError(msg.ERR_BUCKET_DELETE_INCOMPLETE.format(name=name))
    _forget(name)
    console.success(msg.MSG_BUCKET_REMOVED.format(name=name))


def update_bucket(name: str) -> None:
    root = bucket_root(name)
    if not root.exists():
        raise BucketError(msg.ERR_BUCKET_NOT_FOUND.format(name=name))
    if not (root / ".git").exists():
        raise BucketError(msg.ERR_BUCKET_NOT_GIT.format(name=name))
    if not shutil.which("git"):
        raise BucketError(msg.ERR_GIT_MISSING)
    fetch = run(["git", "-C", str(root), "fetch", "--depth", "1", "--no-tags", "origin"])
    if fetch.returncode != 0:
        raise BucketError(
            (fetch.stderr or fetch.stdout).strip() or msg.ERR_GIT_FETCH
        )
    reset = run(["git", "-C", str(root), "reset", "--hard", "FETCH_HEAD"])
    if reset.returncode != 0:
        raise BucketError(
            (reset.stderr or reset.stdout).strip() or msg.ERR_GIT_RESET
        )


def update_buckets(name: str | None = None) -> list[str]:
    names = [name] if name else bucket_names()
    if not names:
        console.info(msg.MSG_NO_BUCKETS)
        return []
    failures: list[str] = []
    for bucket in names:
        try:
            update_bucket(bucket)
            console.success(msg.MSG_BUCKET_UPDATED.format(name=bucket))
        except BucketError as exc:
            failures.append(msg.ERR_ITEM.format(name=bucket, error=exc))
            console.warn(msg.MSG_BUCKET_UPDATE_FAILED.format(name=bucket))
    return failures
