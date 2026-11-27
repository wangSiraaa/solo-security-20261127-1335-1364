"""小字库双向文字约定。

约定（本练习的显式规则，不靠系统的隐式 bidi 算法）：
- 两组文字从相反方向读：
  * 选择器自己生成的文字（面板、括号、标记、提示）属 L 组，从左到右；
  * 制包者填写的文字（名字、简介）属 R 组，视觉上从右到左；
- 数字片段永远保持自身顺序，不随组反转；
- 归属先于字形：任何一段 R 组文字在画面和朗读里都带着“发行方填写”的
  来源标记，换行、括号、窄窗口都不能让它与选择器的核验结论连成一句。

可视终端本身不会做 RTL 排版，因此 R 组的“从右到左”在这里落实为：
按小字符为单位把该段整体反转后再放进字符格；朗读文本不反转，改用
显式方向提示 + 逻辑顺序，保证读屏听到的顺序与制包者填写顺序一致。
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

# 窄字符宽 1 格，全角（CJK/全角标点等）宽 2 格。
def char_width(ch: str) -> int:
    if unicodedata.combining(ch):
        return 0
    if unicodedata.east_asian_width(ch) in ("F", "W"):
        return 2
    # 控制字符按 0 处理，其余按 1。
    if unicodedata.category(ch)[0] == "C":
        return 0
    return 1


def text_width(text: str) -> int:
    return sum(char_width(ch) for ch in text)


def _is_digit_run(ch: str) -> bool:
    # 数字片段：ASCII 数字与 CJK 数字都算，保持顺序不反转。
    return ch in "0123456789" or unicodedata.numeric(ch, None) is not None


@dataclass(frozen=True)
class Run:
    text: str
    direction: str  # "L" 选择器文字 | "R" 发行方文字 | "D" 数字片段

    @property
    def width(self) -> int:
        return text_width(self.text)


def _split_runs(text: str, base_dir: str) -> list[Run]:
    """把一段文字切成 普通/数字 小片段，数字独立成段。"""
    runs: list[Run] = []
    buf: list[str] = []
    buf_digit = False

    def flush():
        if buf:
            runs.append(Run("".join(buf), "D" if buf_digit else base_dir))
            buf.clear()

    for ch in text:
        is_d = _is_digit_run(ch)
        if buf and is_d != buf_digit:
            flush()
        buf_digit = is_d
        buf.append(ch)
    flush()
    return runs


def _reverse_keep_digits(text: str) -> str:
    """R 组整体从右到左，但每个数字片段内部顺序保持不变。"""
    pieces = _split_runs(text, "R")
    # 片段顺序反转；数字片段本身不反转，普通片段逐字符反转。
    out: list[str] = []
    for run in reversed(pieces):
        if run.direction == "D":
            out.append(run.text)
        else:
            out.append("".join(reversed(run.text)))
    return "".join(out)


def visual(text: str, direction: str) -> str:
    """渲染进字符格的形态。L 组原样；R 组按约定反转（数字保持顺序）。"""
    if direction == "R":
        return _reverse_keep_digits(text)
    return text


def spoken(text: str, direction: str) -> str:
    """朗读形态：不做字形反转，数字天然保持顺序；R 组带显式方向提示。"""
    if direction == "R":
        return f"〔反向行·发行方原文，逻辑顺序〕{text}"
    return text


@dataclass
class Cell:
    """字符格中的一个格子：字形 + 来源 + 方向，供画面与朗读共同核对。"""

    ch: str
    source: str       # "selector" | "publisher"
    direction: str    # "L" | "R" | "D"
    width: int


def cells(text: str, direction: str, source: str) -> list[Cell]:
    """把一段文字展开成带来源标注的字符格（R 组按视觉顺序展开）。"""
    shown = visual(text, direction)
    out: list[Cell] = []
    for ch in shown:
        d = "D" if _is_digit_run(ch) else direction
        out.append(Cell(ch, source, d, char_width(ch)))
    return out


def wrap_cells(cell_seq: list[Cell], max_width: int) -> list[list[Cell]]:
    """按显示宽度换行。每个续行带续行标记位由调用方处理；
    这里只保证不把一个全角字符拆到两行，且来源标注随格子一起走。"""
    lines: list[list[Cell]] = []
    line: list[Cell] = []
    used = 0
    for c in cell_seq:
        if c.width == 0:
            line.append(c)
            continue
        if used + c.width > max_width and line:
            lines.append(line)
            line = []
            used = 0
        line.append(c)
        used += c.width
    if line or not lines:
        lines.append(line)
    return lines
