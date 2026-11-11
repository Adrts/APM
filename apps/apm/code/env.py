"""User-level environment variables and PATH (HKCU\\Environment)."""

from __future__ import annotations

import ctypes
import os
from typing import Iterable

from .errors import ApmError

try:
    import winreg
except ImportError:  # pragma: no cover - APM targets Windows
    winreg = None  # type: ignore[assignment]

ENV_KEY = r"Environment"


def _require_registry():
    if winreg is None:
        raise ApmError("环境变量操作仅在 Windows 上可用")


def _read_value(name: str) -> tuple[str | None, int | None]:
    _require_registry()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, ENV_KEY, 0, winreg.KEY_READ) as key:
            value, value_type = winreg.QueryValueEx(key, name)
            return str(value), int(value_type)
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        raise ApmError(f"读取环境变量 {name} 失败：{exc}") from exc


def _write_value(name: str, value: str, value_type: int) -> None:
    _require_registry()
    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, ENV_KEY, 0, winreg.KEY_WRITE
        ) as key:
            winreg.SetValueEx(key, name, 0, value_type, value)
    except OSError as exc:
        raise ApmError(f"写入环境变量 {name} 失败：{exc}") from exc


def _delete_value(name: str) -> None:
    _require_registry()
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, ENV_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, name)
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise ApmError(f"删除环境变量 {name} 失败：{exc}") from exc


def _split_path(value: str) -> list[str]:
    return [part for part in value.split(";") if part.strip()]


def _normalize(entry: str) -> str:
    return entry.strip().strip('"').rstrip("\\").lower()


def _broadcast() -> None:
    if os.name != "nt":
        return
    try:
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.SendMessageTimeoutW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
            wintypes.UINT,
            wintypes.UINT,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        user32.SendMessageTimeoutW.restype = ctypes.c_size_t
        result = ctypes.c_size_t()
        user32.SendMessageTimeoutW(
            0xFFFF,
            0x001A,
            0,
            ctypes.cast(ctypes.c_wchar_p("Environment"), ctypes.c_void_p).value,
            0x0002,
            5000,
            ctypes.byref(result),
        )
    except Exception:
        pass


def read_path() -> tuple[str, int | None]:
    value, value_type = _read_value("Path")
    return value or "", value_type


def add_path(entries: Iterable[str]) -> None:
    _require_registry()
    new_entries = [str(entry) for entry in entries if str(entry).strip()]
    if not new_entries:
        return
    current, current_type = read_path()
    existing = _split_path(current)
    keys = {_normalize(item) for item in existing}
    additions: list[str] = []
    for entry in reversed(new_entries):
        key = _normalize(entry)
        if key not in keys:
            keys.add(key)
            additions.insert(0, entry)
    if not additions:
        return
    combined = additions + existing
    value = ";".join(combined)
    value_type = current_type or (
        winreg.REG_EXPAND_SZ if "%" in value else winreg.REG_SZ
    )
    _write_value("Path", value, value_type)
    process_parts = _split_path(os.environ.get("PATH", ""))
    process_keys = {_normalize(item) for item in process_parts}
    prefix = [item for item in additions if _normalize(item) not in process_keys]
    os.environ["PATH"] = ";".join(prefix + process_parts)
    _broadcast()


def remove_path(entries: Iterable[str]) -> None:
    _require_registry()
    targets = {_normalize(str(entry)) for entry in entries if str(entry).strip()}
    if not targets:
        return
    current, current_type = read_path()
    if not current:
        return
    parts = _split_path(current)
    remaining = [item for item in parts if _normalize(item) not in targets]
    if len(remaining) == len(parts):
        return
    value = ";".join(remaining)
    value_type = current_type or (
        winreg.REG_EXPAND_SZ if "%" in value else winreg.REG_SZ
    )
    _write_value("Path", value, value_type)
    process_parts = _split_path(os.environ.get("PATH", ""))
    os.environ["PATH"] = ";".join(
        item for item in process_parts if _normalize(item) not in targets
    )
    _broadcast()


def set_variables(values: dict[str, str]) -> None:
    if not values:
        return
    _require_registry()
    for name, value in values.items():
        value_type = winreg.REG_EXPAND_SZ if "%" in value else winreg.REG_SZ
        _write_value(name, value, value_type)
        os.environ[name] = value
    _broadcast()


def remove_variables(names: Iterable[str]) -> None:
    changed = False
    for name in names:
        if not name:
            continue
        _delete_value(name)
        os.environ.pop(name, None)
        changed = True
    if changed:
        _broadcast()
