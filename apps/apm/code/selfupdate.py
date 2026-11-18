"""APM 本体更新：git fetch + reset，与 bucket 更新共用序列与镜像改写。

本体源以 ``SELF_REPO_URL`` 宏定义写死，不依赖本地 remote 配置；
镜像加速复用同一套 ``url.insteadOf`` 传输时改写规则。
"""

from __future__ import annotations

from pathlib import Path

from . import messages as msg, paths, tools
from .buckets import git_fetch, git_reset
from .errors import SelfUpdateError
from .output import COLOR_INSTALL, console
from .util import run

SELF_REPO_URL = "https://github.com/Adrts/APM.git"
SELF_APP_NAME = "apm"


def _git(root: Path) -> Path:
    git = tools.find_executable("git.exe")
    if not git:
        raise SelfUpdateError(msg.ERR_GIT_MISSING)
    return git


def _rev_short(git: Path, root: Path, ref: str) -> str:
    proc = run([str(git), "-C", str(root), "rev-parse", "--short", ref])
    if proc.returncode != 0:
        raise SelfUpdateError(
            (proc.stderr or proc.stdout).strip() or msg.ERR_GIT_REV
        )
    return proc.stdout.strip()


def fetch_self(root: Path) -> tuple[str, str]:
    """fetch 本体源，返回 (当前短哈希, 新短哈希)；两者相同表示已最新。"""
    if not (root / ".git").exists():
        raise SelfUpdateError(msg.ERR_SELF_NOT_GIT)
    git = _git(root)
    current = _rev_short(git, root, "HEAD")
    detail = git_fetch(git, root, SELF_REPO_URL, SELF_REPO_URL)
    if detail:
        raise SelfUpdateError(detail)
    fetched = _rev_short(git, root, "FETCH_HEAD")
    return current, fetched


def run_self_update(assume_yes: bool) -> bool:
    """本体更新阶段：fetch → 已最新提示 / 事务表+摘要+确认 → reset。

    返回 True 表示失败或确认被取消（调用方据此整体取消，退出码 1）。
    """
    root = paths.ROOT
    try:
        old, new = fetch_self(root)
    except SelfUpdateError as exc:
        console.error(str(exc))
        return True
    if old == new:
        console.info(msg.MSG_APP_LATEST.format(app=SELF_APP_NAME, version=old))
        return False
    console.transaction_table(
        [
            (msg.TABLE_APP, "left"),
            (msg.TABLE_VERSION, "left"),
            (msg.TABLE_SOURCE, "left"),
        ],
        [
            (
                msg.SECTION_SELF_UPDATE,
                [(SELF_APP_NAME, f"{old} -> {new}", SELF_REPO_URL)],
                COLOR_INSTALL,
            )
        ],
    )
    console.plain(msg.TRANSACTION_SUMMARY_TITLE)
    console.plain(msg.MSG_TRANS_SUMMARY_SELF.format(count=1))
    console.plain()
    if not assume_yes and not console.confirm(msg.MSG_CONFIRM):
        console.info(msg.MSG_CANCELED)
        return True
    git = _git(root)
    detail = git_reset(git, root)
    if detail:
        console.error(detail)
        return True
    console.success(
        msg.MSG_SELF_UPDATED.format(app=SELF_APP_NAME, old=old, new=new)
    )
    console.info(msg.MSG_SELF_DEPS_HINT)
    return False
