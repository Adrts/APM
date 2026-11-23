"""APM 本体更新：git fetch + reset，与 bucket 更新共用序列与镜像改写。

本体源以 ``SELF_REPO_URL`` 宏定义写死，不依赖本地 remote 配置；
镜像加速复用同一套 ``url.insteadOf`` 传输时改写规则。

工作流统一为「确定本地哈希 → fetch → 比较哈希 → 事务表+确认 → reset」：

- 本地已纳入 git 管理（``.git`` 存在且 HEAD 可解析）：rev-parse 取本地
  短哈希，与 FETCH_HEAD 比较，相同提示已最新；
- 本地未纳入 git 管理（缺 ``.git`` 或 HEAD 不可解析）：本地哈希视为
  None，无条件先 ``git init``（幂等）再以深度 1 浅拉取本体源，随后走
  与 git 管理分支完全相同的事务表、二次确认与 reset 流程。
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


def _rev_head(git: Path, root: Path) -> str | None:
    """本地短哈希；未纳入 git 管理（缺 .git 或无提交记录）返回 None。"""
    if not (root / ".git").exists():
        return None
    proc = run([str(git), "-C", str(root), "rev-parse", "--short", "HEAD"])
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def _init_repo(git: Path, root: Path) -> None:
    """把未纳入 git 管理的本体目录初始化为仓库（幂等，不产生提交）。"""
    proc = run([str(git), "-C", str(root), "init"])
    if proc.returncode != 0:
        raise SelfUpdateError(
            (proc.stderr or proc.stdout).strip() or msg.ERR_GIT_INIT
        )


def fetch_self(root: Path) -> tuple[str | None, str]:
    """fetch 本体源，返回 (本地短哈希或 None, 新短哈希)；两者相同表示已最新。"""
    git = _git(root)
    current = _rev_head(git, root)
    if current is None:
        _init_repo(git, root)
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
    if old is not None and old == new:
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
                [(SELF_APP_NAME, f"{old or msg.DASH} -> {new}", SELF_REPO_URL)],
                COLOR_INSTALL,
            )
        ],
    )
    console.plain(msg.TRANSACTION_SUMMARY_TITLE)
    console.plain(msg.MSG_TRANS_SUMMARY_SELF.format(count=1))
    console.plain()
    if not assume_yes and not console.confirm(msg.MSG_CONFIRM):
        console.error(msg.MSG_CANCELED)
        return True
    git = _git(root)
    detail = git_reset(git, root)
    if detail:
        console.error(detail)
        return True
    console.success(
        msg.MSG_SELF_UPDATED.format(app=SELF_APP_NAME, old=old or msg.DASH, new=new)
    )
    console.info(msg.MSG_SELF_DEPS_HINT)
    return False
