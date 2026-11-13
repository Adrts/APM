"""Dependency resolution (DFS post-order, cycle detection)."""

from __future__ import annotations

from . import installed, manifest, messages as msg
from .errors import DependencyError
from .manifest import AppRef
from .output import console


def resolve(specs: list[str], for_update: bool = False) -> tuple[list[AppRef], set[str]]:
    """解析依赖，后序返回 refs；同时返回用户显式指定的根应用名集合。"""
    parsed = [manifest.parse_app_spec(spec) for spec in specs]
    refs: list[AppRef] = []
    roots: set[str] = set()
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
                    msg.ERR_DEPENDENCY_CONFLICT.format(
                        app=app, resolved=versions[app], version=version
                    )
                )
            else:
                return
        if app in visiting:
            chain = " -> ".join(visiting + [app])
            raise DependencyError(
                msg.ERR_DEPENDENCY_CYCLE.format(chain=chain)
            )

        if installed.is_installed(app):
            if not is_root:
                seen.add(app)
                return
            elif not for_update:
                ref = manifest.load_app(app, bucket, version)
                if installed.installed_version(app) == ref.version:
                    console.info(
                        msg.MSG_APP_INSTALLED_SKIP.format(
                            app=app, version=ref.version
                        )
                    )
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
            if is_root:
                roots.add(app)
        finally:
            visiting.pop()

    for bucket, app, version in parsed:
        visit(bucket, app, version, True)
    return refs, roots
