"""Configuration stored in %USERPROFILE%\\.config\\apm\\config.json."""

from __future__ import annotations

import copy
import json
from typing import Any

from . import paths
from .errors import ConfigError
from .util import ensure_dir, write_json

DEFAULTS: dict[str, Any] = {
    "download": {
        "backend": "python",
        "concurrency": {
            "apps": 3,
            "segments": 5,
        },
        "mirror": {
            "github": "",
            "sourceforge": "",
        },
    },
    "verify": {
        "method": "hashlib",
        "seven_zip": "",
    },
    "extract": {
        "seven_zip": "",
        "innounp": "",
    },
}

BACKENDS = ("python", "aria2", "curl", "wget")
VERIFY_METHODS = ("hashlib", "7zip")

# 每个选项的说明，生成默认配置时以「键#」旁车字段写入（JSON 不支持注释）。
OPTION_DOCS: dict[str, str] = {
    "download.backend": "下载后端，必须为 python / aria2 / curl / wget 之一，其他值直接报错。"
    "aria2 支持多连接分片下载，其余后端为单连接顺序下载",
    "download.concurrency.apps": "同时下载的应用数量上限",
    "download.concurrency.segments": "单个文件的分片并发连接数，仅 aria2 后端生效，其余后端按 1 处理",
    "download.mirror.github": "GitHub 加速前缀，留空表示直连（下载时提示一次）。"
    "填写后 GitHub 下载地址改写为「前缀 + 原始地址」，例如 https://mirror.example.com/ ，"
    "用于绕过网络对 GitHub 的限速",
    "download.mirror.sourceforge": "SourceForge 加速前缀，留空表示直连。填写后改写规则同上",
    "verify.method": "校验方式：hashlib（Python 标准库计算哈希并比对 manifest 的 hash）/ "
    "7zip（调用 7z.exe 计算哈希，manifest 无 hash 时顺带做压缩包完整性测试）",
    "verify.seven_zip": "7z.exe 完整路径，留空表示按「本配置项 → PATH → apps 下已安装的 7zip」顺序查找。"
    "仅 verify.method 为 7zip 时使用",
    "extract.seven_zip": "7z.exe 完整路径，留空表示按「本配置项 → PATH → apps 下已安装的 7zip」顺序查找。"
    "zip/7z/rar/iso 等归档解压时优先使用；缺失时 zip/tar 回退标准库解压，其余格式直接报错",
    "extract.innounp": "innounp.exe 完整路径，留空表示按「本配置项 → PATH → apps 下已安装的 innounp」顺序查找。"
    "manifest 标记 innosetup 的 .exe 安装包解包必需，缺失直接报错",
}


def _default_with_docs() -> dict[str, Any]:
    """生成默认配置，并在每个选项旁写入「键#」说明字段（JSON 不支持注释）。"""
    result = copy.deepcopy(DEFAULTS)

    def inject(node: dict[str, Any], prefix: str) -> dict[str, Any]:
        rebuilt: dict[str, Any] = {}
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                rebuilt[key] = inject(value, path)
            else:
                rebuilt[key] = value
            doc = OPTION_DOCS.get(path)
            if doc is not None:
                rebuilt[f"{key}#"] = doc
        return rebuilt

    return inject(result, "")


def _strip_doc_keys(node: dict[str, Any]) -> dict[str, Any]:
    """移除全部「键#」说明字段，仅返回运行时使用的配置值。"""
    stripped: dict[str, Any] = {}
    for key, value in node.items():
        if key.endswith("#"):
            continue
        stripped[key] = _strip_doc_keys(value) if isinstance(value, dict) else value
    return stripped


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def read_raw() -> dict[str, Any]:
    if not paths.CONFIG_FILE.exists():
        try:
            ensure_dir(paths.CONFIG_DIR)
            write_json(paths.CONFIG_FILE, _default_with_docs())
        except OSError as exc:
            raise ConfigError(
                f"无法创建默认配置文件 {paths.CONFIG_FILE}：{exc}"
            ) from exc
        return copy.deepcopy(DEFAULTS)
    try:
        with open(paths.CONFIG_FILE, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise ConfigError(f"无法读取配置文件 {paths.CONFIG_FILE}：{exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"配置文件格式错误（顶层必须是对象）：{paths.CONFIG_FILE}")
    return data


def load() -> dict[str, Any]:
    """Return the effective configuration (defaults merged with the file)."""
    raw = read_raw()
    _validate(raw, DEFAULTS, "")
    return _strip_doc_keys(_deep_merge(DEFAULTS, raw))


def _validate(node: Any, defaults: dict[str, Any], prefix: str) -> None:
    if not isinstance(node, dict):
        name = prefix or "<root>"
        raise ConfigError(f"配置项 {name} 必须是对象")
    for key, default in defaults.items():
        if key not in node:
            continue
        value = node[key]
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(default, dict):
            _validate(value, default, path)
            continue
        if isinstance(default, bool):
            if not isinstance(value, bool):
                raise ConfigError(f"配置项 {path} 必须是布尔值")
            continue
        if isinstance(default, int):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ConfigError(f"配置项 {path} 必须是整数")
            if value < 1:
                raise ConfigError(f"配置项 {path} 必须大于等于 1")
            continue
        if not isinstance(value, str):
            raise ConfigError(f"配置项 {path} 必须是字符串")
        if path == "download.backend" and value not in BACKENDS:
            raise ConfigError(
                f"配置项 download.backend 取值无效：{value}"
                f"（可选：{', '.join(BACKENDS)}）"
            )
        if path == "verify.method" and value not in VERIFY_METHODS:
            raise ConfigError(
                f"配置项 verify.method 取值无效：{value}"
                f"（可选：{', '.join(VERIFY_METHODS)}）"
            )
