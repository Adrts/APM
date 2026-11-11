"""External tool discovery (7-Zip, innounp, download backends)."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from . import messages as msg, paths
from .errors import ToolError
from .util import run


def _configured_path(value: Any) -> Path | None:
    text = str(value or "").strip()
    return Path(text).expanduser() if text else None


def find_executable(exe_name: str) -> Path | None:
    """优先返回 APM 生成的命令入口，其次从 PATH 查找。"""
    shim = paths.SHIM_DIR / exe_name
    if shim.is_file():
        return shim
    found = shutil.which(exe_name)
    return Path(found) if found else None


def resolve_tool(explicit: Any, exe_name: str) -> Path | None:
    configured = _configured_path(explicit)
    if configured:
        if configured.is_file():
            return configured
        raise ToolError(
            msg.ERR_TOOL_CONFIGURED_MISSING.format(
                name=exe_name, path=configured
            )
        )
    return find_executable(exe_name)


def find_7z(config: dict[str, Any], section: str = "extract") -> Path | None:
    explicit = config.get(section, {}).get("seven_zip")
    return resolve_tool(explicit, "7z.exe")


def require_7z(config: dict[str, Any], section: str = "extract") -> Path:
    tool = find_7z(config, section)
    if not tool:
        raise ToolError(msg.ERR_7Z_MISSING.format(section=section))
    return tool


def find_innounp(config: dict[str, Any]) -> Path | None:
    explicit = config.get("extract", {}).get("innounp")
    return resolve_tool(explicit, "innounp.exe")


def require_innounp(config: dict[str, Any]) -> Path:
    tool = find_innounp(config)
    if not tool:
        raise ToolError(msg.ERR_INNOUNP_MISSING)
    return tool


BACKEND_EXES = {
    "aria2": "aria2c.exe",
    "curl": "curl.exe",
    "wget": "wget.exe",
}


def find_backend(name: str) -> Path | None:
    exe = BACKEND_EXES.get(name)
    if not exe:
        return None
    return find_executable(exe)


def run_tool(cmd: list[str], cwd: Path | None = None):
    return run([str(part) for part in cmd], cwd=cwd)
