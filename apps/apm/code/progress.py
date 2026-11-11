"""Multi-line transient progress area for downloads and extraction.

Renders one row per active task (byte-based or percent-based) as an ANSI
dynamic region, and steps aside whenever the console prints a persistent
log line. Silently disabled when stdout is not a TTY or NO_COLOR is set.
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import sys
import threading
import time
from typing import Iterator

from .util import display_width, fit_text, human_size

BAR_CELLS = 20
BAR_FILL = "█"
BAR_EMPTY = "░"
MIN_INTERVAL = 0.05


def _enable_vt() -> bool:
    if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
        return False
    if os.name != "nt":
        return True
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        return False


class _Row:
    __slots__ = ("label", "done", "total", "percent", "unit")

    def __init__(self, label: str, total: int, unit: str) -> None:
        self.label = label
        self.done = 0
        self.total = total
        self.percent = -1
        self.unit = unit  # "bytes" | "count"

    def render(self, width: int) -> str:
        if self.total > 0:
            percent = min(100, self.done * 100 // self.total)
            if self.unit == "count":
                size = f"{self.done}/{self.total} 项"
            else:
                size = f"{human_size(self.done)}/{human_size(self.total)}"
        elif self.percent >= 0:
            percent = self.percent
            size = ""
        else:
            percent = -1
            size = f"{self.done} 项" if self.unit == "count" else human_size(self.done)
        if percent >= 0:
            filled = BAR_CELLS * percent // 100
            bar = BAR_FILL * filled + BAR_EMPTY * (BAR_CELLS - filled)
            tail = f"[{bar}] {percent:3d}%"
        else:
            tail = ""
        tail = f"{tail}  {size}" if size else tail
        room = width - display_width(tail) - 2
        label = fit_text(self.label, max(room, 10))
        pad = " " * max(1, width - display_width(label) - display_width(tail))
        return f"{label}{pad}{tail}".rstrip()


class ProgressArea:
    """Owns the dynamic progress region and the console print lock."""

    def __init__(self) -> None:
        self.enabled = _enable_vt()
        self._lock = threading.Lock()
        self._rows: dict[str, _Row] = {}
        self._order: list[str] = []
        self._drawn = 0
        self._last_render = 0.0

    # -- lifecycle ---------------------------------------------------------

    def start(self, key: str, label: str, total: int = 0, unit: str = "bytes") -> None:
        if not self.enabled:
            return
        with self._lock:
            if key not in self._rows:
                self._order.append(key)
            self._rows[key] = _Row(label, max(0, total), unit)
            self._render_locked(force=True)

    def update(
        self,
        key: str,
        done: int | None = None,
        total: int | None = None,
        percent: int | None = None,
    ) -> None:
        if not self.enabled:
            return
        with self._lock:
            row = self._rows.get(key)
            if row is None:
                return
            if done is not None:
                row.done = max(0, done)
            if total is not None:
                row.total = max(0, total)
            if percent is not None:
                row.percent = max(-1, min(100, percent))
            self._render_locked(force=False)

    def finish(self, key: str) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._rows.pop(key, None)
            if key in self._order:
                self._order.remove(key)
            self._render_locked(force=True)

    def erase(self) -> None:
        """Clear the dynamic region so a persistent log line can be printed."""
        if not self.enabled:
            return
        with self._lock:
            self._clear_locked()

    @contextlib.contextmanager
    def print_guard(self) -> Iterator[None]:
        """Hold the area lock while printing one persistent log line."""
        with self._lock:
            self._clear_locked()
            yield

    # -- rendering ---------------------------------------------------------

    def _clear_locked(self) -> None:
        if self._drawn:
            # 光标停在区域最后一行；回行首后只需上移 drawn-1 行到区域顶行。
            moves = max(0, self._drawn - 1)
            sys.stdout.write(f"\r\x1b[{moves}A\x1b[J")
            self._drawn = 0
            sys.stdout.flush()

    def _render_locked(self, force: bool) -> None:
        now = time.monotonic()
        if not force and now - self._last_render < MIN_INTERVAL:
            return
        self._last_render = now
        self._clear_locked()
        if not self._order:
            return
        width = max(40, int(os.get_terminal_size().columns) if sys.stdout.isatty() else 80)
        lines = [self._rows[key].render(width) for key in self._order]
        sys.stdout.write("\r\x1b[K" + "\n\x1b[K".join(lines))
        self._drawn = len(lines)
        sys.stdout.flush()


progress = ProgressArea()
