"""Start menu shortcut creation via the WScript.Shell COM object."""

from __future__ import annotations

from pathlib import Path

from . import paths, ps
from .errors import ApmError
from .output import console
from .util import ensure_dir, path_is_within, remove_path


def _quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def shortcut_path(app: str, name: str) -> Path:
    relative = Path(name.replace("\\", "/"))
    if relative.is_absolute() or ".." in relative.parts:
        raise ApmError(f"快捷方式名称无效：{name}")
    filename = relative.name
    if not filename.lower().endswith(".lnk"):
        filename += ".lnk"
    base = paths.START_MENU_APPS / app
    result = base / relative.parent / filename
    if not path_is_within(base, result):
        raise ApmError(f"快捷方式路径越界：{name}")
    return result


def create(
    app: str,
    target: Path,
    name: str,
    arguments: str = "",
    icon: Path | None = None,
) -> bool:
    if not target.is_file():
        console.warn(f"创建快捷方式失败（目标不存在）：{target}")
        return False
    if icon is not None and not icon.is_file():
        console.warn(f"创建快捷方式失败（图标不存在）：{icon}")
        return False
    link = shortcut_path(app, name)
    ensure_dir(link.parent)
    lines = [
        "$ErrorActionPreference = 'Stop'",
        "$shell = New-Object -ComObject WScript.Shell",
        f"$link = $shell.CreateShortcut({_quote(str(link))})",
        f"$link.TargetPath = {_quote(str(target))}",
        f"$link.WorkingDirectory = {_quote(str(target.parent))}",
    ]
    if arguments:
        lines.append(f"$link.Arguments = {_quote(arguments)}")
    if icon is not None:
        lines.append(f"$link.IconLocation = {_quote(str(icon))}")
    lines.append("$link.Save()")
    proc = ps.run_script("\r\n".join(lines))
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        console.warn(f"创建快捷方式失败：{name}\n{detail}")
        return False
    console.info(f"创建快捷方式 {name}")
    return True


def remove(app: str, name: str) -> None:
    link = shortcut_path(app, name)
    if link.exists():
        remove_path(link)
    parent = link.parent
    base = paths.START_MENU_APPS / app
    while path_is_within(base, parent) and parent != base:
        try:
            if any(parent.iterdir()):
                break
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent
