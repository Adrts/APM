"""Cache inspection and cleanup."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import paths
from .manifest import url_extension
from .output import console
from .util import display_width, fit_text, human_size, sha256_text, terminal_columns

SIDECAR_SUFFIXES = (".download", ".aria2", ".txt", ".tmp")
NAME_RE = re.compile(r"^(?P<app>[^#]+)#(?P<version>[^#]+)#(?P<rest>.+)$")


def cache_filename(app: str, version: str, url: str) -> str:
    digest = sha256_text(url)[:7]
    extension = url_extension(url)
    return f"{app}#{version}#{digest}{extension}"


def cache_path(app: str, version: str, url: str) -> Path:
    return paths.CACHE_DIR / cache_filename(app, version, url)


@dataclass
class CacheEntry:
    path: Path
    app: str
    version: str
    size: int


def entries() -> list[CacheEntry]:
    if not paths.CACHE_DIR.is_dir():
        return []
    result: list[CacheEntry] = []
    for path in paths.CACHE_DIR.iterdir():
        if not path.is_file():
            continue
        name = path.name
        if name.endswith(SIDECAR_SUFFIXES):
            continue
        match = NAME_RE.match(name)
        app = match.group("app") if match else name
        version = match.group("version") if match else ""
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        result.append(CacheEntry(path=path, app=app, version=version, size=size))
    return sorted(result, key=lambda item: (item.app.lower(), item.version))


def show() -> None:
    items = entries()
    if not items:
        console.info("缓存为空")
        return
    width = terminal_columns()
    app_width = max(12, max(len(item.app) for item in items))
    version_width = max(12, max(len(item.version) for item in items))
    console.plain(f"{'应用':<{app_width}}  {'版本':<{version_width}}  {'大小':>10}  文件")
    total = 0
    for item in items:
        total += item.size
        prefix = (
            f"{item.app:<{app_width}}  {item.version:<{version_width}}  "
            f"{human_size(item.size):>10}  "
        )
        console.plain(prefix + fit_text(item.path.name, width - display_width(prefix)))
    console.plain()
    console.plain(f"共 {len(items)} 个缓存文件，合计 {human_size(total)}")


def _remove_with_sidecars(path: Path) -> int:
    removed = 0
    for suffix in ("",) + SIDECAR_SUFFIXES:
        target = path.with_name(path.name + suffix) if suffix else path
        if target.exists():
            try:
                target.unlink()
                if suffix == "":
                    removed += 1
            except OSError as exc:
                console.warn(f"删除失败：{target}（{exc}）")
    return removed


def remove(target: str | None) -> None:
    """Delete cached files of one app; None means every cached file."""
    items = entries()
    key = target.lower() if target else None
    selected = [item for item in items if key is None or item.app.lower() == key]
    total = sum(item.size for item in selected)
    removed = 0
    for item in selected:
        removed += _remove_with_sidecars(item.path)
    if paths.CACHE_DIR.is_dir():
        for path in paths.CACHE_DIR.iterdir():
            if path.is_file() and path.name.endswith(SIDECAR_SUFFIXES):
                if key is None or path.name.lower().startswith(f"{key}#"):
                    try:
                        path.unlink()
                        removed += 1
                    except OSError:
                        pass
    if removed == 0:
        console.info("没有匹配的缓存文件")
        return
    console.success(f"已删除 {removed} 个文件，释放 {human_size(total)}")
