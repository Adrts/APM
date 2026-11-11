"""Bucket management and manifest lookup."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

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
        raise ManifestError("应用名为空")
    if bucket:
        path = manifest_path(app, bucket)
        if not path.is_file():
            raise ManifestError(f"bucket '{bucket}' 中找不到应用 '{app}'")
        return bucket, path
    if not paths.BUCKETS_DIR.exists():
        raise BucketError("尚未添加任何 bucket，运行 apm bucket add main")
    matches: list[tuple[str, Path]] = []
    for name in bucket_names():
        path = manifest_path(app, name)
        if path.is_file():
            matches.append((name, path))
    if not matches:
        raise ManifestError(f"找不到应用 '{app}'，可运行 apm search {app} 查看")
    if len(matches) > 1:
        names = ", ".join(name for name, _ in matches)
        console.warn(f"应用 '{app}' 存在于多个 bucket（{names}），使用 '{matches[0][0]}'")
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
        raise BucketError(f"bucket 名称无效：{name}")
    if not shutil.which("git"):
        raise BucketError("未检测到 git，请先安装 git 并加入 PATH")
    root = bucket_root(name)
    if root.exists():
        raise BucketError(f"bucket '{name}' 已存在")
    if not repo:
        repo = KNOWN_BUCKETS.get(name)
        if not repo:
            raise BucketError(f"未知 bucket：{name}，请显式提供仓库地址")
    ensure_dir(paths.BUCKETS_DIR)
    console.info(f"添加 bucket '{name}'：{repo}")
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
        raise BucketError(f"添加 bucket 失败：{name}\n{detail}")
    _remember(name)
    console.success(f"bucket '{name}' 已添加（{manifest_count(name)} 个清单）")


def remove_bucket(name: str) -> None:
    root = bucket_root(name)
    if not root.exists():
        raise BucketError(f"bucket '{name}' 不存在")
    remove_path(root)
    if root.exists():
        raise BucketError(f"bucket '{name}' 删除不完整，请关闭占用后重试")
    _forget(name)
    console.success(f"bucket '{name}' 已删除")


def update_bucket(name: str) -> None:
    root = bucket_root(name)
    if not root.exists():
        raise BucketError(f"bucket '{name}' 不存在")
    if not (root / ".git").exists():
        raise BucketError(f"bucket '{name}' 不是 git 仓库，无法更新")
    if not shutil.which("git"):
        raise BucketError("未检测到 git，请先安装 git 并加入 PATH")
    fetch = run(["git", "-C", str(root), "fetch", "--depth", "1", "--no-tags", "origin"])
    if fetch.returncode != 0:
        raise BucketError((fetch.stderr or fetch.stdout).strip() or "git fetch 失败")
    reset = run(["git", "-C", str(root), "reset", "--hard", "FETCH_HEAD"])
    if reset.returncode != 0:
        raise BucketError((reset.stderr or reset.stdout).strip() or "git reset 失败")


def update_buckets(name: str | None = None) -> list[str]:
    names = [name] if name else bucket_names()
    if not names:
        console.info("尚未添加任何 bucket，运行 apm bucket add main")
        return []
    failures: list[str] = []
    for bucket in names:
        try:
            update_bucket(bucket)
            console.success(f"bucket '{bucket}' 已更新")
        except BucketError as exc:
            failures.append(f"{bucket}：{exc}")
            console.warn(f"bucket '{bucket}' 更新失败，保留旧索引")
    return failures
