# -*- coding: utf-8 -*-
"""
rechain.charts —— 纯标准库 SVG 折线趋势图。

每个序列一个独立面板，面板宽高固定（两本笔记的图天然同高），
各面板按自身量程独立定标，并在轴上标明基准单位；
另附 ASCII 等高小图，供无界面环境核对形状与数值。
"""
from __future__ import annotations

import html
from typing import Any, Dict, List, Sequence

from . import table as T

PANEL_W, PANEL_H = 720, 240
MARGIN_L, MARGIN_R, MARGIN_T, MARGIN_B = 78, 24, 34, 46
GAP = 26


def _series_points(tbl: "T.Table", x: str, col: str):
    xi, yi = tbl.col_index(x), tbl.col_index(col)
    pts = [(str(r[xi]), float(r[yi])) for r in tbl.rows]
    return pts


def _polyline(pts, panel_top: int) -> (str, List[str]):
    n = len(pts)
    plot_w = PANEL_W - MARGIN_L - MARGIN_R
    plot_h = PANEL_H - MARGIN_T - MARGIN_B
    ys = [p[1] for p in pts]
    ymin, ymax = min(ys), max(ys)
    span = (ymax - ymin) or 1.0
    coords, labels = [], []
    for i, (lab, y) in enumerate(pts):
        cx = MARGIN_L + (plot_w * i / max(n - 1, 1))
        cy = MARGIN_T + plot_h * (1 - (y - ymin) / span) + panel_top
        coords.append((cx, cy))
        labels.append(lab)
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    # y 轴刻度（4 档，基准单位）
    ticks = []
    for k in range(4):
        frac = k / 3
        val = ymax - frac * span
        ty = MARGIN_T + plot_h * frac + panel_top
        ticks.append((MARGIN_L - 8, ty, val))
    return line, labels, coords, ticks, (ymin, ymax)


def write_svg(tbl: "T.Table", x: str, series: Sequence[str],
              path: str, title: str = "") -> None:
    height = len(series) * PANEL_H + (len(series) - 1) * GAP + 70
    parts: List[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{PANEL_W}" height="{height}" '
        f'viewBox="0 0 {PANEL_W} {height}" font-family="sans-serif" font-size="12">',
        f'<text x="{PANEL_W/2}" y="24" text-anchor="middle" font-size="16" font-weight="bold">'
        f'{html.escape(title)}</text>',
    ]
    for pi, col in enumerate(series):
        top = 46 + pi * (PANEL_H + GAP)
        pts = _series_points(tbl, x, col)
        line, xlabels, coords, ticks, yrange = _polyline(pts, top)
        unit = tbl.units.get(col) or "-"
        parts.append(f'<g>')
        parts.append(f'<rect x="{MARGIN_L}" y="{top+MARGIN_T}" width="{PANEL_W-MARGIN_L-MARGIN_R}" '
                     f'height="{PANEL_H-MARGIN_T-MARGIN_B}" fill="#fafafa" stroke="#ccc"/>')
        parts.append(f'<text x="{MARGIN_L}" y="{top+18}" font-weight="bold">'
                     f'{html.escape(col)} <tspan fill="#666">({html.escape(str(unit))}, 基准单位)</tspan></text>')
        for tx, ty, val in ticks:
            parts.append(f'<line x1="{tx+4}" y1="{ty:.1f}" x2="{tx+8}" y2="{ty:.1f}" stroke="#888"/>')
            parts.append(f'<text x="{tx-2}" y="{ty+4:.1f}" text-anchor="end" fill="#555">'
                         f'{T.canon_num(val)}</text>')
        parts.append(f'<polyline fill="none" stroke="#1f5fbf" stroke-width="2" points="{line}"/>')
        for (cx, cy), (lab, val) in zip(coords, pts):
            parts.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="3" fill="#1f5fbf"/>')
            parts.append(f'<text x="{cx:.1f}" y="{top+PANEL_H-14}" text-anchor="middle">{html.escape(lab)}</text>')
            parts.append(f'<text x="{cx:.1f}" y="{cy-8:.1f}" text-anchor="middle" fill="#333">'
                         f'{T.canon_num(val)}</text>')
        parts.append('</g>')
    parts.append('</svg>')
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))


def ascii_chart(tbl: "T.Table", x: str, series: Sequence[str],
                width: int = 48, rows: int = 8) -> str:
    """终端等高 ASCII 趋势图：每个序列 rows 行，两图行数一致即可直接比形状。"""
    out: List[str] = []
    for col in series:
        pts = _series_points(tbl, x, col)
        ys = [p[1] for p in pts]
        ymin, ymax = min(ys), max(ys)
        span = (ymax - ymin) or 1.0
        unit = tbl.units.get(col) or "-"
        out.append(f"{col} ({unit}, 基准单位)  range=[{T.canon_num(ymin)}, {T.canon_num(ymax)}]")
        grid = [[" "] * width for _ in range(rows)]
        for i, (_, y) in enumerate(pts):
            cx = int((width - 1) * i / max(len(pts) - 1, 1))
            cy = int((rows - 1) * (1 - (y - ymin) / span))
            grid[cy][cx] = "o"
        for r in grid:
            out.append("  |" + "".join(r) + "|")
        out.append("  +" + "-" * width + "+")
        out.append("   " + " ".join(f"{p[0]:>{max(1, (width-1)//max(len(pts)-1,1))}}"
                                    for p in pts)[:width + 8])
    return "\n".join(out)
