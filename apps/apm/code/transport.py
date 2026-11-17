"""镜像地址改写：软件下载与软件源 git 操作共用的传输层。

``transport_url`` 判断 URL 是否属于可加速地址（GitHub / SourceForge），
命中时按配置的前缀改写传输地址；未配置前缀时输出一次提示并直连。
"""

from __future__ import annotations

import threading
from typing import Any
from urllib.parse import urlparse

from . import messages as msg, paths
from .output import console

_warned_mirrors: set[str] = set()
_warn_lock = threading.Lock()


def _warn_mirror(provider: str) -> None:
    with _warn_lock:
        if provider in _warned_mirrors:
            return
        _warned_mirrors.add(provider)
    console.warn(
        msg.MSG_MIRROR_NOT_CONFIGURED.format(
            provider=provider, config=paths.CONFIG_FILE
        )
    )


def transport_url(url: str, config: dict[str, Any]) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return url
    host = (parsed.hostname or "").lower()
    lowered = url.lower()
    mirror = config.get("download", {}).get("mirror", {})
    is_github = (
        "github.com" in host
        or "github.com" in lowered
        or "githubusercontent.com" in host
        or "githubusercontent.com" in lowered
    )
    if is_github:
        prefix = str(mirror.get("github") or "").strip()
        if not prefix:
            _warn_mirror("github")
        elif not url.lower().startswith(prefix.lower()):
            return prefix.rstrip("/") + "/" + url
        return url
    if "sourceforge.net" in host or "sourceforge.net" in url.lower():
        prefix = str(mirror.get("sourceforge") or "").strip()
        if not prefix:
            _warn_mirror("sourceforge")
        elif not url.lower().startswith(prefix.lower()):
            return prefix.rstrip("/") + "/" + url
        return url
    return url
