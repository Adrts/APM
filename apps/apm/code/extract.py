"""Archive extraction, dispatched by file extension (Scoop compatible)."""

from __future__ import annotations

import bz2
import gzip
import lzma
import os
import re
import shutil
import stat
import tarfile
import uuid
import zipfile
from pathlib import Path
from typing import Any, Callable

from . import tools
from .errors import ExtractError
from .manifest import url_extension
from .output import console
from .util import (
    ensure_dir,
    is_reparse_dir,
    lp,
    path_is_within,
    remove_path,
    safe_child,
)

ARCHIVE_EXTENSIONS = {
    ".zip",
    ".nupkg",
    ".7z",
    ".rar",
    ".iso",
    ".img",
    ".lzma",
    ".lzh",
    ".001",
    ".tar",
    ".tar.gz",
    ".tar.bz2",
    ".tar.xz",
    ".tar.zst",
    ".tgz",
    ".tbz2",
    ".txz",
    ".gz",
    ".bz2",
    ".xz",
    ".zst",
    ".zstd",
    ".msi",
}

TAR_SUFFIXES = (".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst", ".tgz", ".tbz2", ".txz")
RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL"} | {
    f"COM{index}" for index in range(1, 10)
} | {f"LPT{index}" for index in range(1, 10)}
INVALID_CHARS = set('<>:"|?*')


def needs_extraction(url: str, innosetup: bool = False) -> bool:
    extension = url_extension(url)
    if extension in ARCHIVE_EXTENSIONS:
        return True
    return extension == ".exe" and innosetup


def _check_name(name: str) -> Path:
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        raise ExtractError(f"拒绝绝对路径条目：{name}")
    parts: list[str] = []
    for part in normalized.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise ExtractError(f"拒绝越界路径条目：{name}")
        if any(char in INVALID_CHARS for char in part):
            raise ExtractError(f"路径包含非法字符：{name}")
        if part.endswith((" ", ".")):
            raise ExtractError(f"路径以空格或点结尾：{name}")
        if part.split(".")[0].upper() in RESERVED_NAMES:
            raise ExtractError(f"路径包含 Windows 保留名：{name}")
        parts.append(part)
    if not parts:
        raise ExtractError(f"空路径条目：{name}")
    return Path(*parts)


def _target(dest: Path, name: str) -> Path:
    result = dest.joinpath(_check_name(name))
    if not path_is_within(dest, result):
        raise ExtractError(f"路径越界：{name}")
    return result


def _merge_move(source: Path, destination: Path) -> None:
    for child in list(source.iterdir()):
        target = destination / child.name
        if child.is_dir() and not is_reparse_dir(child):
            if target.is_dir() and not is_reparse_dir(target):
                _merge_move(child, target)
                continue
        if target.exists() or os.path.lexists(target):
            remove_path(target)
        shutil.move(lp(child), lp(target))


def _zip_entry_name(info: zipfile.ZipInfo) -> str:
    if info.flag_bits & 0x800:
        return info.filename
    try:
        raw = info.filename.encode("cp437")
    except UnicodeEncodeError:
        return info.filename
    try:
        return raw.decode("gbk")
    except UnicodeDecodeError:
        return info.filename


def _extract_zip(archive: Path, dest: Path) -> None:
    ensure_dir(dest)
    with zipfile.ZipFile(lp(archive)) as zf:
        for info in zf.infolist():
            name = _zip_entry_name(info)
            if not name:
                continue
            mode = (info.external_attr >> 16) & 0xFFFF
            if name.endswith("/") or stat.S_ISDIR(mode):
                directory = _target(dest, name)
                ensure_dir(directory)
                continue
            target = _target(dest, name)
            ensure_dir(target.parent)
            if stat.S_ISLNK(mode):
                link_name = zf.read(info).decode("utf-8", errors="replace")
                link_target = (target.parent / link_name).resolve()
                if os.path.isabs(link_name) or not path_is_within(dest, link_target):
                    raise ExtractError(f"拒绝越界符号链接：{name} -> {link_name}")
                try:
                    os.symlink(link_name, target)
                except OSError as exc:
                    raise ExtractError(f"无法创建符号链接：{name}（{exc}）") from exc
                continue
            with zf.open(info) as source, open(lp(target), "wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)


def _extract_zip_isolated(archive: Path, dest: Path) -> None:
    staging = dest / f"_tmp_zip_{uuid.uuid4().hex[:8]}"
    remove_path(staging)
    ensure_dir(staging)
    try:
        _extract_zip(archive, staging)
        _merge_move(staging, dest)
    finally:
        remove_path(staging)


def _safe_tar_members(tf: tarfile.TarFile, dest: Path) -> list[tarfile.TarInfo]:
    members: list[tarfile.TarInfo] = []
    for member in tf.getmembers():
        target = _target(dest, member.name)
        if member.ischr() or member.isblk() or member.isfifo():
            continue
        if member.issym():
            link = member.linkname
            link_target = (target.parent / link).resolve()
            if os.path.isabs(link) or not path_is_within(dest, link_target):
                raise ExtractError(f"拒绝越界链接：{member.name} -> {link}")
        elif member.islnk():
            source = dest / _check_name(member.linkname)
            if not path_is_within(dest, source):
                raise ExtractError(f"拒绝越界硬链接：{member.name} -> {member.linkname}")
        members.append(member)
    return members


def _extract_tar(archive: Path, dest: Path) -> None:
    staging = dest / f"_tmp_tar_{uuid.uuid4().hex[:8]}"
    remove_path(staging)
    ensure_dir(staging)
    ensure_dir(dest)
    try:
        with tarfile.open(lp(archive)) as tf:
            for member in _safe_tar_members(tf, staging):
                target = _target(staging, member.name)
                if member.isdir():
                    ensure_dir(target)
                    continue
                ensure_dir(target.parent)
                if member.isreg():
                    source = tf.extractfile(member)
                    if source is None:
                        raise ExtractError(f"无法读取归档条目：{member.name}")
                    with source, open(lp(target), "wb") as output:
                        shutil.copyfileobj(source, output, length=1024 * 1024)
                elif member.issym():
                    try:
                        os.symlink(member.linkname, target)
                    except OSError as exc:
                        raise ExtractError(
                            f"无法创建符号链接：{member.name}（{exc}）"
                        ) from exc
                elif member.islnk():
                    source = staging / _check_name(member.linkname)
                    if not source.exists():
                        raise ExtractError(f"硬链接源不存在：{member.linkname}")
                    os.link(lp(source), lp(target))
        _merge_move(staging, dest)
    finally:
        remove_path(staging)


def _extract_single(archive: Path, dest: Path) -> None:
    name = archive.name.lower()
    if name.endswith(".gz"):
        opener: Callable[..., Any] = gzip.open
        output_name = archive.name[:-3]
    elif name.endswith(".bz2"):
        opener = bz2.open
        output_name = archive.name[:-4]
    elif name.endswith(".xz"):
        opener = lzma.open
        output_name = archive.name[:-3]
    else:
        raise ExtractError(f"不支持的压缩格式：{archive.name}")
    ensure_dir(dest)
    output = dest / (output_name or "output")
    tmp = output.with_name(output.name + ".tmp")
    try:
        with opener(lp(archive), "rb") as source, open(lp(tmp), "wb") as target:
            shutil.copyfileobj(source, target, length=1024 * 1024)
        remove_path(output)
        os.replace(lp(tmp), lp(output))
    finally:
        if tmp.exists():
            remove_path(tmp)


def _seven_zip_extract(archive: Path, dest: Path, seven_zip: Path) -> None:
    ensure_dir(dest)
    cmd = [
        str(seven_zip),
        "x",
        str(archive),
        f"-o{dest}",
        "-y",
        "-bso0",
        "-bsp0",
    ]
    proc = tools.run_tool(cmd)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise ExtractError(
            f"7-Zip 解压失败：{archive.name}（退出码 {proc.returncode}）\n{detail}"
        )


def _seven_zip_tar(archive: Path, dest: Path, seven_zip: Path) -> None:
    staging = dest / f"_tmp_7z_{uuid.uuid4().hex[:8]}"
    remove_path(staging)
    ensure_dir(staging)
    try:
        _seven_zip_extract(archive, staging, seven_zip)
        inner = [path for path in staging.iterdir() if path.suffix.lower() == ".tar"]
        if not inner:
            _merge_move(staging, dest)
            return
        for tar_path in inner:
            _seven_zip_extract(tar_path, dest, seven_zip)
    finally:
        remove_path(staging)


def _extract_msi(archive: Path, dest: Path) -> None:
    staging = dest / f"_tmp_msi_{uuid.uuid4().hex[:8]}"
    remove_path(staging)
    ensure_dir(staging)
    try:
        proc = tools.run_tool(
            ["msiexec", "/a", str(archive), "/qn", f"TARGETDIR={staging}"]
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise ExtractError(
                f"MSI 解包失败：{archive.name}（退出码 {proc.returncode}）\n{detail}"
            )
        source_dir = staging / "SourceDir"
        _merge_move(source_dir if source_dir.is_dir() else staging, dest)
    finally:
        remove_path(staging)


def _extract_inno(archive: Path, dest: Path, innounp: Path) -> None:
    ensure_dir(dest)
    proc = tools.run_tool(
        [str(innounp), "-x", "-q", f"-d{dest}", str(archive)]
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise ExtractError(
            f"innounp 解包失败：{archive.name}（退出码 {proc.returncode}）\n{detail}"
        )


def _dispatch(
    archive: Path,
    dest: Path,
    innosetup: bool,
    config: dict[str, Any],
) -> None:
    name = archive.name.lower()
    extension = Path(name).suffix.lower()

    if extension in (".zip", ".nupkg"):
        seven_zip = tools.find_7z(config)
        if seven_zip:
            _seven_zip_extract(archive, dest, seven_zip)
        else:
            _extract_zip_isolated(archive, dest)
        return

    if extension in (".7z", ".rar", ".iso", ".img", ".lzma", ".lzh", ".001", ".zst", ".zstd"):
        if extension == ".001":
            parts = list(archive.parent.glob(archive.stem + ".???"))
            if len(parts) < 1:
                raise ExtractError(f"分卷压缩包缺少分卷：{archive.name}")
        seven_zip = tools.require_7z(config)
        if name.endswith(".tar.zst"):
            _seven_zip_tar(archive, dest, seven_zip)
        else:
            _seven_zip_extract(archive, dest, seven_zip)
        return

    if name.endswith(TAR_SUFFIXES) or extension == ".tar":
        try:
            _extract_tar(archive, dest)
        except Exception:
            seven_zip = tools.find_7z(config)
            if not seven_zip:
                raise
            console.warn(f"标准库解压失败，改用 7-Zip：{archive.name}")
            _seven_zip_tar(archive, dest, seven_zip)
        return

    if extension in (".gz", ".bz2", ".xz"):
        try:
            _extract_single(archive, dest)
        except Exception:
            seven_zip = tools.find_7z(config)
            if not seven_zip:
                raise
            console.warn(f"标准库解压失败，改用 7-Zip：{archive.name}")
            _seven_zip_extract(archive, dest, seven_zip)
        return

    if extension == ".msi":
        _extract_msi(archive, dest)
        return

    if extension == ".exe" and innosetup:
        _extract_inno(archive, dest, tools.require_innounp(config))
        return


def extract_package(
    archive: Path,
    dest: Path,
    extract_dir: str | None,
    innosetup: bool,
    config: dict[str, Any],
) -> None:
    """Extract one package into dest, applying extract_dir when present."""
    ensure_dir(dest)
    if extract_dir:
        staging = dest / "_tmp"
        remove_path(staging)
        ensure_dir(staging)
        try:
            _dispatch(archive, staging, innosetup, config)
            source = safe_child(staging, *Path(extract_dir.replace("\\", "/")).parts)
            if not source.exists():
                raise ExtractError(f"extract_dir 不存在：{extract_dir}")
            _merge_move(source, dest)
        finally:
            remove_path(staging)
        return
    _dispatch(archive, dest, innosetup, config)
