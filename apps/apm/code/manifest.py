"""Manifest loading and field resolution."""

from __future__ import annotations

import os
import platform
import re
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from . import buckets, messages as msg
from .errors import ManifestError
from .util import path_is_within, read_json, sanitary_name

VERSION_RE = re.compile(r"^[\w.\-+_]+$")

# Path.suffix 对「foo.tar.xz」只返回 .xz；这些复合扩展名必须整体识别。
COMPOUND_EXTENSIONS = (".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst")


@dataclass
class BinEntry:
    target: str
    name: str
    args: str = ""


@dataclass
class ShortcutEntry:
    target: str
    name: str
    args: str = ""
    icon: str | None = None


@dataclass
class Package:
    url: str
    hash: str | None
    cookie: Any = None


@dataclass
class AppRef:
    app: str
    bucket: str
    path: Path
    data: dict[str, Any]
    architecture: str

    @property
    def version(self) -> str:
        return str(self.data["version"])

    def field(self, name: str) -> Any:
        return arch_specific(self.data, self.architecture, name)

    def packages(self) -> list[Package]:
        return manifest_packages(self.data, self.architecture)

    def dependencies(self) -> list[tuple[str | None, str, str | None]]:
        return manifest_dependencies(self.data, self.architecture)


def normalize_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def arch_specific(data: dict[str, Any], arch: str, prop: str) -> Any:
    architecture = data.get("architecture")
    if isinstance(architecture, dict):
        arch_data = architecture.get(arch)
        if isinstance(arch_data, dict):
            value = arch_data.get(prop)
            if value:
                return value
    return data.get(prop)


def detect_architecture() -> str:
    machine = platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        return "arm64"
    if struct.calcsize("P") == 8:
        return "64bit"
    return "32bit"


def select_architecture(data: dict[str, Any]) -> str:
    arch = detect_architecture()
    if arch == "arm64":
        architecture = data.get("architecture")
        arm64 = architecture.get("arm64") if isinstance(architecture, dict) else None
        has_arm64_url = isinstance(arm64, dict) and bool(arm64.get("url"))
        if not has_arm64_url:
            build = 0
            if os.name == "nt":
                try:
                    build = sys.getwindowsversion().build
                except Exception:
                    build = 0
            arch = "64bit" if build >= 22000 else "32bit"
    if not arch_specific(data, arch, "url"):
        raise ManifestError(msg.ERR_ARCH_UNSUPPORTED.format(arch=arch))
    return arch


def manifest_urls(data: dict[str, Any], arch: str) -> list[str]:
    raw = arch_specific(data, arch, "url")
    urls = [str(item) for item in normalize_list(raw) if item]
    if not urls:
        raise ManifestError(msg.ERR_MANIFEST_NO_URL)
    for url in urls:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ManifestError(
                msg.ERR_MANIFEST_SCHEME.format(url=url)
            )
    return urls


def hash_for_url(data: dict[str, Any], arch: str, urls: list[str], url: str) -> str | None:
    raw = arch_specific(data, arch, "hash")
    if raw is None:
        return None
    hashes = raw if isinstance(raw, list) else [raw]
    try:
        index = urls.index(url)
    except ValueError as exc:
        raise ManifestError(
            msg.ERR_MANIFEST_HASH_URL.format(url=url)
        ) from exc
    if index >= len(hashes):
        raise ManifestError(msg.ERR_MANIFEST_HASH_COUNT.format(url=url))
    value = hashes[index]
    return str(value) if value else None


def manifest_packages(data: dict[str, Any], arch: str) -> list[Package]:
    urls = manifest_urls(data, arch)
    cookie = arch_specific(data, arch, "cookie")
    return [
        Package(url=url, hash=hash_for_url(data, arch, urls, url), cookie=cookie)
        for url in urls
    ]


def manifest_dependencies(
    data: dict[str, Any], arch: str
) -> list[tuple[str | None, str, str | None]]:
    result: list[tuple[str | None, str, str | None]] = []
    for item in normalize_list(arch_specific(data, arch, "depends")):
        if not item:
            continue
        try:
            result.append(parse_app_spec(str(item)))
        except ManifestError as exc:
            raise ManifestError(
                msg.ERR_DEPENDENCY_FORMAT.format(item=item, error=exc)
            ) from exc
    return result


def split_spec_raw(spec: str) -> tuple[str | None, str, str | None]:
    """通配符 spec 的分段：不清洗字符，保留 * ? [ 等通配符。

    返回 ``(bucket, app, version)``；与 parse_app_spec 的分段规则一致，
    但不执行 sanitary_name，因此通配符不会被剥掉。
    """
    raw = spec.strip().replace("\\", "/")
    version: str | None = None
    if "@" in raw:
        raw, version = raw.rsplit("@", 1)
    bucket: str | None = None
    app = raw
    if "/" in raw:
        bucket, app = raw.split("/", 1)
        bucket = bucket or None
    return bucket, app, version


def parse_app_spec(spec: str) -> tuple[str | None, str, str | None]:
    raw = spec.strip().replace("\\", "/")
    if not raw:
        raise ManifestError(msg.ERR_APP_NAME_EMPTY)
    version: str | None = None
    if "@" in raw:
        raw, version = raw.rsplit("@", 1)
        if not version or not VERSION_RE.match(version):
            raise ManifestError(msg.ERR_VERSION_INVALID.format(version=version))
    bucket: str | None = None
    app = raw
    if "/" in raw:
        bucket, app = raw.split("/", 1)
        if not bucket or not app:
            raise ManifestError(msg.ERR_APP_SPEC_INVALID.format(spec=spec))
        if not re.match(r"^[\w.\-]+$", bucket):
            raise ManifestError(
                msg.ERR_BUCKET_NAME_FIELD_INVALID.format(bucket=bucket)
            )
    app = sanitary_name(app)
    if not app:
        raise ManifestError(msg.ERR_APP_SPEC_INVALID.format(spec=spec))
    return bucket, app, version


def validate_manifest(data: Any, app: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ManifestError(msg.ERR_MANIFEST_NOT_OBJECT.format(app=app))
    version = data.get("version")
    if not isinstance(version, str) or not version:
        raise ManifestError(msg.ERR_MANIFEST_VERSION_MISSING.format(app=app))
    if version != "nightly" and not VERSION_RE.match(version):
        raise ManifestError(
            msg.ERR_MANIFEST_VERSION_INVALID.format(version=version)
        )
    homepage = data.get("homepage")
    if not isinstance(homepage, str) or not homepage:
        raise ManifestError(
            msg.ERR_MANIFEST_HOMEPAGE_MISSING.format(app=app)
        )
    if "license" not in data:
        raise ManifestError(msg.ERR_MANIFEST_LICENSE_MISSING.format(app=app))
    architecture = data.get("architecture")
    if architecture is not None and not isinstance(architecture, dict):
        raise ManifestError(
            msg.ERR_MANIFEST_ARCH_INVALID.format(app=app)
        )
    return data


def load_app(app: str, bucket: str | None = None, pinned_version: str | None = None) -> AppRef:
    bucket_name, path = buckets.find_app(app, bucket)
    try:
        data = validate_manifest(read_json(path), app)
    except (OSError, ValueError) as exc:
        raise ManifestError(
            msg.ERR_MANIFEST_PARSE.format(path=path, error=exc)
        ) from exc
    if pinned_version and data.get("version") != pinned_version:
        raise ManifestError(
            msg.ERR_MANIFEST_PINNED.format(
                current=data.get("version"),
                app=app,
                version=pinned_version,
            )
        )
    try:
        arch = select_architecture(data)
    except ManifestError as exc:
        raise ManifestError(
            msg.ERR_MANIFEST_WRAPPED.format(app=app, error=exc)
        ) from exc
    return AppRef(
        app=sanitary_name(app),
        bucket=bucket_name,
        path=path,
        data=data,
        architecture=arch,
    )


def substitute(text: str, mapping: dict[str, str]) -> str:
    result = text
    for key, value in mapping.items():
        result = result.replace(key, value)
    return result


def parse_bin(value: Any) -> list[BinEntry]:
    entries: list[BinEntry] = []
    for item in normalize_list(value):
        if isinstance(item, str):
            target = item
            name = Path(item).stem
            args = ""
        elif isinstance(item, list) and item:
            target = str(item[0])
            name = str(item[1]) if len(item) > 1 and item[1] else Path(target).stem
            args = str(item[2]) if len(item) > 2 and item[2] is not None else ""
        else:
            raise ManifestError(msg.ERR_BIN_ENTRY.format(item=item))
        if not target:
            raise ManifestError(msg.ERR_BIN_TARGET_EMPTY)
        if not name:
            raise ManifestError(msg.ERR_BIN_NAME_EMPTY.format(target=target))
        if any(sep in name for sep in ("/", "\\")):
            raise ManifestError(msg.ERR_BIN_NAME_PATH.format(name=name))
        entries.append(BinEntry(target=target, name=name, args=args))
    return entries


def parse_shortcuts(value: Any) -> list[ShortcutEntry]:
    entries: list[ShortcutEntry] = []
    for item in normalize_list(value):
        if not isinstance(item, list) or len(item) < 2:
            raise ManifestError(msg.ERR_SHORTCUT_ENTRY.format(item=item))
        target = str(item[0])
        name = str(item[1])
        args = str(item[2]) if len(item) > 2 and item[2] is not None else ""
        icon = str(item[3]) if len(item) > 3 and item[3] is not None else None
        if not target or not name:
            raise ManifestError(msg.ERR_SHORTCUT_MISSING.format(item=item))
        entries.append(ShortcutEntry(target=target, name=name, args=args, icon=icon))
    return entries


def parse_persist(value: Any) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for item in normalize_list(value):
        if isinstance(item, str):
            source = item
            target = item
        elif isinstance(item, list) and item:
            source = str(item[0])
            target = str(item[1]) if len(item) > 1 and item[1] else item[0]
        else:
            raise ManifestError(msg.ERR_PERSIST_ENTRY.format(item=item))
        if not source:
            raise ManifestError(msg.ERR_PERSIST_SOURCE_EMPTY)
        entries.append((source, target))
    return entries


def extract_dir_values(ref: AppRef) -> tuple[list[str | None], bool]:
    raw = ref.field("extract_dir")
    if raw is None:
        return [], False
    values = [str(item) if item else None for item in normalize_list(raw)]
    return values, isinstance(raw, list)


def installer_of(ref: AppRef) -> dict[str, Any] | None:
    raw = ref.field("installer")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ManifestError(msg.ERR_INSTALLER_FORMAT.format(app=ref.app))
    return raw


def uninstaller_of(ref: AppRef) -> dict[str, Any] | None:
    raw = ref.field("uninstaller")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ManifestError(msg.ERR_UNINSTALLER_FORMAT.format(app=ref.app))
    return raw


def env_add_path_values(ref: AppRef) -> list[str]:
    return [str(item) for item in normalize_list(ref.field("env_add_path")) if item]


def env_set_values(ref: AppRef) -> dict[str, str]:
    raw = ref.field("env_set")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ManifestError(msg.ERR_ENV_SET_FORMAT.format(app=ref.app))
    return {str(key): str(value) for key, value in raw.items()}


def notes_of(ref: AppRef) -> str | None:
    raw = ref.field("notes")
    items = [str(item) for item in normalize_list(raw) if item]
    return "\n".join(items) if items else None


def license_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        identifier = value.get("identifier")
        url = value.get("url")
        if identifier and url:
            return f"{identifier} ({url})"
        return str(identifier or url or "")
    return str(value)


def remote_filename(url: str) -> str:
    parsed = urlparse(url)
    fragment = unquote(parsed.fragment)
    # 清单约定：fragment 以 / 开头时表示保存文件名，例如
    # https://curl.se/ca/cacert-2026-11-05.pem#/cacert.pem
    if fragment.startswith("/"):
        renamed = Path(fragment.lstrip("/")).name
        if renamed:
            return renamed
    name = unquote(Path(parsed.path).name)
    if not name:
        name = unquote(parsed.fragment.strip("/#")) or "download"
    if "." not in name:
        match = re.search(r"[?=]+([\w._-]+)", parsed.query)
        if match:
            name = match.group(1)
    if "." not in name:
        fragment = unquote(parsed.fragment.strip("/#"))
        if fragment:
            name = fragment
    return name


def url_extension(url: str) -> str:
    name = remote_filename(url).lower()
    for compound in COMPOUND_EXTENSIONS:
        if name.endswith(compound):
            return compound
    return Path(name).suffix.lower()


def ensure_inside(base: Path, relative: str, field: str) -> Path:
    rel_path = Path(relative)
    if rel_path.is_absolute() or rel_path.drive or ".." in rel_path.parts:
        raise ManifestError(
            msg.ERR_FIELD_OUTSIDE.format(field=field, relative=relative)
        )
    target = Path(os.path.normpath(str(base / rel_path)))
    if not path_is_within(base, target):
        raise ManifestError(
            msg.ERR_FIELD_OUTSIDE.format(field=field, relative=relative)
        )
    return target
