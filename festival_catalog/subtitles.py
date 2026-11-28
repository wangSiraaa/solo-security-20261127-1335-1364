# -*- coding: utf-8 -*-
"""字幕席：行宽、断句与连播单排版。

* 按可读语序排版：RTL 行加 U+2067 隔离符并在尾部复位，避免与拉丁数字串向；
* 先按句读断句，再按可用行宽贪心折行；
* 连播单逐条输出，条目间用空行分隔，并标注来源供核对。
"""

from __future__ import annotations

import re

from .client import ResolveResult

_RTL_ISOLATE = "⁧"   # U+2067 RIGHT-TO-LEFT ISOLATE
_POP_DIRECTIONAL = "⁦"  # U+2066 仅用于显式结束隔离的可读复位标记
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？.!?؟])\s+")


def break_sentences(text: str) -> list[str]:
    parts = [part.strip() for part in _SENTENCE_SPLIT.split(text) if part.strip()]
    return parts or [text.strip()]


def wrap_width(text: str, width: int) -> list[str]:
    """按显示宽度贪心折行（教学模型：1 码位 = 1 列）。"""
    if width <= 0:
        raise ValueError("行宽必须为正数")
    lines: list[str] = []
    for sentence in break_sentences(text):
        current = ""
        for word in sentence.split(" "):
            if not current:
                current = word
            elif len(current) + 1 + len(word) <= width:
                current += " " + word
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
    return lines


def format_line(result: ResolveResult, width: int) -> list[str]:
    lines = wrap_width(result.text, width)
    if result.rtl:
        lines = [_RTL_ISOLATE + line + _POP_DIRECTIONAL for line in lines]
    return lines


def render_playlist(results: list[ResolveResult], width: int) -> str:
    blocks = []
    for index, result in enumerate(results, start=1):
        head = f"#{index} [{result.provenance}] key={result.key} locale={result.locale} n={result.n}"
        body = "\n".join(format_line(result, width))
        blocks.append(head + "\n" + body)
    return "\n\n".join(blocks)
