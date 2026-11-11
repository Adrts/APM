"""Two-phase download layer: cache, mirrors, backends and verification."""

from __future__ import annotations

import os
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urldefrag, urlparse

from . import cache, paths, tools, verify
from .errors import DownloadError
from .manifest import AppRef
from .output import console
from .util import ensure_dir, lp, remove_path

USER_AGENT = "APM/0.1"

_warned_mirrors: set[str] = set()
_warn_lock = threading.Lock()


@dataclass
class DownloadItem:
    app: str
    version: str
    url: str
    hash: str | None
    cookie: Any
    path: Path


@dataclass
class DownloadResult:
    item: DownloadItem
    ok: bool
    error: str | None = None


def build_items(refs: list[AppRef]) -> list[DownloadItem]:
    items: list[DownloadItem] = []
    seen: set[Path] = set()
    for ref in refs:
        for package in ref.packages():
            path = cache.cache_path(ref.app, ref.version, package.url)
            if path in seen:
                continue
            seen.add(path)
            items.append(
                DownloadItem(
                    app=ref.app,
                    version=ref.version,
                    url=package.url,
                    hash=package.hash,
                    cookie=package.cookie,
                    path=path,
                )
            )
    return items


def _cookie_header(cookie: Any) -> str | None:
    if not cookie:
        return None
    if isinstance(cookie, str):
        return cookie
    if isinstance(cookie, dict):
        return ";".join(f"{key}={value}" for key, value in cookie.items())
    if isinstance(cookie, list):
        parts = [_cookie_header(item) for item in cookie]
        return ";".join(part for part in parts if part) or None
    return str(cookie)


def _warn_mirror(provider: str) -> None:
    with _warn_lock:
        if provider in _warned_mirrors:
            return
        _warned_mirrors.add(provider)
    console.warn(
        f"未配置 {provider} 镜像前缀，按原始地址下载"
        f"（可在 {paths.CONFIG_FILE} 中设置 download.mirror.{provider}）"
    )


def transport_url(url: str, config: dict[str, Any]) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return url
    host = (parsed.hostname or "").lower()
    mirror = config.get("download", {}).get("mirror", {})
    if "github.com" in host or "github.com" in url.lower():
        prefix = str(mirror.get("github") or "").strip()
        if not prefix:
            _warn_mirror("github")
        elif not url.lower().startswith(prefix.lower()):
            return prefix.rstrip("/") + "/" + url
        return url
    if "sourceforge.net" in host or "sourceforge.net" in url.lower():
        prefix = str(mirror.get("sourceforge") or "").strip()
        if not prefix:
            _warn_mirror("sourceforge")
        elif not url.lower().startswith(prefix.lower()):
            return prefix.rstrip("/") + "/" + url
        return url
    return url


def _request_url(url: str) -> str:
    return urldefrag(url)[0]


def _progress_callback(label: str) -> Callable[[int, int], None]:
    state = {"last": -1}

    def callback(read: int, total: int) -> None:
        if total <= 0:
            return
        percent = min(100, int(read * 100 / total))
        if percent >= state["last"] + 10 or percent == 100:
            state["last"] = percent
            console.progress(f"{label} {percent:3d}%")

    return callback


def _python_download(
    url: str, dest: Path, cookie: str | None, progress: bool, label: str
) -> None:
    headers = {"User-Agent": USER_AGENT}
    if cookie:
        headers["Cookie"] = cookie
    request = urllib.request.Request(_request_url(url), headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            total = int(response.headers.get("Content-Length") or 0)
            callback = _progress_callback(label) if progress else None
            read = 0
            with open(lp(dest), "wb") as fh:
                while True:
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    fh.write(chunk)
                    read += len(chunk)
                    if callback:
                        callback(read, total)
            if callback:
                console.progress(f"{label} 100%", final=True)
    except urllib.error.HTTPError as exc:
        raise DownloadError(f"HTTP {exc.code}：{url}") from exc
    except urllib.error.URLError as exc:
        raise DownloadError(f"网络错误：{exc.reason}（{url}）") from exc
    except OSError as exc:
        raise DownloadError(f"写入失败：{exc}") from exc


def _run_backend(cmd: list[str], url: str) -> None:
    proc = tools.run_tool(cmd)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise DownloadError(
            f"下载失败（退出码 {proc.returncode}）：{url}\n{detail}".strip()
        )


def _curl_download(url: str, dest: Path, cookie: str | None, curl: Path) -> None:
    cmd = [
        str(curl),
        "-L",
        "--fail",
        "--retry",
        "0",
        "--silent",
        "--show-error",
        "--output",
        str(dest),
        "--connect-timeout",
        "30",
        "--user-agent",
        USER_AGENT,
    ]
    if cookie:
        cmd.extend(["--header", f"Cookie: {cookie}"])
    cmd.append(_request_url(url))
    _run_backend(cmd, url)


def _wget_download(url: str, dest: Path, cookie: str | None, wget: Path) -> None:
    cmd = [
        str(wget),
        "--no-verbose",
        "--output-document",
        str(dest),
        "--timeout=60",
        "--tries=1",
        f"--user-agent={USER_AGENT}",
    ]
    if cookie:
        cmd.append(f"--header=Cookie: {cookie}")
    cmd.append(_request_url(url))
    _run_backend(cmd, url)


def _aria2_download(
    url: str,
    dest: Path,
    cookie: str | None,
    aria2: Path,
    segments: int,
) -> None:
    ensure_dir(dest.parent)
    if dest.exists():
        remove_path(dest)
    control = Path(str(dest) + ".aria2")
    if control.exists():
        remove_path(control)
    cmd = [
        str(aria2),
        "--dir",
        str(dest.parent),
        "--out",
        dest.name,
        "--split",
        str(max(1, segments)),
        "--max-connection-per-server",
        str(max(1, segments)),
        "--allow-overwrite=true",
        "--auto-file-renaming=false",
        "--file-allocation=none",
        "--max-tries=1",
        "--console-log-level=warn",
        "--summary-interval=0",
        "--user-agent",
        USER_AGENT,
    ]
    if cookie:
        cmd.extend(["--header", f"Cookie: {cookie}"])
    cmd.append(_request_url(url))
    _run_backend(cmd, url)


def _backend_name(config: dict[str, Any]) -> str:
    return str(config.get("download", {}).get("backend", "python"))


def _download_one(
    item: DownloadItem,
    config: dict[str, Any],
    progress: bool,
    cache_hit: bool,
) -> DownloadResult:
    backend = _backend_name(config)
    cookie = _cookie_header(item.cookie)
    label = f"下载 {item.app} {item.version}（{item.path.name}）"
    ensure_dir(item.path.parent)
    tmp = item.path.with_name(item.path.name + ".download")

    try:
        if cache_hit:
            console.info(f"使用缓存 {item.path.name}")
            ok, message = verify.verify_file(item.path, item.hash, config, item.app)
            if ok:
                return DownloadResult(item=item, ok=True)
            console.warn(f"缓存校验失败，重新下载：{item.path.name}\n{message or ''}")
            _purge(item.path)

        transport = transport_url(item.url, config)
        console.info(f"下载 {item.path.name}" + ("（镜像）" if transport != item.url else ""))

        if backend == "python":
            if tmp.exists():
                remove_path(tmp)
            _python_download(transport, tmp, cookie, progress, label)
            target = tmp
        elif backend == "curl":
            curl = tools.find_backend("curl")
            if not curl:
                raise DownloadError("未检测到 curl.exe")
            if tmp.exists():
                remove_path(tmp)
            _curl_download(transport, tmp, cookie, curl)
            target = tmp
        elif backend == "wget":
            wget = tools.find_backend("wget")
            if not wget:
                raise DownloadError("未检测到 wget.exe")
            if tmp.exists():
                remove_path(tmp)
            _wget_download(transport, tmp, cookie, wget)
            target = tmp
        elif backend == "aria2":
            aria2 = tools.find_backend("aria2")
            if not aria2:
                raise DownloadError("未检测到 aria2c.exe")
            segments = int(
                config.get("download", {}).get("concurrency", {}).get("segments", 5)
            )
            _aria2_download(transport, item.path, cookie, aria2, segments)
            target = item.path
        else:
            raise DownloadError(f"未知的下载后端：{backend}")

        ok, message = verify.verify_file(target, item.hash, config, item.app)
        if not ok:
            _purge(item.path if target == item.path else tmp)
            return DownloadResult(item=item, ok=False, error=message or "校验失败")

        if target == tmp:
            os.replace(lp(tmp), lp(item.path))
        else:
            control = Path(str(item.path) + ".aria2")
            if control.exists():
                remove_path(control)
        console.success(f"已下载 {item.path.name}")
        return DownloadResult(item=item, ok=True)
    except Exception as exc:  # noqa: BLE001 - failures are reported per item
        _purge(tmp if tmp.exists() else item.path)
        return DownloadResult(item=item, ok=False, error=str(exc))


def _purge(path: Path) -> None:
    remove_path(path)
    for suffix in (".download", ".aria2"):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists():
            remove_path(sidecar)


def download_all(items: list[DownloadItem], config: dict[str, Any]) -> list[DownloadResult]:
    if not items:
        return []
    ensure_dir(paths.CACHE_DIR)
    concurrency = int(
        config.get("download", {}).get("concurrency", {}).get("apps", 3)
    )
    concurrency = max(1, concurrency)
    progress = concurrency == 1 and console.tty
    results: dict[int, DownloadResult] = {}
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {
            executor.submit(
                _download_one,
                item,
                config,
                progress,
                item.path.exists(),
            ): index
            for index, item in enumerate(items)
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                results[index] = future.result()
            except Exception as exc:  # noqa: BLE001
                results[index] = DownloadResult(
                    item=items[index], ok=False, error=str(exc)
                )
    return [results[index] for index in range(len(items))]
