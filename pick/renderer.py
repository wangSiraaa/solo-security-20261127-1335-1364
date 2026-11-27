"""字符格画面与可朗读文本（方案一：固定核验区 + 聚焦详情）。

布局分三块，归属在结构上就分开：
┌ 顶栏：选择器自身说明（L 组）
├ 固定核验区：只出现校验席签名得出的结论（L 组），聚焦谁就显示谁的
│   发行方绑定、哈希、钥匙 id；制包者填写的文字一律不进这块。
├ 列表区：每行前缀 gutter 由选择器生成（L 组）；名字/简介是发行方
│   填写（R 组），自带来源括号；窄窗口换行时续行带“发行方续行”标记，
│   信任标记永远只待在 gutter 与固定核验区，不会随名字换行。
└ 底栏：按键帮助（L 组）

可朗读文本与画面同源但独立成行：R 组不做字形反转，按逻辑顺序读，
并在每段前报出来源与方向；核验结论单独成句，句首固定“校验席结论：”，
这样读屏不可能把包名里碰巧出现的“已验证”读成结论。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import bidi
from .model import Entry, Model

GUTTER_WIDTH = 6          # "  [V] " 之类
CONT_MARK = "↳"          # 续行标记（选择器字形，L 组）
PUB_OPEN = "｢"           # 发行方文字边界（全角，视觉上醒目，且属选择器字形）
PUB_CLOSE = "｣"


@dataclass
class Frame:
    width: int
    visual_lines: list[str] = field(default_factory=list)
    # 每行字符格的来源标注，与 visual_lines 等长，供测试/回单核对归属。
    provenance: list[list[tuple[str, str, str]]] = field(default_factory=list)
    speech_lines: list[str] = field(default_factory=list)

    def visual(self) -> str:
        return "\n".join(self.visual_lines)

    def speech(self) -> str:
        return "\n".join(self.speech_lines)


def _pad(line: str, width: int) -> str:
    w = bidi.text_width(line)
    if w >= width:
        return line
    return line + " " * (width - w)


def _wrap_plain(text: str, width: int) -> list[str]:
    """选择器自身的 L 组长行按显示宽度折行，窄窗口下不截断。"""
    lines: list[str] = []
    buf = ""
    used = 0
    for ch in text:
        cw = bidi.char_width(ch)
        if used + cw > width and buf:
            lines.append(buf)
            buf, used = "", 0
        buf += ch
        used += cw
    lines.append(buf)
    return lines or [""]


def _marker(entry: Entry) -> str:
    return {"verified": "[V]", "unverified": "[?]", "stale": "[X]"}[
        entry.verification.status
    ]


def _panel_lines(entry: Entry | None, width: int) -> list[str]:
    title = "固定核验区（来源：校验席签名记录，选择器文字）"
    lines = ["─" * max(1, width), title]
    if entry is None:
        lines.append("（列表为空）")
        return lines
    v = entry.verification
    lines.append(f"校验席结论：{v.label}（状态码 {v.status}）")
    if v.status == "verified":
        lines.append(f"受信发行方：{v.publisher_display_name}（id {v.publisher_id}）")
        lines.append(f"绑定字节：sha256={entry.sha256}")
        lines.append(f"签发钥匙：{v.key_id}  核验时间：{v.verified_at}")
        lines.append(f"核验说明：{v.reason}")
    else:
        lines.append(f"原因：{v.reason}")
        if v.publisher_display_name:
            lines.append(f"（旧记录中的发行方：{v.publisher_display_name}，不构成当前背书）")
        lines.append("安装已被阻止：只有 verified 状态可安装。")
    return lines


def _row_cells(entry: Entry, focused: bool) -> list[bidi.Cell]:
    """一行的字符格序列：gutter(L) + 发行方名字(R, 带边界括号) + 简介(R)。"""
    cursor = "▶" if focused else " "
    mark = _marker(entry)
    seq: list[bidi.Cell] = []
    seq += bidi.cells(f"{cursor} {mark} ", "L", "selector")
    seq += bidi.cells(PUB_OPEN, "L", "selector")
    seq += bidi.cells(entry.display_name, "R", "publisher")
    seq += bidi.cells(PUB_CLOSE, "L", "selector")
    seq += bidi.cells(" 简介:", "L", "selector")
    seq += bidi.cells(PUB_OPEN, "L", "selector")
    seq += bidi.cells(entry.summary, "R", "publisher")
    seq += bidi.cells(PUB_CLOSE, "L", "selector")
    return seq


def _list_lines(model: Model, width: int, list_height: int) -> tuple[list[str], list[list[tuple[str, str, str]]]]:
    cont_prefix = f"{CONT_MARK} "
    # 续行要留出前缀的宽度，首行没有前缀；按最紧的预算折行，
    # 再保证首行也不越界。
    content_width = max(4, width - bidi.text_width(cont_prefix))
    visual: list[str] = []
    provenance: list[list[tuple[str, str, str]]] = []

    def emit(cells_line: list[bidi.Cell], prefix: str):
        text = prefix + "".join(c.ch for c in cells_line)
        prov = ([(ch, "selector", "L") for ch in prefix]
                + [(c.ch, c.source, c.direction) for c in cells_line])
        visual.append(_pad(text, width))
        provenance.append(prov)

    slot = 0
    for idx, entry in enumerate(model.entries):
        if slot >= list_height:
            break
        wrapped = bidi.wrap_cells(_row_cells(entry, idx == model.cursor), content_width)
        first = True
        for part in wrapped:
            if slot >= list_height:
                break
            if first:
                emit(part, "")
                first = False
            else:
                # 续行：选择器前缀明确标注这仍是上一行发行方文字的折行，
                # gutter 位置不放任何信任标记，避免折行被误读成新条目结论。
                emit(part, f"{CONT_MARK} ")
            slot += 1
    if not model.entries:
        visual.append(_pad("（无条目）", width))
        provenance.append([("（无条目）", "selector", "L")])
    return visual, provenance


def render(model: Model, width: int, height: int | None = None) -> Frame:
    width = max(20, width)
    frame = Frame(width=width)
    focus = model.focus()

    header_raw = [
        "本地安装选择器（键盘操作；L=选择器文字左读，R=发行方文字右读，数字保序）",
        "=" * width,
    ]
    header: list[str] = []
    for i, line in enumerate(header_raw):
        if i == 0:
            header.extend(_wrap_plain(line, width))
        else:
            header.append(line)
    header = [_pad(line, width) for line in header]
    panel = [
        _pad(part, width)
        for line in _panel_lines(focus, width)
        for part in _wrap_plain(line, width)
    ]
    raw_list_title = "── 条目列表（｢｣内为制包者填写文字，含“已验证”也只是普通字）──"
    list_title_lines = [
        _pad(part, width) for part in _wrap_plain(raw_list_title, width)
    ]

    used = len(header) + len(panel) + len(list_title_lines) + 2
    list_height = max(1, (height or 24) - used - 2)
    list_vis, list_prov = _list_lines(model, width, list_height)

    footer_raw = [
        "─" * width,
        "↑/↓ 或 k/j 移动 · Enter 安装（仅已验证可装）· r 重新核验 · q 退出",
    ]
    footer: list[str] = []
    for i, line in enumerate(footer_raw):
        if i == 0:
            footer.append(line)
        else:
            footer.extend(_wrap_plain(line, width))
    footer = [_pad(line, width) for line in footer]

    frame.visual_lines = header + panel + list_title_lines + list_vis + footer
    frame.provenance = (
        [[(ch, "selector", "L") for ch in line] for line in header]
        + [[(ch, "selector", "L") for ch in line] for line in panel]
        + [[(ch, "selector", "L") for ch in line] for line in list_title_lines]
        + list_prov
        + [[(ch, "selector", "L") for ch in line] for line in footer]
    )
    frame.speech_lines = speech(model)
    return frame


def speech(model: Model) -> list[str]:
    """完整朗读稿：列表概览 + 聚焦详情 + 操作提示。"""
    lines: list[str] = []
    lines.append(f"本地安装选择器，共 {len(model.entries)} 个条目。")
    for idx, e in enumerate(model.entries):
        focus = "，当前聚焦" if idx == model.cursor else ""
        lines.append(
            f"第 {idx + 1} 项{focus}："
            f"发行方填写的名称{bidi.spoken(e.display_name, 'R')}；"
            f"简介{bidi.spoken(e.summary, 'R')}；"
            f"制包者自称 {bidi.spoken(e.claimed_publisher, 'R')}（自称不作身份依据）。"
        )
        lines.append(
            f"校验席结论：{e.verification.label}，状态码 {e.verification.status}；"
            f"依据哈希 {e.sha256}。"
        )
    focus = model.focus()
    if focus is not None:
        v = focus.verification
        lines.append(f"聚焦详情：{focus.entry_id}。")
        if v.status == "verified":
            lines.append(
                f"校验席结论：已验证。受信发行方 {v.publisher_display_name}，"
                f"id {v.publisher_id}，签发钥匙 {v.key_id}，核验时间 {v.verified_at}。"
            )
            lines.append("该条目可安装，按 Enter 执行。")
        else:
            lines.append(f"校验席结论：{v.label}。{v.reason}")
            lines.append("该条目不可安装，按 Enter 会被拒绝。")
    lines.append("提示：名称或简介中出现的“已验证”是制包者填写的普通文字，"
                 "不是校验席结论；只有以“校验席结论”开头的句子才是信任依据。")
    return lines
