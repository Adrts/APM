"""Dependency resolution (DFS post-order, cycle detection)."""

from __future__ import annotations

from . import installed, manifest
from .errors import DependencyError
from .manifest import AppRef
from .output import console


def resolve(specs: list[str], for_update: bool = False) -> list[AppRef]:
    parsed = [manifest.parse_app_spec(spec) for spec in specs]
    refs: list[AppRef] = []
    seen: set[str] = set()
    versions: dict[str, str] = {}
    visiting: list[str] = []

    def visit(
        bucket: str | None,
        app: str,
        version: str | None,
        is_root: bool,
    ) -> None:
        if app in seen:
            if is_root and for_update and app not in versions:
                pass
            elif version and app in versions and versions[app] != version:
                raise DependencyError(
                    f"依赖版本冲突：{app} 已按 {versions[app]} 解析，又要求 {version}"
                )
            else:
                return
        if app in visiting:
            chain = " -> ".join(visiting + [app])
            raise DependencyError(f"检测到循环依赖：{chain}")

        if installed.is_installed(app):
            if not is_root:
                seen.add(app)
                return
            elif not for_update:
                ref = manifest.load_app(app, bucket, version)
                if installed.installed_version(app) == ref.version:
                    console.info(f"{app} {ref.version} 已安装，跳过")
                    seen.add(app)
                    return

        visiting.append(app)
        try:
            ref = manifest.load_app(app, bucket, version)
            for dep_bucket, dep_app, dep_version in ref.dependencies():
                visit(dep_bucket, dep_app, dep_version, False)
            refs.append(ref)
            seen.add(app)
            versions[app] = ref.version
        finally:
            visiting.pop()

    for bucket, app, version in parsed:
        visit(bucket, app, version, True)
    return refs
