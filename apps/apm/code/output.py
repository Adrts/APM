"""Console output helpers."""

from __future__ import annotations

import os
import sys
import threading


class Console:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.color = sys.stdout.isatty() and not os.environ.get("NO_COLOR")

    def _paint(self, code: str, text: str) -> str:
        if not self.color:
            return text
        return f"\x1b[{code}m{text}\x1b[0m"

    def _emit(self, prefix: str, msg: str, code: str, stream) -> None:
        with self._lock:
            print(f"{self._paint(code, prefix)} {msg}", file=stream, flush=True)

    def info(self, msg: str) -> None:
        self._emit("[apm]", msg, "36", sys.stdout)

    def success(self, msg: str) -> None:
        self._emit("[apm]", msg, "32", sys.stdout)

    def warn(self, msg: str) -> None:
        self._emit("[apm]", msg, "33", sys.stderr)

    def error(self, msg: str) -> None:
        self._emit("[apm]", msg, "31", sys.stderr)

    def plain(self, msg: str = "") -> None:
        with self._lock:
            print(msg, flush=True)

    def detail(self, msg: str) -> None:
        with self._lock:
            print(f"      {msg}", flush=True)

    def progress(self, msg: str, final: bool = False) -> None:
        """Render a transient progress line (only when attached to a TTY)."""
        if not self.color:
            return
        with self._lock:
            end = "\n" if final else ""
            print(f"\r{msg}", end=end, flush=True)

    def failure_list(self, failures: list[str]) -> None:
        if not failures:
            return
        self.error("以下项目失败：")
        for item in failures:
            self.detail(item)


console = Console()
