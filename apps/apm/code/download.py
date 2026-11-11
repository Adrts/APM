"""Two-phase download layer: cache, mirrors, backends and verification."""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urldefrag, urlparse

from . import cache, paths, tools, verify
from .errors import DownloadError
from .manifest import AppRef
from .output import console
from .progress import progress
from .util import ensure_dir, lp, remove_path

USER_AGENT = "APM/0.1"

_warned_mirrors: set[str] = set()
_warn_lock = threading.Lock()

# 协作式取消：Ctrl+C 时置位，工作线程在块间检查点自行退出，
# 避免 ThreadPoolExecutor 的非 daemon 线程把进程拖到下载结束。
_cancel = threading.Event()
_workers_lock = threading.Lock()
_active_workers = 0


def request_cancel() -> None:
    _cancel.set()


def cancel_requested() -> bool:
    return _cancel.is_set()


def active_workers() -> int:
    with _workers_lock:
        return _active_workers


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


def _python_download(url: str, dest: Path, cookie: str | None, key: str, label: str) -> None:
    headers = {"User-Agent": USER_AGENT}
    if cookie:
        headers["Cookie"] = cookie
    request = urllib.request.Request(_request_url(url), headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            total = int(response.headers.get("Content-Length") or 0)
            progress.start(key, label, total)
            read = 0
            with open(lp(dest), "wb") as fh:
                while True:
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    fh.write(chunk)
                    read += len(chunk)
                    progress.update(key, done=read)
    except urllib.error.HTTPError as exc:
        raise DownloadError(f"HTTP {exc.code}：{url}") from exc
    except urllib.error.URLError as exc:
        raise DownloadError(f"网络错误：{exc.reason}（{url}）") from exc
    except OSError as exc:
        raise DownloadError(f"写入失败：{exc}") from exc
    finally:
        progress.finish(key)


def _run_backend(cmd: list[str], url: str) -> None:
    # curl/wget 无官方进度接口，保持无进度条；后端自身重试已用参数关闭。
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


def _free_tcp_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _aria2_rpc(port: int, secret: str, method: str, params: list[Any], timeout: float = 5.0) -> Any:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/jsonrpc",
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "apm",
                "method": method,
                "params": ["token:" + secret, *params],
            }
        ).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if "error" in payload:
        raise DownloadError(f"aria2 RPC {method} 失败：{payload['error']}")
    return payload.get("result")


def _aria2_wait_rpc(port: int, secret: str, process: subprocess.Popen) -> None:
    deadline = time.monotonic() + 5.0
    while True:
        if process.poll() is not None:
            raise DownloadError(f"aria2 RPC 进程提前退出（退出码 {process.returncode}）")
        try:
            _aria2_rpc(port, secret, "aria2.getVersion", [], timeout=1.0)
            return
        except (urllib.error.URLError, OSError):
            if time.monotonic() >= deadline:
                raise DownloadError("aria2 RPC 服务未在 5 秒内就绪")
            time.sleep(0.05)


def _aria2_download(
    url: str,
    dest: Path,
    cookie: str | None,
    aria2: Path,
    segments: int,
    key: str,
    label: str,
) -> None:
    # 官方进度接口：JSON-RPC（--enable-rpc + tellStatus），与 aria2 官方 WebUI 同一机制。
    ensure_dir(dest.parent)
    if dest.exists():
        remove_path(dest)
    control = Path(str(dest) + ".aria2")
    if control.exists():
        remove_path(control)

    port = _free_tcp_port()
    secret = uuid.uuid4().hex
    cmd = [
        str(aria2),
        "--enable-rpc=true",
        "--rpc-listen-port",
        str(port),
        "--rpc-secret",
        secret,
        "--quiet=true",
        # 父进程退出时 aria2 自行退出，避免孤儿进程。
        "--stop-with-process",
        str(os.getpid()),
        "--allow-overwrite=true",
        "--auto-file-renaming=false",
        "--file-allocation=none",
        "--console-log-level=warn",
    ]
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        _aria2_wait_rpc(port, secret, process)
        options: dict[str, Any] = {
            "dir": str(dest.parent),
            "out": dest.name,
            "split": str(max(1, segments)),
            "max-connection-per-server": str(max(1, segments)),
            "max-tries": "1",
            "user-agent": USER_AGENT,
        }
        if cookie:
            options["header"] = [f"Cookie: {cookie}"]
        gid = _aria2_rpc(port, secret, "aria2.addUri", [[_request_url(url)], options])

        progress.start(key, label)
        try:
            while True:
                status = _aria2_rpc(
                    port,
                    secret,
                    "aria2.tellStatus",
                    [gid, ["status", "totalLength", "completedLength", "errorCode", "errorMessage"]],
                )
                if status["status"] == "complete":
                    progress.update(
                        key, done=int(status.get("completedLength") or 0), total=int(status.get("totalLength") or 0)
                    )
                    break
                if status["status"] in ("error", "removed"):
                    detail = status.get("errorMessage") or status.get("errorCode") or ""
                    raise DownloadError(f"下载失败：{url}\n{detail}".strip())
                progress.update(
                    key, done=int(status.get("completedLength") or 0), total=int(status.get("totalLength") or 0)
                )
                time.sleep(0.2)
        finally:
            progress.finish(key)
            with contextlib.suppress(Exception):
                _aria2_rpc(port, secret, "aria2.shutdown", [], timeout=1.0)
    finally:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    if process.returncode != 0:
        raise DownloadError(f"下载失败（aria2 退出码 {process.returncode}）：{url}")


def _backend_name(config: dict[str, Any]) -> str:
    return str(config.get("download", {}).get("backend", "python"))


def _download_one(
    item: DownloadItem,
    config: dict[str, Any],
    cache_hit: bool,
) -> DownloadResult:
    backend = _backend_name(config)
    cookie = _cookie_header(item.cookie)
    key = item.path.name
    label = f"下载 {item.app} {item.version}"
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
            _python_download(transport, tmp, cookie, key, label)
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
            _aria2_download(transport, item.path, cookie, aria2, segments, key, label)
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
        console.success(f"完成 {item.path.name}")
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
    results: dict[int, DownloadResult] = {}
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {
            executor.submit(
                _download_one,
                item,
                config,
                item.path.exists(),
            ): index
    