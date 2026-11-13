"""Console output: primitives and transaction display components.

模块分层：

- ``Console``：状态行、着色、事务表、失败清单等输出原语；
- ``Bar``：进度条行的状态与渲染（确定进度按字节推进，未知进度用
  ``<=>`` 弹跳模拟动画）；
- 进度条块：多个进度条共享一块终端区域，用 ANSI 光标序列整体重绘。
  事务步骤（安装/卸载）运行期间的 info 消息就地显示在所属进度条下方，
  步骤完成后定格为历史行；下载条完成后保留在块内，阶段结束时统一定格。

着色范围仅限结构性元素：success / warn / error 状态行正文分别使用绿色、
黄色、红色，info 状态行与消息正文不着色；表头与小节标题使用青色加粗并
带细分隔线，进度条行运行中青色、成功绿色、失败红色，事务表按操作类型给
应用名着色（安装/更新绿色，卸载红色）。表格数据、路径、备注内容与失败
明细一律不着色。
"""

from __future__ import annotations

import os
import sys
import threading
import time

from . import messages as msg
from .util import display_width, fit_text, human_size, pad_text, terminal_columns

BODY_INDENT = " " * 4
ITEM_INDENT = "  - "
ITEM_CONT_INDENT = "    "

COLOR_INFO = "36"
COLOR_SUCCESS = "32"
COLOR_WARN = "33"
COLOR_ERROR = "31"

# 事务表操作色：安装/更新绿色，卸载红色。
COLOR_INSTALL = COLOR_SUCCESS
COLOR_REMOVE = COLOR_ERROR

# 进度条块参数。
BAR_INTERVAL = 0.12
BAR_INNER = 24
BAR_SPEED_WIDTH = 10
BAR_SIZE_WIDTH = 10
# 终端宽度低于该值时不启用进度条（行会折行导致重绘错位），回退为普通状态行。
BAR_MIN_TERMINAL = 84
BAR_DESC_MIN = 15
BAR_SAFETY = 2
BAR_DOWNLOAD_FIXED = 66
BAR_MAX_MESSAGES = 8

# 进度条描述列宽：下载条与步骤条共用同一公式，保证进度块起始列对齐。
# fixed 为该样式 widgets 区的固定宽度（下载条最宽，以它为基准）；
# 描述列不设上限，终端越宽文件名显示越完整。
def _bar_desc_width(width: int) -> int:
    return max(BAR_DESC_MIN, width - BAR_DOWNLOAD_FIXED - BAR_SAFETY)

# ANSI 光标控制（进度条块重绘）。
_CURSOR_UP = "\x1b[{}A"
_CURSOR_COL1 = "\x1b[1G"
_CLEAR_LINE = "\x1b[2K"
_CLEAR_BELOW = "\x1b[0J"


def bar_widget(elapsed: float, pct: int | None) -> str:
    """进度条控件：确定进度填 ``=``，未知进度用 ``<=>`` 弹跳模拟动画。"""
    if pct is not None:
        filled = round(pct / 100 * BAR_INNER)
        return f"[{'=' * filled:<{BAR_INNER}}]"
    pos = int(elapsed * 1000 / 400) % (BAR_INNER - 2)
    return f"[{' ' * pos}<=>{' ' * max(0, BAR_INNER - pos - 3)}]"


def _color_enabled() -> bool:
    """与 Python 3.14 argparse 使用同一套颜色启用判定。"""
    try:
        from _colorize import can_colorize
    except ImportError:
        return sys.stdout.isatty() and not os.environ.get("NO_COLOR")
    return can_colorize()


class Bar:
    """单条进度条行的状态与渲染。

    ``style`` 决定列布局：``download`` 含百分比/速度/字节/用时四组控件，
    ``step`` 仅有百分比与用时。进度未知时用 ``<=>`` 弹跳动画模拟。
    """

    STYLE_DOWNLOAD = "download"
    STYLE_STEP = "step"

    def __init__(self, desc: str, style: str, keep_on_finish: bool) -> None:
        self.desc = desc
        self.style = style
        self.keep_on_finish = keep_on_finish
        self.running = False
        self.begin = time.monotonic()
        self.end: float | None = None
        self.ok: bool | None = None
        self.done = 0
        self.total: int | None = None
        self.speed = 0.0
        self.size_text: str | None = None
        self.messages: list[str] = []
        self._last_done = 0
        self._last_time = self.begin

    # ------------------------------------------------------------ 状态

    def run(self) -> None:
        """开始计时（预分配的等待条在真正开始下载时调用）。"""
        self.running = True
        self.begin = time.monotonic()
        self._last_time = self.begin

    def update(self, done: int, total: int) -> None:
        """推进字节进度（python 后端下载回调），计算瞬时速度的滑动均值。"""
        now = time.monotonic()
        self.done = max(self.done, done)
        if total and total > 0:
            self.total = total
        delta = now - self._last_time
        if delta >= 0.4:
            instant = (self.done - self._last_done) / delta
            self.speed = (
                instant if self.speed <= 0 else self.speed * 0.7 + instant * 0.3
            )
            self._last_done = self.done
            self._last_time = now

    def finish(self, ok: bool, size_text: str | None = None) -> None:
        self.end = time.monotonic()
        self.ok = ok
        if ok and self.total:
            self.done = self.total
        if size_text:
            self.size_text = size_text

    @property
    def finished(self) -> bool:
        return self.end is not None

    def elapsed(self) -> float:
        return (self.end or time.monotonic()) - self.begin

    # ------------------------------------------------------------ 渲染

    def render(self, width: int) -> str:
        desc_width = _bar_desc_width(width)
        if not self.running:
            # 预分配的等待条：静止占位，等待槽位或缓存检查。
            desc = pad_text(fit_text(self.desc, desc_width), desc_width)
            widgets = (
                f"    {bar_widget(0.0, None)} "
                f"{pad_text('--', BAR_SPEED_WIDTH, 'right')} | "
                f"{pad_text('--', BAR_SIZE_WIDTH, 'right')} |         --"
            )
            return f"{desc} {widgets}"
        elapsed = self.elapsed()
        time_text = f"{int(elapsed) // 60:02d}m{int(elapsed) % 60:02d}s"
        if self.style == Bar.STYLE_STEP:
            pct = 100 if (self.finished and self.ok) else None
            percent = f"{pct:3d}%" if pct is not None else "    "
            widgets = f"{percent} {bar_widget(elapsed, pct)} {time_text}"
        else:
            if self.total:
                pct = (
                    100
                    if self.finished and self.ok
                    else min(100, int(self.done * 100 / self.total))
                )
                percent = f"{pct:3d}%"
                speed_text = (
                    f"{human_size(self.speed)}/s" if self.speed > 0 else "--"
                )
                size_text = self.size_text or human_size(self.done)
            else:
                pct = None
                percent = "    "
                speed_text = "--"
                size_text = self.size_text or "--"
            widgets = (
                f"{percent} {bar_widget(elapsed, pct)} "
                f"{pad_text(speed_text, BAR_SPEED_WIDTH, 'right')} | "
                f"{pad_text(size_text, BAR_SIZE_WIDTH, 'right')} | {time_text}"
            )
        desc = pad_text(fit_text(self.desc, desc_width), desc_width)
        return f"{desc} {widgets}"


class Console:
    def __init__(self) -> None:
        self.tty = sys.stdout.isatty()
        self.color = _color_enabled()
        # 明细行开关：仅 --verbose 时输出步骤明细（解压、命令入口等）。
        self.verbose = False
        self._lock = threading.Lock()
        self._bars: list[Bar] = []
        self._block_open = False
        self._block_lines = 0
        self._capture: Bar | None = None
        self._anim_thread: threading.Thread | None = None
        self._anim_stop = threading.Event()

    # ------------------------------------------------------------ 原语

    def _paint(self, code: str, text: str) -> str:
        if not self.color:
            return text
        return f"\x1b[{code}m{text}\x1b[0m"

    def info(self, msg_text: str) -> None:
        with self._lock:
            if self._capture is not None:
                self._capture.messages.append(msg_text)
                self._trim_messages(self._capture)
                self._redraw()
                return
        self._emit(COLOR_INFO, msg_text, sys.stdout)

    def detail(self, msg_text: str) -> None:
        """步骤明细行（解压、命令入口等）：仅 --verbose 时输出。

        verbose 且处于事务步骤进度条内时，明细就地显示在进度条下方并在
        步骤完成后固化为历史行；verbose 且无进度条时按普通 info 行输出；
        默认（非 verbose）一律隐藏。
        """
        with self._lock:
            if self._capture is not None:
                if self.verbose:
                    self._capture.messages.append(msg_text)
                    self._trim_messages(self._capture)
                    self._redraw()
                return
        if self.verbose:
            self._emit(COLOR_INFO, msg_text, sys.stdout)

    def success(self, msg_text: str) -> None:
        self._emit(COLOR_SUCCESS, msg_text, sys.stdout)

    def warn(self, msg_text: str) -> None:
        self._emit(COLOR_WARN, msg_text, sys.stderr)

    def error(self, msg_text: str) -> None:
        self._emit(COLOR_ERROR, msg_text, sys.stderr)

    def _emit(self, code: str, msg_text: str, stream) -> None:
        """输出状态行；块激活时先擦除块，打印后重绘，保证画面一致。"""
        with self._lock:
            self._pause_block()
            self._emit_lines(code, msg_text, stream)
            if self._bars:
                self._redraw()

    def _emit_lines(self, code: str, msg_text: str, stream) -> None:
        """无锁输出状态行；success/warn/error 正文按级别着色，info 不着色。

        消息内的后续行缩进 4 格。
        """
        lines = str(msg_text).rstrip("\n").splitlines() or [""]
        first = lines[0] if code == COLOR_INFO else self._paint(code, lines[0])
        print(first, file=stream, flush=True)
        for line in lines[1:]:
            print(f"{BODY_INDENT}{line}", file=stream, flush=True)

    def heading(self, msg_text: str) -> None:
        """小节标题或表头：前置空行，青色加粗，下方输出细分隔线。"""
        with self._lock:
            self._pause_block()
            print(flush=True)
            print(self._paint("1;36", msg_text), flush=True)
            print(self._paint("2", "-" * display_width(msg_text)), flush=True)
            if self._bars:
                self._redraw()

    def plain(self, msg_text: str = "") -> None:
        with self._lock:
            self._pause_block()
            print(msg_text, flush=True)
            if self._bars:
                self._redraw()

    def confirm(self, msg_text: str = "") -> bool:
        """二次确认提示（参照 DNF 的 Is this ok [y/N]）：回车或非 y 视为取消。"""
        with self._lock:
            self._pause_block()
            try:
                answer = input(f"{msg_text} ").strip().lower()
            except (EOFError, OSError):
                answer = ""
        return answer in ("y", "yes", "是")

    def summary(self, msg_text: str) -> None:
        """表格末尾的汇总行，无前缀、无颜色。"""
        self.plain(msg_text)

    def failure_list(self, failures: list[str]) -> None:
        if not failures:
            return
        with self._lock:
            self._pause_block()
            self._emit_lines(COLOR_ERROR, msg.MSG_FAILURE_HEADER, sys.stderr)
            for item in failures:
                lines = str(item).rstrip("\n").splitlines() or [""]
                print(f"{ITEM_INDENT}{lines[0]}", file=sys.stderr, flush=True)
                for line in lines[1:]:
                    print(
                        f"{ITEM_CONT_INDENT}{line}", file=sys.stderr, flush=True
                    )
            if self._bars:
                self._redraw()

    def _trim_messages(self, bar: Bar) -> None:
        while len(bar.messages) > BAR_MAX_MESSAGES:
            bar.messages.pop(0)

    # ------------------------------------------------------------ 进度条块

    def bar_start(
        self, desc: str, style: str, running: bool = True
    ) -> Bar | None:
        """开始一条进度条；非 TTY 或终端过窄时返回 None，回退为普通状态行。

        ``running=False`` 时先按位置预留条（静止占位，如按编号排队的下载项），
        由 ``bar_run`` 在真正开始时启动计时。
        """
        if not self.tty or terminal_columns() < BAR_MIN_TERMINAL:
            return None
        bar = Bar(desc, style, keep_on_finish=style == Bar.STYLE_DOWNLOAD)
        bar.running = running
        with self._lock:
            self._bars.append(bar)
            if style == Bar.STYLE_STEP:
                self._capture = bar
            self._ensure_anim()
            self._redraw()
        return bar

    def bar_run(self, bar: Bar | None) -> None:
        """启动预分配条的计时与动画。"""
        if bar is None:
            return
        with self._lock:
            bar.run()
            self._redraw()

    def bar_remove(self, bar: Bar | None) -> None:
        """从进度条块中移除未完成的条（如缓存命中无需显示进度）。"""
        if bar is None:
            return
        with self._lock:
            if bar in self._bars:
                self._bars.remove(bar)
                if self._capture is bar:
                    self._capture = None
                if not self._bars:
                    self._pause_block()
                    self._block_open = False
                    self._block_lines = 0
                else:
                    self._redraw()

    def bar_update(self, bar: Bar | None, done: int, total: int) -> None:
        if bar is None:
            return
        with self._lock:
            bar.update(done, total)

    def bar_finish(
        self, bar: Bar | None, ok: bool = True, size_text: str | None = None
    ) -> None:
        if bar is None:
            return
        with self._lock:
            bar.finish(ok, size_text)
            if bar not in self._bars:
                return
            if bar.keep_on_finish:
                # 下载条：定格在块内，等阶段结束统一收口。
                self._redraw()
                return
            # 事务步骤：定格行 + 消息固化为历史行，块清空。
            self._pause_block()
            code = COLOR_SUCCESS if ok else COLOR_ERROR
            print(f"{self._paint(code, bar.render(terminal_columns()))}", flush=True)
            for message in bar.messages:
                self._emit_lines(COLOR_INFO, message, sys.stdout)
            self._bars.remove(bar)
            if self._capture is bar:
                self._capture = None
            if not self._bars:
                self._block_open = False
                self._block_lines = 0
            else:
                self._redraw()

    def bars_close(self, erase: bool = False) -> None:
        """收口进度条块：默认定格为永久输出；erase=True 时直接擦除（取消场景）。"""
        if not self.tty:
            return
        with self._lock:
            if self._bars:
                if erase:
                    self._pause_block()
                else:
                    self._redraw(final=True)
            elif self._block_open and erase:
                self._pause_block()
            elif self._block_open:
                print(flush=True)
            self._bars.clear()
            self._capture = None
            self._block_open = False
            self._block_lines = 0

    def _ensure_anim(self) -> None:
        if self._anim_thread is None:
            self._anim_thread = threading.Thread(
                target=self._anim_loop, daemon=True
            )
            self._anim_thread.start()

    def _anim_loop(self) -> None:
        while not self._anim_stop.wait(BAR_INTERVAL):
            try:
                with self._lock:
                    if self._bars and self._block_open:
                        self._redraw()
            except Exception:  # noqa: BLE001 - 动画线程不中断主流程
                pass

    def _redraw(self, final: bool = False) -> None:
        """整体重绘进度条块；调用方必须已持有锁。"""
        width = terminal_columns()
        lines: list[str] = []
        for bar in self._bars:
            code = COLOR_ERROR if bar.ok is False else (
                COLOR_SUCCESS if bar.finished else COLOR_INFO
            )
            # Bar.render 内部已按终端宽度伸缩描述列，整行不会折行。
            lines.append(self._paint(code, bar.render(width)))
            if bar.messages:
                text_width = max(1, width - 1)
                for message in bar.messages[-BAR_MAX_MESSAGES:]:
                    message_lines = str(message).rstrip("\n").splitlines() or [""]
                    lines.append(fit_text(message_lines[0], text_width))
                    for extra in message_lines[1:]:
                        lines.append(f"{BODY_INDENT}{fit_text(extra, text_width)}")
        if self._block_open and self._block_lines:
            # 注意：ECMA-48 中 CUU 的参数 0 按缺省值 1 处理，上移 0 行时
            # 必须省略序列，否则单行块每帧净上移一行，逐帧吞掉上方日志。
            up = self._block_lines - 1
            out = _CURSOR_UP.format(up) if up > 0 else ""
            for line in lines[:-1]:
                out += f"{_CLEAR_LINE}{_CURSOR_COL1}{line}\n"
            # 尾行后接清除到底：块缩短（如缓存命中移除条）时擦掉底部残留行。
            out += f"{_CLEAR_LINE}{_CURSOR_COL1}{lines[-1]}{_CLEAR_BELOW}"
        else:
            out = "".join(f"{line}\n" for line in lines[:-1])
            out += lines[-1]
        self._block_open = True
        self._block_lines = len(lines)
        if final:
            out += "\n"
        print(out, end="", flush=True)

    def _pause_block(self) -> None:
        """擦除屏幕上的进度条块；调用方必须已持有锁。"""
        if self._block_open and self._block_lines:
            # 同 _redraw：上移 0 行时省略 CUU 序列，避免 \x1b[0A 按 1 处理。
            up = self._block_lines - 1
            seq = (_CURSOR_UP.format(up) if up > 0 else "") + _CURSOR_COL1 + _CLEAR_BELOW
            print(seq, end="", flush=True)
            self._block_open = False
            self._block_lines = 0

    # ------------------------------------------------------------ 事务表

    def transaction_table(
        self,
        columns: list[tuple[str, str]],
        sections: list[tuple[str, list[tuple[str, ...]], str]],
    ) -> None:
        """事务表：等号包裹表头，分节标题 + 行首空格的应用行。

        ``columns`` 为 ``(表头, 对齐)`` 序列；``sections`` 每项为
        ``(分节标题, 行元组列表, 首列颜色)``，空分节自动跳过。
        """
        widths = [display_width(label) for label, _align in columns]
        for index in range(len(columns)):
            for _title, rows, _color in sections:
                for row in rows:
                    widths[index] = max(widths[index], display_width(row[index]))
        widths[0] += 1  # 首列行首带一个空格（事务表缩进）
        rule = "=" * (sum(widths) + 2 * (len(columns) - 1))
        header_cells = []
        for index, (label, align) in enumerate(columns):
            if index == 0:
                header_cells.append(" " + pad_text(label, widths[index] - 1))
            else:
                header_cells.append(pad_text(label, widths[index], align))
        with self._lock:
            self._pause_block()
            print(flush=True)
            print(rule, flush=True)
            print(self._paint("1;36", "  ".join(header_cells)), flush=True)
            print(rule, flush=True)
            for title, rows, color in sections:
                if not rows:
                    continue
                print(title, flush=True)
                for row in rows:
                    cells = []
                    for index, cell in enumerate(row):
                        text = pad_text(
                            cell,
                            widths[index] - (1 if index == 0 else 0),
                            columns[index][1],
                        )
                        cells.append((" " + text) if index == 0 else text)
                    # 先截断再着色：ANSI 码不参与显示宽度计算。
                    line = fit_text("  ".join(cells), terminal_columns())
                    if color and line.startswith(cells[0]):
                        line = self._paint(color, cells[0]) + line[len(cells[0]):]
                    print(line, flush=True)
            print(flush=True)


console = Console()
