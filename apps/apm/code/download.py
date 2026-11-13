"""Two-phase download layer: cache, mirrors, backends and verification."""

from __future__ import annotations

import os
import subprocess
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urldefrag, urlparse

from . import cache, messages as msg, paths, tools, verify
from .errors import DownloadError
from .manifest import AppRef
from .output import Bar, console
from .util import ensure_dir, human_size, lp, remove_path

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
        msg.MSG_MIRROR_NOT_CONFIGURED.format(
            provider=provider, config=paths.CONFIG_FILE
        )
    )


def transport_url(url: str, config: dict[str, Any]) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return url
    host = (parsed.hostname or "").lower()
    lowered = url.lower()
    mirror = config.get("download", {}).get("mirror", {})
    is_github = (
        "github.com" in host
        or "github.com" in lowered
        or "githubusercontent.com" in host
        or "githubusercontent.com" in lowered
    )
    if is_github:
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


class _Interrupted(Exception):
    """Ctrl+C 协作中断信号：worker 线程据此快速退出，不产生任何输出。"""


def _request_url(url: str) -> str:
    return urldefrag(url)[0]


class DownloadBackend:
    """下载后端统一接口（对应 13.11 流程图的各下载分支封装）。

    四个后端（python / curl / wget / aria2）的方法签名与传出完全一致：

    - ``staging_path(dest)``：本次下载的工作路径（python/curl/wget 为
      ``缓存名.download``，aria2 为正式缓存名本身）；
    - ``run(url, work, cookie)``：把字节写入工作路径，失败抛出
      ``DownloadError``，无返回值；Ctrl+C 中断时抛出 ``_Interrupted``；
    - ``commit(work, dest)``：哈希校验通过后固化为正式缓存（重命名 /
      清理 aria2 控制文件）；
    - ``rollback(work, dest)``：失败清理本次产生的文件；
    - ``progress()``：返回 ``(已完成字节, 总字节)``。不支持获取进度的
      后端直接沿用基类空实现，恒为 ``(0, 0)``。

    哈希校验、固化与失败清理的调用次序由 APM 统一执行，后端只负责
    字节落盘与自身的临时文件策略。
    """

    name = ""

    def __init__(self, config: dict[str, Any], stop: threading.Event) -> None:
        self.config = config
        self.stop = stop

    def staging_path(self, dest: Path) -> Path:
        raise NotImplementedError

    def run(self, url: str, work: Path, cookie: str | None) -> None:
        raise NotImplementedError

    def commit(self, work: Path, dest: Path) -> None:
        raise NotImplementedError

    def rollback(self, work: Path, dest: Path) -> None:
        raise NotImplementedError

    def progress(self) -> tuple[int, int]:
        # 不支持获取进度的后端恒为 (0, 0)。
        return (0, 0)


class TempFileBackend(DownloadBackend):
    """以 ``缓存名.download`` 为工作路径的后端家族（python / curl / wget）。"""

    def staging_path(self, dest: Path) -> Path:
        return dest.with_name(dest.name + ".download")

    def run(self, url: str, work: Path, cookie: str | None) -> None:
        raise NotImplementedError

    def commit(self, work: Path, dest: Path) -> None:
        os.replace(lp(work), lp(dest))

    def rollback(self, work: Path, dest: Path) -> None:
        remove_path(work)


class PythonBackend(TempFileBackend):
    """Python 标准库后端：唯一支持字节级进度回报的后端。"""

    name = "python"
    _done = 0
    _total = 0

    def run(self, url: str, work: Path, cookie: str | None) -> None:
        if work.exists():
            remove_path(work)
        headers = {"User-Agent": USER_AGENT}
        if cookie:
            headers["Cookie"] = cookie
        request = urllib.request.Request(_request_url(url), headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                self._done = 0
                self._total = int(response.headers.get("Content-Length") or 0)
                read = 0
                with open(lp(work), "wb") as fh:
                    while True:
                        if self.stop.is_set():
                            raise _Interrupted()
                        chunk = response.read(256 * 1024)
                        if not chunk:
                            break
                        fh.write(chunk)
                        read += len(chunk)
                        self._done = read
        except urllib.error.HTTPError as exc:
            raise DownloadError(msg.ERR_HTTP.format(code=exc.code, url=url)) from exc
        except urllib.error.URLError as exc:
            raise DownloadError(
                msg.ERR_NETWORK.format(reason=exc.reason, url=url)
            ) from exc
        except OSError as exc:
            if self.stop.is_set():
                raise _Interrupted() from exc
            raise DownloadError(msg.ERR_WRITE_FAILED.format(error=exc)) from exc

    def progress(self) -> tuple[int, int]:
        return (self._done, self._total)


class CurlBackend(TempFileBackend):
    name = "curl"

    def __init__(self, config: dict[str, Any], stop: threading.Event) -> None:
        super().__init__(config, stop)
        exe = tools.find_backend(self.name)
        if not exe:
            raise DownloadError(msg.ERR_CURL_MISSING)
        self.exe = exe

    def run(self, url: str, work: Path, cookie: str | None) -> None:
        if work.exists():
            remove_path(work)
        cmd = [
            str(self.exe),
            "-L",
            "--fail",
            "--retry",
            "0",
            "--silent",
            "--show-error",
            "--output",
            str(work),
            "--connect-timeout",
            "30",
            "--user-agent",
            USER_AGENT,
        ]
        if cookie:
            cmd.extend(["--header", f"Cookie: {cookie}"])
        cmd.append(_request_url(url))
        _run_backend(cmd, url, self.name, self.stop)


class WgetBackend(TempFileBackend):
    name = "wget"

    def __init__(self, config: dict[str, Any], stop: threading.Event) -> None:
        super().__init__(config, stop)
        exe = tools.find_backend(self.name)
        if not exe:
            raise DownloadError(msg.ERR_WGET_MISSING)
        self.exe = exe

    def run(self, url: str, work: Path, cookie: str | None) -> None:
        if work.exists():
            remove_path(work)
        cmd = [
            str(self.exe),
            "--no-verbose",
            "--output-document",
            str(work),
            "--timeout=60",
            "--tries=1",
            f"--user-agent={USER_AGENT}",
        ]
        if cookie:
            cmd.append(f"--header=Cookie: {cookie}")
        cmd.append(_request_url(url))
        _run_backend(cmd, url, self.name, self.stop)


class Aria2Backend(DownloadBackend):
    name = "aria2"

    def __init__(self, config: dict[str, Any], stop: threading.Event) -> None:
        super().__init__(config, stop)
        exe = tools.find_backend(self.name)
        if not exe:
            raise DownloadError(msg.ERR_ARIA2_MISSING)
        self.exe = exe
        self.segments = max(
            1,
            int(config.get("download", {}).get("concurrency", {}).get("segments", 5)),
        )

    def staging_path(self, dest: Path) -> Path:
        # aria2 直接下载到正式缓存名，靠 .aria2 控制文件管理断点。
        return dest

    def run(self, url: str, work: Path, cookie: str | None) -> None:
        if work.exists():
            remove_path(work)
        control = Path(str(work) + ".aria2")
        if control.exists():
            remove_path(control)
        cmd = [
            str(self.exe),
            "--dir",
            str(work.parent),
            "--out",
            work.name,
            "--split",
            str(self.segments),
            "--max-connection-per-server",
            str(self.segments),
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
        _run_backend(cmd, url, self.name, self.stop)

    def commit(self, work: Path, dest: Path) -> None:
        # work 即正式缓存名，仅需清理 aria2 控制文件。
        control = Path(str(work) + ".aria2")
        if control.exists():
            remove_path(control)

    def rollback(self, work: Path, dest: Path) -> None:
        _purge(work)


BACKEND_CLASSES: dict[str, type[DownloadBackend]] = {
    "python": PythonBackend,
    "curl": CurlBackend,
    "wget": WgetBackend,
    "aria2": Aria2Backend,
}


def _backend_name(config: dict[str, Any]) -> str:
    return str(config.get("download", {}).get("backend", "python"))


def _create_backend(config: dict[str, Any], stop: threading.Event) -> DownloadBackend:
    cls = BACKEND_CLASSES.get(_backend_name(config))
    if cls is None:
        raise DownloadError(
            msg.ERR_BACKEND_UNKNOWN.format(backend=_backend_name(config))
        )
    return cls(config, stop)


def _run_backend(
    cmd: list[str], url: str, backend: str, stop: threading.Event
) -> None:
    """运行外部下载器：轮询等待以便 Ctrl+C 时立即终止子进程。"""
    proc = subprocess.Popen(
        [str(part) for part in cmd],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    while True:
        if stop.is_set():
            proc.kill()
            try:
                proc.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
            raise _Interrupted()
        try:
            proc.communicate(timeout=0.2)
            break
        except subprocess.TimeoutExpired:
            continue
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        lowered = detail.lower()
        if backend == "wget" and (
            "certificate" in lowered or "unable to locally verify" in lowered
        ):
            raise DownloadError(msg.ERR_TLS_CERTIFICATE.format(detail=detail))
        raise DownloadError(
            msg.ERR_DOWNLOAD_EXIT.format(
                code=proc.returncode, url=url, detail=detail
            )
        )


def _poll_progress(
    backend: DownloadBackend, bar: Bar | None, stop: threading.Event
) -> None:
    """统一进度轮询：定期读取 backend.progress() 推进进度条。

    不支持获取进度的后端 progress() 恒为 (0, 0)，进度条保持弹跳动画。
    """
    while not stop.wait(0.2):
        if bar is not None and bar.finished:
            return
        try:
            done, total = backend.progress()
        except Exception:  # noqa: BLE001 - 轮询失败不影响下载
            return
        if done or total:
            console.bar_update(bar, done, total)


def _download_one(
    item: DownloadItem,
    config: dict[str, Any],
    cache_hit: bool,
    index: int,
    total: int,
    stop: threading.Event,
    bar: Bar | None = None,
) -> DownloadResult:
    cookie = _cookie_header(item.cookie)
    prefix = f"[{index}/{total}] "
    ensure_dir(item.path.parent)

    try:
        if cache_hit:
            console.info(f"{prefix}{msg.MSG_USE_CACHE.format(file=item.path.name)}")
            ok, message = verify.verify_file(item.path, item.hash, config, item.app)
            if ok:
                console.bar_remove(bar)
                return DownloadResult(item=item, ok=True)
            console.warn(
                msg.MSG_CACHE_INVALID.format(
                    file=item.path.name, detail=message or ""
                )
            )
            _purge(item.path)

        # 统一后端流程：staging_path → run → 校验 → commit（失败 rollback）。
        transport = transport_url(item.url, config)
        mirror = transport != item.url
        if bar is None:
            # 非 TTY / 窄终端：进度条回退为普通状态行。
            text = (
                msg.MSG_DOWNLOAD_MIRROR.format(file=item.path.name)
                if mirror
                else msg.MSG_DOWNLOAD.format(file=item.path.name)
            )
            console.info(f"{prefix}{text}")
        else:
            console.bar_run(bar)
        backend = _create_backend(config, stop)
        work = backend.staging_path(item.path)
        # 进度条覆盖下载与校验全程；python 后端经 progress() 实时推进，
        # 其余后端 progress() 恒为 (0, 0)，进度条保持弹跳动画模拟。
        poller = threading.Thread(
            target=_poll_progress, args=(backend, bar, stop), daemon=True
        )
        poller.start()
        try:
            backend.run(transport, work, cookie)
        finally:
            # 轮询线程在条定格后自行退出（见 _poll_progress）。
            poller.join(timeout=0.5)

        ok, message = verify.verify_file(work, item.hash, config, item.app)
        if not ok:
            backend.rollback(work, item.path)
            return DownloadResult(
                item=item, ok=False, error=message or msg.ERR_VERIFY_FAILED
            )
        backend.commit(work, item.path)

        size_text = human_size(item.path.stat().st_size)
        if bar is None:
            console.success(
                f"{prefix}{msg.MSG_DOWNLOADED.format(file=item.path.name)}"
            )
        console.bar_finish(bar, ok=True, size_text=size_text)
        return DownloadResult(item=item, ok=True)
    except _Interrupted:
        # Ctrl+C：静默退出，进度条块由取消路径统一擦除。
        _purge(item.path)
        return DownloadResult(item=item, ok=False, error=None)
    except Exception as exc:  # noqa: BLE001 - failures are reported per item
        if stop.is_set():
            _purge(item.path)
            return DownloadResult(item=item, ok=False, error=None)
        console.bar_finish(bar, ok=False)
        if backend is not None and work is not None:
            try:
                backend.rollback(work, item.path)
            except Exception:  # noqa: BLE001 - 清理失败不影响失败上报
                pass
        else:
            _purge(item.path)
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
    concurrency = max(
        1,
        int(config.get("download", {}).get("concurrency", {}).get("apps", 3)),
    )
    # Ctrl+C 协作中断：worker 下载循环与外部子进程轮询均检查 stop 事件。
    # 下载线程全部 daemon 化——主线程的 join 被 ^C 打断后不再等待任何
    # worker（可能阻塞在连接阶段，最长 60s 超时），进程立即结束，残留的
    # .download 临时文件由下次下载开头的清理逻辑回收。
    stop = threading.Event()
    semaphore = threading.Semaphore(concurrency)
    results: list[DownloadResult | None] = [None] * len(items)

    # 按编号顺序预分配进度条（等待中的条静止占位），保证块内顺序与编号一致。
    bars: list[Bar | None] = []
    for index, item in enumerate(items):
        mirror = transport_url(item.url, config) != item.url
        desc = item.path.name + (msg.MIRROR_SUFFIX if mirror else "")
        bars.append(
            console.bar_start(f"[{index + 1}/{len(items)}] {desc}", Bar.STYLE_DOWNLOAD, running=False)
        )

    def worker(index: int) -> None:
        while not stop.is_set():
            if semaphore.acquire(timeout=0.2):
                try:
                    results[index] = _download_one(
                        items[index],
                        config,
                        items[index].path.exists(),
                        index + 1,
                        len(items),
                        stop,
                        bars[index],
                    )
                finally:
                    semaphore.release()
                return
        results[index] = DownloadResult(item=items[index], ok=False, error=None)

    threads = [
        threading.Thread(target=worker, args=(index,), daemon=True)
        for index in range(len(items))
    ]
    for thread in threads:
        thread.start()
    try:
        for thread in threads:
            thread.join()
    except KeyboardInterrupt:
        stop.set()
        raise
    finally:
        console.bars_close(erase=stop.is_set())
    return list(results)
