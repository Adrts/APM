"""Small filesystem and process helpers."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import struct
import subprocess
import time
import unicodedata
from pathlib import Path
from typing import Any, Iterable

from . import messages as msg
from .errors import ApmError

FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def lp(path: os.PathLike[str] | str) -> str:
    """Return a path usable with very long file names on Windows."""
    raw = os.path.abspath(os.fspath(path))
    if os.name != "nt" or len(raw) < 240:
        return raw
    if raw.startswith("\\\\?\\"):
        return raw
    if raw.startswith("\\\\"):
        return "\\\\?\\UNC\\" + raw[2:]
    return "\\\\?\\" + raw


def ensure_dir(path: os.PathLike[str] | str) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def sanitary_name(value: str) -> str:
    """Apply Scoop's sanitary_path rules to an app name."""
    cleaned = re.sub(r"[/\\?:*<>|]", "", value)
    return cleaned.strip().rstrip(". ") or cleaned


def is_reparse_dir(path: os.PathLike[str] | str) -> bool:
    raw = lp(path)
    if os.path.islink(raw):
        return True
    try:
        st = os.lstat(raw)
    except OSError:
        return False
    if not stat.S_ISDIR(st.st_mode):
        return False
    isjunction = getattr(os.path, "isjunction", None)
    if isjunction is not None and isjunction(raw):
        return True
    return bool(getattr(st, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT)


def _rmtree_no_follow(path: str) -> None:
    try:
        os.chmod(path, stat.S_IWRITE)
    except OSError:
        pass
    with os.scandir(path) as entries:
        children = list(entries)
    for entry in children:
        child = entry.path
        if entry.is_symlink():
            try:
                os.rmdir(child)
            except OSError:
                try:
                    os.unlink(child)
                except OSError:
                    pass
            continue
        if entry.is_dir(follow_symlinks=False):
            if is_reparse_dir(child):
                try:
                    os.rmdir(child)
                except OSError:
                    pass
            else:
                _rmtree_no_follow(child)
        else:
            try:
                os.chmod(child, stat.S_IWRITE)
            except OSError:
                pass
            try:
                os.unlink(child)
            except OSError:
                pass
    try:
        os.rmdir(path)
    except OSError:
        pass


def remove_path(path: os.PathLike[str] | str) -> None:
    """Remove a file, symlink, junction or directory tree safely.

    Junctions are removed as links only; their targets are never traversed.
    """
    if not os.path.lexists(path):
        return
    target = lp(path)
    try:
        if is_reparse_dir(path):
            try:
                os.rmdir(target)
            except OSError:
                os.unlink(target)
        elif os.path.isdir(path):
            try:
                os.chmod(target, stat.S_IWRITE)
            except OSError:
                pass
            _rmtree_no_follow(target)
        else:
            try:
                os.chmod(target, stat.S_IWRITE)
            except OSError:
                pass
            os.unlink(target)
    except FileNotFoundError:
        pass


def _create_junction_native(link: Path, target: Path) -> None:
    """Create an NTFS directory junction without external tools."""
    from ctypes import wintypes

    GENERIC_WRITE = 0x40000000
    FILE_SHARE_READ = 0x00000001
    FILE_SHARE_WRITE = 0x00000002
    FILE_SHARE_DELETE = 0x00000004
    OPEN_EXISTING = 3
    FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    FSCTL_SET_REPARSE_POINT = 0x000900A4
    IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003

    absolute_target = os.path.abspath(target)
    if absolute_target.startswith("\\\\"):
        substitute = "\\??\\UNC\\" + absolute_target[2:]
    else:
        substitute = "\\??\\" + absolute_target
    substitute_bytes = substitute.encode("utf-16-le")
    print_bytes = absolute_target.encode("utf-16-le")
    path_buffer = substitute_bytes + b"\x00\x00" + print_bytes + b"\x00\x00"

    buffer = ctypes.create_string_buffer(16 + len(path_buffer))
    struct.pack_into(
        "<IHH", buffer, 0, IO_REPARSE_TAG_MOUNT_POINT, 8 + len(path_buffer), 0
    )
    struct.pack_into(
        "<HHHH",
        buffer,
        8,
        0,
        len(substitute_bytes),
        len(substitute_bytes) + 2,
        len(print_bytes),
    )
    buffer[16 : 16 + len(path_buffer)] = path_buffer

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.DeviceIoControl.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    kernel32.DeviceIoControl.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    handle = kernel32.CreateFileW(
        lp(link),
        GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        None,
        OPEN_EXISTING,
        FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if handle in (None, invalid_handle):
        raise OSError(ctypes.get_last_error(), "CreateFileW failed")
    try:
        returned = wintypes.DWORD(0)
        ok = kernel32.DeviceIoControl(
            handle,
            FSCTL_SET_REPARSE_POINT,
            buffer,
            len(buffer),
            None,
            0,
            ctypes.byref(returned),
            None,
        )
        if not ok:
            raise OSError(ctypes.get_last_error(), "FSCTL_SET_REPARSE_POINT failed")
    finally:
        kernel32.CloseHandle(handle)


def create_junction(link: os.PathLike[str] | str, target: os.PathLike[str] | str) -> None:
    link_path = Path(link)
    target_path = Path(target)
    try:
        os.makedirs(lp(link_path.parent), exist_ok=True)
    except OSError as exc:
        raise ApmError(
            msg.ERR_JUNCTION_PARENT.format(
                path=link_path.parent, error=exc
            )
        ) from exc
    remove_path(link_path)
    if not os.path.exists(lp(target_path)):
        raise ApmError(msg.ERR_JUNCTION_TARGET.format(path=target_path))
    if os.name == "nt":
        try:
            os.makedirs(lp(link_path), exist_ok=True)
            _create_junction_native(link_path, target_path)
            return
        except Exception:
            remove_path(link_path)
    proc = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link_path), str(target_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise ApmError(
            msg.ERR_JUNCTION_CREATE.format(
                link=link_path,
                target=target_path,
                detail=f"{proc.stdout}{proc.stderr}".strip(),
            )
        )


def create_hardlink(link: os.PathLike[str] | str, target: os.PathLike[str] | str) -> None:
    link_path = Path(link)
    try:
        os.makedirs(lp(link_path.parent), exist_ok=True)
    except OSError as exc:
        raise ApmError(
            msg.ERR_HARDLINK_PARENT.format(
                path=link_path.parent, error=exc
            )
        ) from exc
    remove_path(link_path)
    try:
        os.link(lp(target), lp(link_path))
    except OSError as exc:
        raise ApmError(
            msg.ERR_HARDLINK.format(
                link=link_path, target=target, error=exc
            )
        ) from exc


def path_is_within(base: os.PathLike[str] | str, target: os.PathLike[str] | str) -> bool:
    try:
        base_abs = os.path.normcase(os.path.abspath(base))
        target_abs = os.path.normcase(os.path.abspath(target))
        return os.path.commonpath([base_abs, target_abs]) == base_abs
    except ValueError:
        return False


def safe_child(base: os.PathLike[str] | str, *parts: str) -> Path:
    """Join parts under base and reject absolute paths / traversal."""
    base_path = Path(base)
    for part in parts:
        if not part:
            continue
        candidate = Path(part)
        if candidate.is_absolute() or candidate.drive or candidate.root:
            raise ApmError(msg.ERR_PATH_ABSOLUTE.format(part=part))
        if ".." in candidate.parts:
            raise ApmError(msg.ERR_PATH_TRAVERSAL.format(part=part))
    result = base_path.joinpath(*[p for p in parts if p])
    if not path_is_within(base_path, result):
        raise ApmError(msg.ERR_PATH_OUTSIDE.format(path=result))
    return result


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def human_size(size: float) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def terminal_columns() -> int:
    """Return the current terminal width (falls back to 80 when redirected)."""
    return shutil.get_terminal_size().columns


def display_width(text: str) -> int:
    """Console display width; East-Asian wide/ambiguous chars count as 2 cells."""
    return sum(
        2 if unicodedata.east_asian_width(char) in ("F", "W", "A") else 1
        for char in text
    )


def pad_text(text: object, width: int, align: str = "left") -> str:
    """Pad text to a console display width so CJK and ASCII columns line up."""
    value = str(text)
    gap = " " * max(0, width - display_width(value))
    return gap + value if align == "right" else value + gap


def fit_text(text: str, width: int) -> str:
    """Truncate text to the console display width so the line never wraps."""
    if width <= 0:
        return ""
    chars: list[str] = []
    used = 0
    for char in text:
        char_width = 2 if unicodedata.east_asian_width(char) in ("F", "W", "A") else 1
        if used + char_width > width:
            break
        chars.append(char)
        used += char_width
    return "".join(chars)


def read_json(path: os.PathLike[str] | str) -> Any:
    with open(lp(path), "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def write_json(path: os.PathLike[str] | str, data: Any) -> None:
    p = Path(path)
    ensure_dir(p.parent)
    tmp = p.with_name(p.name + ".tmp")
    try:
        with open(lp(tmp), "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(lp(tmp), lp(p))
    except OSError as exc:
        raise ApmError(msg.ERR_WRITE_FILE.format(path=p, error=exc)) from exc


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def run(
    cmd: Iterable[str],
    cwd: os.PathLike[str] | str | None = None,
    capture: bool = True,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(cmd),
        cwd=str(cwd) if cwd else None,
        capture_output=capture,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def split_windows_args(args: str) -> list[str]:
    """Split a Windows command line the same way CommandLineToArgvW does."""
    if not args:
        return []
    if os.name != "nt":
        return shlex.split(args)

    from ctypes import wintypes

    argc = ctypes.c_int()
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.CommandLineToArgvW.argtypes = [
        wintypes.LPCWSTR,
        ctypes.POINTER(ctypes.c_int),
    ]
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    argv = shell32.CommandLineToArgvW(args, ctypes.byref(argc))
    if not argv:
        return shlex.split(args)
    try:
        return [argv[i] for i in range(argc.value)]
    finally:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        kernel32.LocalFree(ctypes.cast(argv, ctypes.c_void_p))
