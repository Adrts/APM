"""Bucket repositories known to Scoop.

The list lives in apps/apm/buckets.json (kept in sync with Scoop's
buckets.json) so `apm bucket add <name>` works without an explicit
repository URL, and the list can be edited without touching code.
"""

import json
from pathlib import Path

# 程序数据随代码走，不经过 APM_ROOT：位于包目录上一级（apps/apm/buckets.json）。
BUCKETS_JSON = Path(__file__).resolve().parent.parent / "buckets.json"


def _load_known_buckets() -> dict[str, str]:
    try:
        raw = json.loads(BUCKETS_JSON.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"已知源清单缺失：{BUCKETS_JSON}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"已知源清单不可读：{BUCKETS_JSON}（{exc}）") from exc
    if not isinstance(raw, dict) or not all(
        isinstance(name, str) and isinstance(repo, str) and repo
        for name, repo in raw.items()
    ):
        raise RuntimeError(
            f"已知源清单格式错误：{BUCKETS_JSON}（应为「名称 → 仓库地址」的字符串映射）"
        )
    return raw


KNOWN_BUCKETS: dict[str, str] = _load_known_buckets()
