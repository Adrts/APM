"""External tool discovery (7-Zip, innounp, download backends)."""

from __future__ import annotations

import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable

from . import paths
from .errors import ToolError
from .util import run

PERCENT_RE = re.compile(r"(\d{1,3})%")


def _configured_path(value: Any) -> Path | None:
    text = str(value or "").strip()
    return Path(text).expanduser() if text else None


def resolve_tool(explicit: Any, exe_name: str, app_name: str) -> Path | None:
    configured = _configured_path(explicit)
    if configured and configured.is_file():
        return configured
    found = shutil.which(exe_name)
    if found:
        return Path(found)
    bundled = paths.APPS_DIR / app_name / "current" / exe_name
    if bundled.is_file():
        return bundled
    return None


def find_7z(config: dict[str, Any], section: str = "extract") -> Path | None:
    explicit = config.get(section, {}).get("seven_zip")
    return resolve_tool(explicit, "7z.exe", "7zip")


def require_7z(config: dict[str, Any], section: str = "extract") -> Path:
    tool = find_7z(config, section)
    if not tool:
        raise ToolError(
            "未检测到 7z.exe，请安装 7-Zip 并在配置中设置 "
            f"{section}.seven_zip，或运行 apm install 7zip"
        )
    return tool


def find_innounp(config: dict[str, Any]) -> Path | None:
    explicit = config.get("extract", {}).get("innounp")
    return resolve_tool(explicit, "innounp.exe", "innounp")


def require_innounp(config: dict[str, Any]) -> Path:
    tool = find_innounp(config)
    if not tool:
        raise ToolError(
            "未检测到 innounp.exe，请安装 innounp 并在配置中设置 extract.innounp"
        )
    return tool


def find_backend(name: str) -> Path | None:
    exe = {"aria2": "aria2c.exe", "curl": "curl.exe", "wget": "wget.exe"}.get(name)
    if not exe:
        return None
    found = shutil.which(exe)
    return Path(found) if found else None


def run_tool(cmd: list[str], cwd: Path | None = None):
    return run([str(part) for part in cmd], cwd=cwd)


def run_tool_streamed(
    cmd: list[str],
    cwd: Path | None = None,
    parse_stream: str = "stderr",
    on_percent: Callable[[int], None] | None = None,
) -> subprocess.CompletedProcess:
    """Run a tool while parsing ``NN%`` from one output stream.

    The parsed stream is consumed incrementally (progress tools rewrite the
    same line with ``\\r``), the other stream is drained in a background
    thread. Returns a CompletedProcess compatible with ``run_tool``.
    """
    argv = [str(part) for part in cmd]
    proc = subprocess.Popen(
        argv,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    chunks: dict[str, list[bytes]] = {"stdout": [], "stderr": []}

    def drain(name: str, stream) -> None:
        try:
            for chunk in iter(lambda: stream.read(8192), b""):
                chunks[name].append(chunk)
        finally:
            stream.close()

    other = "stdout" if parse_stream == "stderr" else "stderr"
    reader = threading.Thread(
        target=drain, args=(other, proc.stderr if other == "stderr" else proc.stdout)
    )
    reader.start()

    stream = proc.stderr if parse_stream == "stderr" else proc.stdout
    tail = b""
    try:
        while True:
            chunk = stream.read(8192)
            if not chunk:
                break
            chunks[parse_stream].append(chunk)
            if on_percent is not None:
                tail = (tail + chunk)[-256:]
                matches = PERCENT_RE.findall(tail.decode("utf-8", errors="replace"))
                if matches:
                    try:
                        on_percent(int(matches[-1]))
                    except Exception:
                        pass
        stream.close()
    finally:
        reader.join()
    return subprocess.CompletedProcess(
        argv,
        proc.wait(),
        stdout=b"".join(chunks["stdout"]).decode("utf-8", errors="replace"),
        stderr=b"".join(chunks["stderr"]).decode("utf-8", errors="replace"),
    )
