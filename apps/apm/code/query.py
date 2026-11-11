"""Read-only commands and inventory helpers."""

from __future__ import annotations

import json
import os

from . import buckets, installed
from . import manifest, paths
from .errors import ApmError
from .manifest import AppRef
from .output import console
from .util import display_width, pad_text, remove_path

INFO_LABEL_WIDTH = 8


def _field(label: str, value: object) -> str:
    """字段视图：左列按显示宽度补齐，不使用冒号。"""
    padding = max(1, INFO_LABEL_WIDTH - display_width(label))
    return f"{label}{' ' * padding}{value}"


def list_installed(query: str | None = None) -> int:
    apps = installed.installed_apps()
    if query:
        lowered = query.lower()
        apps = [item for item in apps if lowered in item.app.lower()]
    if not apps:
        if query:
            console.info("没有匹配的已安装应用")
        else:
            console.info("尚未安装任何应用")
        return 0
    name_width = max(8, max(display_width(item.app) for item in apps))
    version_width = max(8, max(display_width(item.version) for item in apps))
    console.heading(
        f"{pad_text('应用', name_width)}  {pad_text('版本', version_width)}  "
        f"{pad_text('Bucket', 12)} 锁定"
    )
    for item in apps:
        console.plain(
            f"{pad_text(item.app, name_width)}  {pad_text(item.version, version_width)}  "
            f"{pad_text(item.bucket or '-', 12)} {'是' if item.hold else '否'}"
        )
    console.plain()
    console.summary(f"共 {len(apps)} 个应用")
    return 0


def search(query: str) -> int:
    lowered = query.lower()
    rows: list[tuple[str, str, str]] = []
    for bucket, app, path in buckets.iter_manifests():
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        haystack = [app]
        for item in manifest.normalize_list(data.get("bin")):
            if isinstance(item, str):
                haystack.append(item)
            elif isinstance(item, list) and item:
                haystack.extend(str(part) for part in item)
        if not any(lowered in value.lower() for value in haystack):
            continue
        rows.append((bucket, app, str(data.get("version") or "")))
    if not rows:
        console.info(f"没有匹配 '{query}' 的应用")
        return 0
    rows.sort(key=lambda row: (row[1].lower(), row[0].lower()))
    bucket_width = max(8, max(display_width(row[0]) for row in rows))
    name_width = max(12, max(display_width(row[1]) for row in rows))
    console.heading(
        f"{pad_text('Bucket', bucket_width)}  {pad_text('应用', name_width)}  版本"
    )
    for bucket, app, version in rows:
        console.plain(
            f"{pad_text(bucket, bucket_width)}  {pad_text(app, name_width)}  {version}"
        )
    console.plain()
    console.summary(f"共 {len(rows)} 个结果")
    return 0


def _find_for_info(app: str) -> AppRef | None:
    try:
        return manifest.load_app(app)
    except ApmError:
        copy_path = installed.manifest_copy_path(app)
        if copy_path.is_file():
            try:
                data = json.loads(copy_path.read_text(encoding="utf-8-sig"))
                meta = installed.read_meta(app) or {}
                return AppRef(
                    app=app,
                    bucket=str(meta.get("bucket") or ""),
                    path=copy_path,
                    data=data,
                    architecture=str(
                        meta.get("architecture") or manifest.detect_architecture()
                    ),
                )
            except (OSError, ValueError):
                return None
    return None


def info(apps: list[str]) -> int:
    failed = False
    first = True
    for app in apps:
        _, name, _ = manifest.parse_app_spec(app)
        ref = _find_for_info(name)
        if ref is None:
            console.error(f"找不到应用：{name}")
            failed = True
            continue
        if not first:
            console.plain()
        first = False
        data = ref.data
        meta = installed.read_meta(name) or {}
        installed_now = installed.is_installed(name)
        installed_text = "否"
        if installed_now:
            installed_text = f"是（{installed.installed_version(name)}）"
        console.plain(_field("应用", name))
        console.plain(_field("版本", ref.version))
        console.plain(_field("Bucket", ref.bucket or "-"))
        console.plain(_field("已安装", installed_text))
        console.plain(_field("锁定", "是" if installed_now and meta.get("hold") else "否"))
        console.plain(_field("主页", data.get("homepage") or "-"))
        console.plain(_field("许可证", manifest.license_text(data.get("license", "")) or "-"))
        description = data.get("description")
        if description:
            console.plain(_field("描述", description))
        depends = data.get("depends")
        if depends:
            console.plain(
                _field(
                    "依赖",
                    ", ".join(str(item) for item in manifest.normalize_list(depends)),
                )
            )
        try:
            bins = manifest.parse_bin(ref.field("bin"))
            if bins:
                console.plain(_field("命令", ", ".join(entry.name for entry in bins)))
        except ApmError:
            pass
        suggest = data.get("suggest")
        if isinstance(suggest, dict) and suggest:
            console.plain(_field("建议", ", ".join(str(key) for key in suggest)))
    return 1 if failed else 0


def status() -> int:
    apps = installed.installed_apps()
    if not apps:
        console.info("尚未安装任何应用")
        return 0
    outdated: list[tuple[str, str, str]] = []
    errors = 0
    for item in apps:
        if item.hold:
            console.info(f"{item.app} 已锁定，跳过检查")
            continue
        try:
            ref = manifest.load_app(item.app, item.bucket)
        except ApmError as exc:
            console.warn(f"无法检查 {item.app}：{exc}")
            errors += 1
            continue
        if ref.version != item.version:
            outdated.append((item.app, item.version, ref.version))
    if not outdated:
        if errors:
            return 1
        console.success("所有应用均已是最新版本")
        return 0
    name_width = max(8, max(display_width(row[0]) for row in outdated))
    console.heading(f"{pad_text('应用', name_width)}  {pad_text('已安装', 14)}  最新")
    for app, current, latest in outdated:
        console.plain(
            f"{pad_text(app, name_width)}  {pad_text(current, 14)}  {latest}"
        )
    console.plain()
    console.summary(
        f"共 {len(outdated)} 个应用可更新，运行 apm update <app> 或 apm update *"
    )
    return 1 if errors else 0


def cleanup(app: str | None = None) -> int:
    targets: list[str]
    if app:
        name = manifest.parse_app_spec(app)[1]
        if name.lower() == "apm":
            console.error("不能清理 APM 自身")
            return 1
        targets = [name]
    else:
        targets = [item.app for item in installed.installed_apps()]
        if paths.APPS_DIR.is_dir():
            for entry in paths.APPS_DIR.iterdir():
                if entry.is_dir() and entry.name.lower() != "apm" and entry.name not in targets:
                    targets.append(entry.name)

    removed_dirs = 0
    removed_files = 0
    for name in targets:
        app_root = paths.app_dir(name)
        if not app_root.is_dir():
            continue
        current = paths.current_dir(name)
        keep: set[str] = set()
        if current.exists():
            meta_version = installed.installed_version(name)
            if meta_version:
                keep.add(meta_version)
            keep.add(Path(os.path.realpath(current)).name)
        else:
            for file_path in (installed.meta_path(name), installed.manifest_copy_path(name)):
                if file_path.exists():
                    remove_path(file_path)
                    if file_path.exists():
                        console.warn(f"删除失败（可能被占用）：{file_path}")
                    else:
                        removed_files += 1
        for directory in list(app_root.iterdir()):
            if directory.name in ("current", installed.META_FILE, installed.MANIFEST_FILE):
                continue
            if directory.name in (".apm-current-new", ".apm-current-old"):
                remove_path(directory)
                if directory.exists():
                    console.warn(f"删除失败（可能被占用）：{directory}")
                else:
                    removed_dirs += 1
                continue
            if not directory.is_dir():
                continue
            if directory.name in keep:
                continue
            remove_path(directory)
            if directory.exists():
                console.warn(f"删除失败（可能被占用）：{directory}")
            else:
                removed_dirs += 1
        try:
            if not any(app_root.iterdir()):
                app_root.rmdir()
        except OSError:
            pass

    if paths.CACHE_DIR.is_dir():
        prefix = f"{targets[0].lower()}#" if len(targets) == 1 else None
        for path in list(paths.CACHE_DIR.iterdir()):
            if not path.is_file():
                continue
            if not path.name.endswith((".download", ".aria2", ".tmp", ".txt")):
                continue
            if prefix and not path.name.lower().startswith(prefix):
                continue
            remove_path(path)
            if path.exists():
                console.warn(f"删除失败（可能被占用）：{path}")
            else:
                removed_files += 1

    console.success(
        f"已清理：删除 {removed_dirs} 个目录、{removed_files} 个临时文件"
    )
    return 0
