"""curses 键盘界面。只在真正的 TTY 下被导入，脚本/测试环境不依赖它。

按键：↑↓/kj 移动，Enter 安装，r 重新核验，q 退出。
每次移动与每次重算都会刷新固定核验区，并把聚焦项的朗读句追加到
选择器目录下的 speech.log（模拟读屏播报流，可事后回放）。

按键处理集中在 handle_key 纯函数里，不依赖真实终端，因此无 PTY 的
环境也能对键盘状态机做完整测试；run_curses 只负责屏幕与取键。
"""

from __future__ import annotations

import curses
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .installer import InstallError, install
from .model import Model
from .renderer import render


@dataclass
class KeyOutcome:
    quit_requested: bool = False
    installed: dict | None = None
    message: str = ""
    speech: list[str] | None = None


def _focus_speech(model: Model, width: int, height: int) -> list[str]:
    frame = render(model, max(20, width), height)
    return [
        line for line in frame.speech_lines
        if line.startswith("聚焦详情") or line.startswith("校验席结论")
    ]


def append_speech_log(model: Model, selector_dir: Path,
                      width: int, height: int) -> list[str]:
    lines = _focus_speech(model, width, height)
    stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
    with open(selector_dir / "speech.log", "a", encoding="utf-8") as f:
        for line in lines:
            f.write(f"[{stamp}] {line}\n")
    return lines


def handle_key(model: Model, ch: int, width: int, height: int,
               install_root: Path) -> KeyOutcome:
    """键盘状态机：与具体终端解耦，返回该次按键的全部可观察结果。"""
    outcome = KeyOutcome()
    if ch in (ord("q"), ord("Q")):
        outcome.quit_requested = True
        return outcome
    if ch in (curses.KEY_UP, ord("k")):
        model.move(-1)
    elif ch in (curses.KEY_DOWN, ord("j")):
        model.move(1)
    elif ch in (ord("r"), ord("R")):
        model.reload()
        outcome.message = "已按磁盘当前字节重新核验"
    elif ch in (curses.KEY_ENTER, 10, 13):
        focus = model.focus()
        if focus is None:
            return outcome
        frame = render(model, max(20, width), height)
        try:
            result = install(model, focus.entry_id, install_root, frame)
        except InstallError as exc:
            outcome.message = f"安装被拒绝：{exc}"
        else:
            outcome.message = (
                f"已安装 {result.entry_id}，哈希 {result.sha256[:12]}…，"
                f"回单 {result.receipt_path.name}"
            )
            outcome.installed = {
                "action": "installed",
                "entry_id": result.entry_id,
                "receipt": str(result.receipt_path),
                "sha256": result.sha256,
            }
            return outcome
    outcome.speech = append_speech_log(model, model.selector_dir, width, height)
    return outcome


def _draw(stdscr, model: Model) -> object:
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    frame = render(model, w, h)
    for y, line in enumerate(frame.visual_lines[:h]):
        try:
            stdscr.addstr(y, 0, line[:w])
        except curses.error:
            pass
    stdscr.refresh()
    return frame


def run_curses(model: Model) -> dict:
    selector_dir = model.selector_dir

    def _main(stdscr):
        curses.curs_set(0)
        stdscr.keypad(True)
        frame = _draw(stdscr, model)
        append_speech_log(model, selector_dir, curses.COLS, curses.LINES)
        last_message = ""
        while True:
            ch = stdscr.getch()
            if ch == curses.KEY_RESIZE:
                frame = _draw(stdscr, model)
                continue
            outcome = handle_key(
                model, ch, curses.COLS, curses.LINES,
                selector_dir / "installed",
            )
            if outcome.quit_requested:
                return {"action": "quit"}
            if outcome.installed is not None:
                return outcome.installed
            last_message = outcome.message or last_message
            frame = _draw(stdscr, model)
            if last_message:
                try:
                    stdscr.addstr(curses.LINES - 1, 0,
                                  last_message[:curses.COLS - 1])
                    stdscr.clrtoeol()
                except curses.error:
                    pass

    return curses.wrapper(_main)
