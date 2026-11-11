"""Application uninstallation."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from . import env, installed, manifest, messages as msg, paths, ps, shortcuts
from .errors import ApmError, UninstallError
from .manifest import AppRef
from .output import console
from .util import read_json, remove_path


def uninstall_apps(specs: list[str], purge: bool = False) -> int:
    failures: list[str] = []
    for spec in specs:
        _, app, _ = manifest.parse_app_spec(spec)
        try:
            _uninstall_one(app, purge)
        except ApmError as exc:
            failures.append(msg.ERR_ITEM.format(name=app, error=exc))
        except Exception as exc:  # noqa: BLE001
            failures.append(msg.ERR_ITEM.format(name=app, error=exc))
    if failures:
        console.failure_list(failures)
        return 1
    return 0


def _load_ref(app: str, meta: dict[str, Any]) -> AppRef:
    copy_path = installed.manifest_copy_path(app)
    data: dict[str, Any] = {}
    source = copy_path
    if copy_path.is_file():
        try:
            loaded = read_json(copy_path)
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, ValueError):
            data = {}
    if not data:
        try:
            ref = manifest.load_app(app, meta.get("bucket"))
            data = ref.data
            source = ref.path
        except ApmError:
            data = {}
    architecture = str(meta.get("architecture") or manifest.detect_architecture())
    return AppRef(
        app=app,
        bucket=str(meta.get("bucket") or ""),
        path=source,
        data=data,
        architecture=architecture,
    )


def _hook_names(ref: AppRef) -> list[str]:
    try:
        return [manifest.remote_filename(package.url) for package in ref.packages()]
    except ApmError:
        return []


def _uninstall_one(app: str, purge: bool) -> None:
    if app.lower() == "apm":
        raise UninstallError(msg.MSG_UNINSTALL_APM)
    if not installed.is_installed(app):
        raise UninstallError(msg.MSG_APP_NOT_INSTALLED.format(app=app))

    meta = installed.read_meta(app) or {}
    ref = _load_ref(app, meta)
    current = paths.current_dir(app)
    original_dir = Path(os.path.realpath(current))
    persist_dir = paths.persist_app_dir(app)
    version = str(meta.get("version") or installed.installed_version(app) or "")
    filenames = _hook_names(ref)
    context = dict(
        app=app,
        version=version,
        bucket=ref.bucket,
        directory=current,
        original_dir=original_dir,
        persist_dir=persist_dir,
        architecture=ref.architecture,
        fname=filenames,
        manifest_path=ref.path,
    )

    ps.run_hook("pre_uninstall", ref.field("pre_uninstall"), cmd="uninstall", **context)

    uninstaller = manifest.uninstaller_of(ref)
    if uninstaller:
        # 可执行分支：仅当 file 或 args 存在时执行（对齐 Scoop Invoke-Installer）。
        if uninstaller.get("file") or uninstaller.get("args"):
            file_name = uninstaller.get("file") or (filenames[0] if filenames else "")
            if not file_name:
                raise UninstallError(msg.ERR_UNINSTALLER_NO_FILE)
            executable = manifest.ensure_inside(
                current, str(file_name), "uninstaller.file"
            )
            args = manifest.substitute(
                str(uninstaller.get("args") or ""),
                {
                    "$dir": str(current),
                    "$original_dir": str(original_dir),
                    "$persist_dir": str(persist_dir),
                    "$version": version,
                    "$global": "false",
                },
            )
            ps.run_installer(
                executable,
                args,
                original_dir,
                context={
                    "app": app,
                    "version": version,
                    "bucket": ref.bucket,
                    "dir": current,
                    "original_dir": original_dir,
                    "persist_dir": persist_dir,
                    "architecture": ref.architecture,
                    "fname": filenames[0] if filenames else "",
                    "manifest_path": ref.path,
                    "cmd": "uninstall",
                },
            )
        # script 分支：无条件执行（对齐 Scoop Invoke-HookScript）。
        script = uninstaller.get("script")
        if script:
            ps.run_hook("uninstaller", script, cmd="uninstall", **context)

    ps.run_hook("post_uninstall", ref.field("post_uninstall"), cmd="uninstall", **context)

    _remove_shortcuts(app, meta, ref)
    _remove_bins(app, meta)
    _remove_env(app, meta, ref, current, original_dir, persist_dir)
    _unlink_persist(meta, ref, original_dir)

    remove_path(current)
    versions = installed.version_dirs(app)
    target_version = str(meta.get("version") or "")
    if target_version and paths.version_dir(app, target_version) not in versions:
        versions.append(paths.version_dir(app, target_version))
    for directory in versions:
        remove_path(directory)

    if purge:
        remove_path(paths.persist_app_dir(app))
        console.info(msg.MSG_PERSIST_PURGED.format(app=app))
    else:
        console.info(msg.MSG_PERSIST_KEPT.format(path=persist_dir))

    for file_path in (
        installed.meta_path(app),
        installed.manifest_copy_path(app),
    ):
        if file_path.exists():
            remove_path(file_path)
    app_root = paths.app_dir(app)
    try:
        if app_root.is_dir() and not any(app_root.iterdir()):
            app_root.rmdir()
    except OSError:
        pass
    console.success(msg.MSG_UNINSTALLED.format(app=app))


def _remove_shortcuts(app: str, meta: dict[str, Any], ref: AppRef) -> None:
    names = [str(item) for item in meta.get("shortcuts") or []]
    if not names:
        try:
            names = [entry.name for entry in manifest.parse_shortcuts(ref.field("shortcuts"))]
        except ApmError:
            names = []
    for name in names:
        try:
            shortcuts.remove(app, name)
        except ApmError as exc:
            console.warn(str(exc))
    directory = paths.START_MENU_APPS / app
    if directory.is_dir():
        try:
            if not any(directory.iterdir()):
                directory.rmdir()
        except OSError:
            pass


def _remove_bins(app: str, meta: dict[str, Any]) -> None:
    names = [str(item) for item in meta.get("bin") or []]
    for name in names:
        variants = [
            paths.SHIM_DIR / f"{name}{extension}"
            for extension in (".cmd", ".exe", ".shim")
        ]
        existing = [variant for variant in variants if variant.exists()]
        if existing:
            for variant in existing:
                remove_path(variant)
            console.info(msg.MSG_REMOVE_SHIM.format(name=name))


def _remove_env(
    app: str,
    meta: dict[str, Any],
    ref: AppRef,
    current: Path,
    original_dir: Path,
    persist_dir: Path,
) -> None:
    path_entries = [str(item) for item in meta.get("env_add_path") or []]
    if not path_entries:
        mapping = {
            "$dir": str(current),
            "$original_dir": str(original_dir),
            "$persist_dir": str(persist_dir),
        }
        for item in manifest.env_add_path_values(ref):
            try:
                path_entries.append(
                    str(
                        manifest.ensure_inside(
                            current, manifest.substitute(item, mapping), "env_add_path"
                        )
                    )
                )
            except ApmError:
                pass
    if path_entries:
        env.remove_path(path_entries)

    names = [str(item) for item in meta.get("env_set") or []]
    if not names:
        try:
            names = list(manifest.env_set_values(ref).keys())
        except ApmError:
            names = []
    if names:
        env.remove_variables(names)


def _unlink_persist(
    meta: dict[str, Any], ref: AppRef, original_dir: Path
) -> None:
    entries = meta.get("persist") or []
    if not entries:
        try:
            entries = manifest.parse_persist(ref.field("persist"))
        except ApmError:
            entries = []
    for item in entries:
        try:
            source_rel = str(item[0])
        except (TypeError, IndexError):
            continue
        source = original_dir / source_rel
        if os.path.lexists(source):
            remove_path(source)
