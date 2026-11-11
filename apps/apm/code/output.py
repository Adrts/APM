"""Console output helpers.

着色范围仅限结构性元素：状态行的 ``[apm]`` 前缀按级别着色，表头与
小节标题使用青色加粗并带细分隔线，进度行使用 info 色。消息正文、
表格数据、路径、备注内容与失败明细一律不着色。
"""

from __future__ import annotations

import os
import sys
import threading

from . import messages as msg
from .util import display_width

PREFIX = msg.PREFIX
BODY_INDENT = " " * 6
ITEM_INDENT = "  - "
ITEM_CONT_INDENT = "    "


def _color_enabled() -> bool:
    """与 Python 3.14 argparse 使用同一套颜色启用判定。"""
    try:
        from _colorize import can_colorize
    except ImportError:
        return sys.stdout.isatty() and not os.environ.get("NO_COLOR")
    return can_colorize()


class Console:
    def __init__(self) -> None:
        self.tty = sys.stdout.isatty()
        self.color = _color_enabled()
        self._lock = threading.Lock()

    def _paint(self, code: str, text: str) -> str:
        if not self.color:
            return text
        return f"\x1b[{code}m{text}\x1b[0m"

    def _emit(self, code: str, msg: str, stream) -> None:
        """输出状态行；消息内的后续行按正文缩进对齐，不重复前缀。"""
        lines = str(msg).rstrip("\n").splitlines() or [""]
        prefix = self._paint(code, PREFIX)
        with self._lock:
            print(f"{prefix} {lines[0]}", file=stream, flush=True)
            for line in lines[1:]:
                print(f"{BODY_INDENT}{line}", file=stream, flush=True)

    def info(self, msg: str) -> None:
        self._emit("36", msg, sys.stdout)

    def success(self, msg: str) -> None:
        self._emit("32", msg, sys.stdout)

    def warn(self, msg: str) -> None:
        self._emit("33", msg, sys.stderr)

    def error(self, msg: str) -> None:
        self._emit("31", msg, sys.stderr)

    def heading(self, msg: str) -> None:
        """小节标题或表头：前置空行，青色加粗，下方输出细分隔线。"""
        with self._lock:
            print(flush=True)
            print(self._paint("1;36", msg), flush=True)
            print(self._paint("2", "-" * display_width(msg)), flush=True)

    def plain(self, msg: str = "") -> None:
        with self._lock:
            print(msg, flush=True)

    def summary(self, msg: str) -> None:
        """表格末尾的汇总行，无前缀、无颜色。"""
        self.plain(msg)

    def progress(self, msg: str, final: bool = False) -> None:
        """原地刷新的进度行；仅在 TTY 下输出。"""
        if not self.tty:
            return
        with self._lock:
            end = "\n" if final else ""
            print(f"\r{self._paint('36', msg)}", end=end, flush=True)

    def failure_list(self, failures: list[str]) -> None:
        if not failures:
            return
        self.error(msg.MSG_FAILURE_HEADER)
        with self._lock:
            for item in failures:
                lines = str(item).rstrip("\n").splitlines() or [""]
                print(f"{ITEM_INDENT}{lines[0]}", flush=True)
                for line in lines[1:]:
                    print(f"{ITEM_CONT_INDENT}{line}", flush=True)


console = Console()
