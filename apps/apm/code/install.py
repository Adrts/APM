"""Two-phase installation and update engine."""

from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import cache, config as config_module, deps, download, env, extract, installed
from . import manifest, messages as msg, paths, ps, shortcuts
from .errors import ApmError, InstallError
from .manifest import AppRef
from .output import console
from .util import (
    create_hardlink,
    create_junction,
    ensure_dir,
    lp,
    now_iso,
    path_is_within,
    remove_path,
    write_json,
)


@dataclass
class Artifacts:
    bin: list[str] = field(default_factory=list)
    shortcuts: list[str] = field(default_factory=list)
    env_set: list[str] = field(default_factory=list)
    env_add_path: list[str] = field(default_factory=list)
    persist: list[list[str]] = field(default_factory=list)


@dataclass
class Materialized:
    filenames: list[str]
    copied: dict[str, Path]


def install_apps(
    specs: list[str],
    for_update: bool = False,
    download_only: bool = False,
) -> int:
    for spec in specs:
        if manifest.parse_app_spec(spec)[1].lower() == "apm":
            raise ApmError(msg.MSG_APM_RESERVED)
    refs = deps.resolve(specs, for_update=for_update or download_only)
    if not refs:
        console.info(msg.MSG_NOTHING_TO_PROCESS)
        return 0

    console.info(msg.MSG_PLAN.format(apps=" -> ".join(ref.app for ref in refs)))
    cfg = config_module.load()
    items = download.build_items(refs)
    if not items:
        console.info(msg.MSG_NOTHING_TO_DOWNLOAD)
        return 0
    results = download.download_all(items, cfg)
    failures = [result for result in results if not result.ok]
    if failures:
        console.failure_list(
            [
                msg.ERR_ITEM.format(name=result.item.app, error=result.error)
                for result in failures
            ]
        )
        console.error(msg.MSG_DOWNLOAD_STAGE_FAILED)
        return 1

    if download_only:
        for result in results:
            console.plain(str(result.item.path))
        console.success(msg.MSG_DOWNLOAD_VERIFIED)
        return 0

    by_path = {result.item.path: result for result in results}
    for ref in refs:
        try:
            _install_one(ref, cfg, by_path)
        except ApmError as exc:
            console.failure_list([msg.ERR_ITEM.format(name=ref.app, error=exc)])
            return 1
        except Exception as exc:  # noqa: BLE001
            console.failure_list([msg.ERR_ITEM.format(name=ref.app, error=exc)])
            return 1

    console.success(msg.MSG_ALL_INSTALLED)
    return 0


def update_apps(targets: list[str]) -> int:
    installed_apps = {item.app: item for item in installed.installed_apps()}
    if not installed_apps:
        console.info(msg.MSG_NO_INSTALLED)
        return 0

    if not targets:
        selected = list(installed_apps.values())
    else:
        selected = []
        for target in targets:
            _, app, _ = manifest.parse_app_spec(target)
            item = installed_apps.get(app)
            if item is None:
                raise ApmError(msg.MSG_APP_NOT_INSTALLED.format(app=app))
            selected.append(item)

    specs: list[str] = []
    errors = 0
    for item in selected:
        if item.hold:
            console.info(msg.MSG_APP_LOCKED_SKIP.format(app=item.app))
            continue
        try:
            ref = manifest.load_app(item.app, item.bucket)
        except ApmError as exc:
            console.warn(
                msg.MSG_MANIFEST_UNREADABLE.format(app=item.app, error=exc)
            )
            errors += 1
            continue
        if ref.version == item.version:
            console.info(
                msg.MSG_APP_LATEST.format(app=item.app, version=item.version)
            )
            continue
        console.info(
            msg.MSG_UPDATE_APP.format(
                app=item.app, old=item.version, new=ref.version
            )
        )
        specs.append(f"{ref.bucket}/{item.app}")
    if not specs:
        if errors:
            return 1
        console.success(msg.MSG_ALL_LATEST)
        return 0
    result = install_apps(specs, for_update=True)
    return 1 if errors else result


def download_apps(specs: list[str]) -> int:
    return install_apps(specs, for_update=True, download_only=True)


def _install_one(
    ref: AppRef,
    cfg: dict[str, Any],
    results_by_path: dict[Path, download.DownloadResult],
) -> None:
    app = ref.app
    version = ref.version
    if app.lower() == "apm":
        raise InstallError(msg.MSG_APM_RESERVED)
    if installed.is_installed(app) and installed.installed_version(app) == version:
        console.info(msg.MSG_APP_INSTALLED_SKIP.format(app=app, version=version))
        return

    app_root = paths.app_dir(app)
    version_path = paths.version_dir(app, version)
    current = paths.current_dir(app)
    persist_dir = paths.persist_app_dir(app)
    old_meta = installed.read_meta(app) or {}
    old_versions = [
        item for item in installed.version_dirs(app) if item.name != version
    ]

    remove_path(version_path)
    ensure_dir(version_path)

    artifacts = Artifacts()
    switched = False
    try:
        materialized = _materialize(ref, version_path, cfg, results_by_path)

        ps.run_hook(
            "pre_install",
            ref.field("pre_install"),
            app=app,
            version=version,
            bucket=ref.bucket,
            directory=version_path,
            original_dir=version_path,
            persist_dir=persist_dir,
            architecture=ref.architecture,
            fname=materialized.filenames,
            manifest_path=ref.path,
            cmd="install",
        )

        _run_installer(ref, version_path, materialized)

        console.info(msg.MSG_SWITCH_CURRENT.format(version=version))
        _switch_current(current, version_path)
        switched = True

        _create_bin_entries(ref, current, version_path, persist_dir, artifacts)
        _create_shortcuts(ref, current, version_path, persist_dir, artifacts)
        _apply_env(ref, current, version_path, persist_dir, artifacts)
        _apply_persist(ref, version_path, persist_dir, artifacts)

        ps.run_hook(
            "post_install",
            ref.field("post_install"),
            app=app,
            version=version,
            bucket=ref.bucket,
            directory=current,
            original_dir=version_path,
            persist_dir=persist_dir,
            architecture=ref.architecture,
            fname=materialized.filenames,
            manifest_path=ref.path,
            cmd="install",
        )

        _prune_old_versions(old_versions)
        _write_meta(ref, artifacts, old_meta)
        _show_notes(ref, current, version_path, persist_dir)
        console.success(msg.MSG_INSTALLED.format(app=app, version=version))
    except Exception as exc:
        if switched:
            try:
                _write_meta(ref, artifacts, old_meta)
            except Exception:
                pass
        else:
            remove_path(version_path)
            try:
                if app_root.is_dir() and not any(app_root.iterdir()):
                    app_root.rmdir()
            except OSError:
                pass
        if isinstance(exc, ApmError):
            raise
        raise InstallError(
            msg.MSG_INSTALL_FAILED.format(app=app, error=exc)
        ) from exc


def _switch_current(current: Path, version_path: Path) -> None:
    staging = current.with_name(".apm-current-new")
    backup = current.with_name(".apm-current-old")
    remove_path(staging)
    remove_path(backup)
    create_junction(staging, version_path)
    try:
        if os.path.lexists(current):
            os.rename(current, backup)
        os.rename(staging, current)
    except Exception:
        if not os.path.lexists(current) and os.path.lexists(backup):
            try:
                os.rename(backup, current)
            except OSError:
                pass
        remove_path(staging)
        raise
    remove_path(backup)


def _materialize(
    ref: AppRef,
    version_path: Path,
    cfg: dict[str, Any],
    results_by_path: dict[Path, download.DownloadResult],
) -> Materialized:
    packages = ref.packages()
    innosetup = bool(ref.field("innosetup"))
    if ref.field("extract_to"):
        console.warn(msg.MSG_EXTRACT_TO_UNSUPPORTED.format(app=ref.app))
    extract_values, is_list = manifest.extract_dir_values(ref)
    copied: dict[str, Path] = {}
    filenames: list[str] = []
    extracted_index = 0

    for package in packages:
        key = cache.cache_path(ref.app, ref.version, package.url)
        result = results_by_path.get(key)
        if result is None or not result.ok:
            raise InstallError(
                msg.ERR_MISSING_PACKAGE.format(url=package.url)
            )
        name = manifest.remote_filename(package.url)
        filenames.append(name)
        archive = result.item.path
        if extract.needs_extraction(package.url, innosetup):
            if is_list:
                extract_dir = (
                    extract_values[extracted_index]
                    if extracted_index < len(extract_values)
                    else None
                )
            else:
                extract_dir = extract_values[0] if extract_values else None
            console.info(msg.MSG_EXTRACTING.format(file=name))
            extract.extract_package(archive, version_path, extract_dir, innosetup, cfg)
            extracted_index += 1
        else:
            target = manifest.ensure_inside(version_path, name, "安装包")
            ensure_dir(target.parent)
            shutil.copy2(lp(archive), lp(target))
            copied[name] = target

    return Materialized(filenames=filenames, copied=copied)


def _run_installer(ref: AppRef, version_path: Path, materialized: Materialized) -> None:
    installer = manifest.installer_of(ref)
    if not installer:
        return
    persist_dir = paths.persist_app_dir(ref.app)
    # 可执行分支：仅当 file 或 args 存在时执行（对齐 Scoop Invoke-Installer）。
    if installer.get("file") or installer.get("args"):
        file_name = installer.get("file")
        if not file_name:
            if not materialized.filenames:
                raise InstallError(msg.ERR_INSTALLER_NO_FILE)
            file_name = materialized.filenames[0]
        file_name = str(file_name)
        executable = manifest.ensure_inside(version_path, file_name, "installer.file")
        if not executable.is_file():
            raise InstallError(
                msg.ERR_INSTALLER_FILE_MISSING.format(file=file_name)
            )

        args = str(installer.get("args") or "")
        args = manifest.substitute(
            args,
            {
                "$dir": str(version_path),
                "$original_dir": str(version_path),
                "$persist_dir": str(persist_dir),
                "$version": ref.version,
                "$global": "false",
            },
        )
        console.info(msg.MSG_RUNNING_INSTALLER.format(file=file_name))
        ps.run_installer(
            executable,
            args,
            version_path,
            context={
                "app": ref.app,
                "version": ref.version,
                "bucket": ref.bucket,
                "dir": version_path,
                "original_dir": version_path,
                "persist_dir": persist_dir,
                "architecture": ref.architecture,
                "fname": file_name,
                "manifest_path": ref.path,
                "cmd": "install",
            },
        )
        if not installer.get("keep") and file_name in materialized.copied:
            remove_path(materialized.copied[file_name])
    # script 分支：无条件执行（对齐 Scoop Invoke-HookScript）。
    script = installer.get("script")
    if script:
        ps.run_hook(
            "installer",
            script,
            app=ref.app,
            version=ref.version,
            bucket=ref.bucket,
            directory=version_path,
            original_dir=version_path,
            persist_dir=persist_dir,
            architecture=ref.architecture,
            fname=materialized.filenames,
            manifest_path=ref.path,
            cmd="install",
        )


def _create_bin_entries(
    ref: AppRef,
    current: Path,
    original_dir: Path,
    persist_dir: Path,
    artifacts: Artifacts,
) -> None:
    entries = manifest.parse_bin(ref.field("bin"))
    if not entries:
        return
    ensure_dir(paths.SHIM_DIR)
    mapping = {
        "$dir": str(current),
        "$original_dir": str(original_dir),
        "$persist_dir": str(persist_dir),
    }
    for entry in entries:
        target_text = manifest.substitute(entry.target, mapping)
        resolved = _resolve_bin_target(current, original_dir, target_text)
        args = manifest.substitute(entry.args, mapping)
        wrapper_name = _wrapper_name(entry.name)
        if wrapper_name.lower() == "apm":
            raise InstallError(msg.ERR_BIN_NAME_APM)
        variants = [
            paths.SHIM_DIR / f"{wrapper_name}{extension}"
            for extension in (".cmd", ".exe", ".shim")
        ]
        if any(variant.exists() for variant in variants):
            console.warn(msg.MSG_OVERWRITE_SHIM.format(name=wrapper_name))
        for variant in variants:
            remove_path(variant)
        if resolved.suffix.lower() in (".exe", ".com"):
            _write_native_shim(wrapper_name, resolved, args)
        else:
            _write_wrapper(variants[0], resolved, args)
        artifacts.bin.append(wrapper_name)
        console.info(msg.MSG_CREATING_SHIM.format(name=wrapper_name))


def _resolve_bin_target(current: Path, original_dir: Path, target: str) -> Path:
    text = target.strip().strip('"')
    if not text:
        raise InstallError(msg.ERR_BIN_TARGET_EMPTY)
    candidate = Path(text)
    if candidate.is_absolute():
        if candidate.is_file():
            return candidate
    else:
        for base in (current, original_dir):
            inside = Path(os.path.normpath(str(base / candidate)))
            if path_is_within(base, inside) and inside.is_file():
                return inside
    found = shutil.which(text)
    if found:
        return Path(found)
    raise InstallError(msg.ERR_BIN_TARGET_MISSING.format(target=target))


def _wrapper_name(name: str) -> str:
    path = Path(name)
    if path.suffix.lower() in (".exe", ".cmd", ".bat", ".ps1", ".com"):
        name = path.stem
    return name.strip()


def _write_wrapper(path: Path, target: Path, fixed_args: str) -> None:
    suffix = target.suffix.lower()
    if suffix == ".ps1":
        command = f'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{target}"'
    elif suffix in (".cmd", ".bat"):
        command = f'call "{target}"'
    else:
        command = f'"{target}"'
    if fixed_args:
        command += " " + fixed_args
    content = f"@echo off\nsetlocal\n{command} %*\nexit /b %ERRORLEVEL%\n"
    try:
        path.write_text(content, encoding="mbcs", newline="\r\n")
    except (LookupError, UnicodeEncodeError):
        path.write_text(content, encoding="utf-8", newline="\r\n")


def _write_native_shim(name: str, target: Path, fixed_args: str) -> None:
    """复制 vendored shim.exe 并写 .shim 旁车文件（对齐 Scoop 的 shim 函数）。"""
    if not paths.VENDORED_SHIM.is_file():
        raise InstallError(
            msg.ERR_SHIM_MISSING.format(path=paths.VENDORED_SHIM)
        )
    expected = _vendored_shim_checksum()
    actual = hashlib.sha256(paths.VENDORED_SHIM.read_bytes()).hexdigest()
    if actual != expected:
        raise InstallError(
            msg.ERR_SHIM_CHECKSUM.format(
                path=paths.VENDORED_SHIM, expected=expected, actual=actual
            )
        )
    shim_exe = paths.SHIM_DIR / f"{name}.exe"
    shim_conf = paths.SHIM_DIR / f"{name}.shim"
    shutil.copyfile(lp(paths.VENDORED_SHIM), lp(shim_exe))
    lines = [f'path = "{target}"']
    if fixed_args:
        lines.append(f"args = {fixed_args}")
    shim_conf.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\r\n")


def _vendored_shim_checksum() -> str:
    if not paths.VENDORED_SHIM_CHECKSUM.is_file():
        raise InstallError(
            msg.ERR_SHIM_CHECKSUM_MISSING.format(
                path=paths.VENDORED_SHIM_CHECKSUM
            )
        )
    text = paths.VENDORED_SHIM_CHECKSUM.read_text(encoding="utf-8-sig").strip()
    expected = text.split()[0].lower() if text else ""
    if len(expected) != 64:
        raise InstallError(
            msg.ERR_SHIM_CHECKSUM_INVALID.format(
                path=paths.VENDORED_SHIM_CHECKSUM
            )
        )
    return expected


def _create_shortcuts(
    ref: AppRef,
    current: Path,
    original_dir: Path,
    persist_dir: Path,
    artifacts: Artifacts,
) -> None:
    entries = manifest.parse_shortcuts(ref.field("shortcuts"))
    if not entries:
        return
    mapping = {
        "$dir": str(current),
        "$original_dir": str(original_dir),
        "$persist_dir": str(persist_dir),
    }
    for entry in entries:
        target = manifest.ensure_inside(
            current, manifest.substitute(entry.target, mapping), "shortcuts"
        )
        arguments = manifest.substitute(entry.args, mapping)
        icon = None
        if entry.icon:
            icon = manifest.ensure_inside(
                current, manifest.substitute(entry.icon, mapping), "shortcuts.icon"
            )
        if shortcuts.create(ref.app, target, entry.name, arguments, icon):
            artifacts.shortcuts.append(entry.name)


def _apply_env(
    ref: AppRef,
    current: Path,
    original_dir: Path,
    persist_dir: Path,
    artifacts: Artifacts,
) -> None:
    mapping = {
        "$dir": str(current),
        "$original_dir": str(original_dir),
        "$persist_dir": str(persist_dir),
    }
    path_entries: list[str] = []
    for item in manifest.env_add_path_values(ref):
        target = manifest.ensure_inside(
            current, manifest.substitute(item, mapping), "env_add_path"
        )
        path_entries.append(str(target))
    if path_entries:
        env.add_path(path_entries)
        artifacts.env_add_path.extend(path_entries)
        console.info(msg.MSG_ADD_PATH.format(entries="; ".join(path_entries)))

    values = manifest.env_set_values(ref)
    if values:
        values = {
            name: manifest.substitute(
                value,
                {
                    "$dir": str(current),
                    "$original_dir": str(original_dir),
                    "$persist_dir": str(persist_dir),
                    "$version": ref.version,
                    "$global": "false",
                },
            )
            for name, value in values.items()
        }
        env.set_variables(values)
        artifacts.env_set.extend(values.keys())
        console.info(msg.MSG_SET_ENV.format(names=", ".join(values.keys())))


def _apply_persist(
    ref: AppRef,
    original_dir: Path,
    persist_dir: Path,
    artifacts: Artifacts,
) -> None:
    entries = manifest.parse_persist(ref.field("persist"))
    if not entries:
        return
    ensure_dir(persist_dir)
    for source_rel, target_rel in entries:
        source = manifest.ensure_inside(original_dir, source_rel, "persist")
        target = manifest.ensure_inside(persist_dir, target_rel, "persist")
        ensure_dir(target.parent)
        if target.exists() or os.path.lexists(target):
            if source.exists() or os.path.lexists(source):
                backup = source.with_name(source.name + ".original")
                remove_path(backup)
                shutil.move(lp(source), lp(backup))
        elif source.exists() or os.path.lexists(source):
            ensure_dir(target.parent)
            shutil.move(lp(source), lp(target))
        else:
            ensure_dir(target)
        if target.is_dir():
            create_junction(source, target)
        else:
            create_hardlink(source, target)
        artifacts.persist.append([source_rel, target_rel])
        console.info(msg.MSG_PERSISTING.format(source=source_rel))


def _prune_old_versions(old_versions: list[Path]) -> None:
    for old in old_versions:
        if not old.exists():
            continue
        remove_path(old)
        if old.exists():
            console.warn(
                msg.MSG_OLD_VERSION_DELETE_FAILED.format(path=old)
            )


def _write_meta(ref: AppRef, artifacts: Artifacts, old_meta: dict[str, Any]) -> None:
    meta = {
        "app": ref.app,
        "version": ref.version,
        "bucket": ref.bucket,
        "architecture": ref.architecture,
        "installed_at": now_iso(),
        "hold": bool(old_meta.get("hold")),
        "bin": sorted(set(artifacts.bin)),
        "shortcuts": list(artifacts.shortcuts),
        "env_set": list(artifacts.env_set),
        "env_add_path": list(artifacts.env_add_path),
        "persist": artifacts.persist,
        "manifest": installed.MANIFEST_FILE,
        "source": str(ref.path),
    }
    installed.write_meta(ref.app, meta)
    write_json(installed.manifest_copy_path(ref.app), ref.data)


def _show_notes(
    ref: AppRef, current: Path, original_dir: Path, persist_dir: Path
) -> None:
    notes = manifest.notes_of(ref)
    if not notes:
        return
    notes = manifest.substitute(
        notes,
        {
            "$dir": str(current),
            "$original_dir": str(original_dir),
            "$persist_dir": str(persist_dir),
        },
    )
    console.heading(msg.NOTES_TITLE)
    console.plain(notes)
    console.plain()
