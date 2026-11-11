"""Configuration stored in %USERPROFILE%\\.config\\apm\\config.json."""

from __future__ import annotations

import copy
import json
from typing import Any

from . import messages as msg
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
OPTION_DOCS = msg.CONFIG_DOCS


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
                msg.ERR_CONFIG_CREATE.format(
                    path=paths.CONFIG_FILE, error=exc
                )
            ) from exc
        return copy.deepcopy(DEFAULTS)
    try:
        with open(paths.CONFIG_FILE, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            msg.ERR_CONFIG_JSON.format(
                path=paths.CONFIG_FILE,
                line=exc.lineno,
                column=exc.colno,
                error=exc.msg,
            )
        ) from exc
    except (OSError, ValueError) as exc:
        raise ConfigError(
            msg.ERR_CONFIG_READ.format(path=paths.CONFIG_FILE, error=exc)
        ) from exc
    if not isinstance(data, dict):
        raise ConfigError(
            msg.ERR_CONFIG_TOP_LEVEL.format(path=paths.CONFIG_FILE)
        )
    return data


def load() -> dict[str, Any]:
    """Return the effective configuration (defaults merged with the file)."""
    raw = read_raw()
    _validate(raw, DEFAULTS, "")
    return _strip_doc_keys(_deep_merge(DEFAULTS, raw))


def _validate(node: Any, defaults: dict[str, Any], prefix: str) -> None:
    if not isinstance(node, dict):
        name = prefix or "<root>"
        raise ConfigError(msg.ERR_CONFIG_SECTION.format(name=name))
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
                raise ConfigError(msg.ERR_CONFIG_BOOL.format(path=path))
            continue
        if isinstance(default, int):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ConfigError(msg.ERR_CONFIG_INT.format(path=path))
            if value < 1:
                raise ConfigError(msg.ERR_CONFIG_POSITIVE.format(path=path))
            continue
        if not isinstance(value, str):
            raise ConfigError(msg.ERR_CONFIG_STRING.format(path=path))
        if path == "download.backend" and value not in BACKENDS:
            raise ConfigError(
                msg.ERR_CONFIG_BACKEND.format(
                    value=value, choices=" / ".join(BACKENDS)
                )
            )
        if path == "verify.method" and value not in VERIFY_METHODS:
            raise ConfigError(
                msg.ERR_CONFIG_VERIFY.format(
                    value=value, choices=" / ".join(VERIFY_METHODS)
                )
            )
