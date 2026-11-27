#!/usr/bin/env python3
"""Generate the two self-contained declarative sample packages.

Run: python examples/build_sample_packages.py
The generated JSON contains no executable code and reads no system fonts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def squares(rows: list[str]) -> list[dict[str, int]]:
    ops: list[dict[str, int]] = []
    for y, row in enumerate(rows):
        if len(row) != 3:
            raise ValueError(f"sample row must have 3 columns: {row!r}")
        for x, cell in enumerate(row):
            if cell == "#":
                ops.append({"op": "square", "x": x, "y": y})
            elif cell != ".":
                raise ValueError("sample rows may only contain # and .")
    return ops


def branch_width(max_width: int, then_ref: str, else_ref: str) -> dict[str, Any]:
    return {
        "op": "branch",
        "cases": [
            {"when": {"line_width_lte": max_width}, "ops": [{"op": "ref", "ref": then_ref}]},
            {"otherwise": True, "ops": [{"op": "ref", "ref": else_ref}]},
        ],
    }


def build_fangyuan() -> dict[str, Any]:
    primitives: dict[str, Any] = {
        "bar3": squares(["###", "...", "...", "...", "..."]),
        "ask": squares(["###", "..#", ".#.", "...", ".#."]),
        "qi": squares(
            [
                "###",
                "#..",
                "###",
                "#.#",
                "###",
            ]
        ),
        "ming": squares(
            [
                "#.#",
                "###",
                "#.#",
                "###",
                "#.#",
            ]
        ),
        "xue_top": squares(
            [
                "#.#",
                ".#.",
                "###",
                "...",
                "...",
            ]
        ),
        "xue": [
            {"op": "ref", "ref": "bar3", "x": 0, "y": 2},
            {"op": "ref", "ref": "bar3", "x": 0, "y": 4},
            {"op": "square", "x": 0, "y": 0},
            {"op": "square", "x": 2, "y": 0},
            {"op": "square", "x": 1, "y": 1},
        ],
        "xiao": squares(
            [
                "#.#",
                "###",
                "##.",
                "#.#",
                "#.#",
            ]
        ),
        "yong_wide": squares(
            [
                ".#.",
                "#.#",
                ".#.",
                "#.#",
                ".#.",
            ]
        ),
        "yong_narrow": squares(
            [
                "###",
                "#.#",
                "###",
                "#.#",
                "###",
            ]
        ),
        "yong": [branch_width(15, "yong_narrow", "yong_wide")],
        "wang": [
            {"op": "ref", "ref": "bar3", "x": 0, "y": 0},
            {"op": "ref", "ref": "bar3", "x": 0, "y": 2},
            {"op": "ref", "ref": "bar3", "x": 0, "y": 4},
            {"op": "square", "x": 1, "y": 1},
            {"op": "square", "x": 1, "y": 3},
        ],
        "meng_cheap": squares(
            [
                ".#.",
                "###",
                "#.#",
                ".#.",
                ".#.",
            ]
        ),
        # Statically expensive because bar3 is drawn ten times.  At run time
        # this branch is only selected by a huge font size absent from the show.
        "inkstorm": [{"op": "ref", "ref": "bar3", "x": 0, "y": 0} for _ in range(10)],
        "meng": [
            {
                "op": "branch",
                "cases": [
                    {"when": {"font_size_gte": 999}, "ops": [{"op": "ref", "ref": "inkstorm"}]},
                    {"otherwise": True, "ops": [{"op": "ref", "ref": "meng_cheap"}]},
                ],
            }
        ],
    }

    glyph_order = {
        "启": "qi",
        "明": "ming",
        "学": "xue",
        "校": "xiao",
        "永": "yong",
        "王": "wang",
        "梦": "meng",
        "?": "ask",
    }
    return {
        "schema": "grid-font-pack/1",
        "id": "fangyuan",
        "name": "方圆格字模包",
        "version": "1.0.0",
        "fallback": "?",
        "glyphs": {ch: {"primitive": pid, "advance": 4} for ch, pid in glyph_order.items()},
        "primitives": primitives,
    }


def build_gewu() -> dict[str, Any]:
    patterns = {
        "qi2": [
            "###",
            "#.#",
            "###",
            "#..",
            "###",
        ],
        "ming2": [
            "#.#",
            "##.",
            "#.#",
            ".##",
            "#.#",
        ],
        "xue2": [
            "#.#",
            "###",
            ".#.",
            "###",
            "#.#",
        ],
        "xiao2": [
            "#..",
            "##.",
            "#.#",
            "##.",
            "#.#",
        ],
        "yong_wide2": [
            "..#",
            ".#.",
            "###",
            "#..",
            ".#.",
        ],
        "yong_narrow2": [
            "###",
            "#.#",
            ".#.",
            "#.#",
            "###",
        ],
        "ask2": [
            "###",
            "..#",
            ".#.",
            "...",
            ".#.",
        ],
    }
    primitives = {pid: squares(rows) for pid, rows in patterns.items()}
    primitives["yong2"] = [branch_width(15, "yong_narrow2", "yong_wide2")]
    glyph_order = {
        "启": "qi2",
        "明": "ming2",
        "学": "xue2",
        "校": "xiao2",
        "永": "yong2",
        "?": "ask2",
    }
    return {
        "schema": "grid-font-pack/1",
        "id": "gewu",
        "name": "格物像素包",
        "version": "2.1.0",
        "fallback": "?",
        "glyphs": {ch: {"primitive": pid, "advance": 4} for ch, pid in glyph_order.items()},
        "primitives": primitives,
    }


def main() -> None:
    out_dir = Path(__file__).resolve().parents[1] / "packages"
    out_dir.mkdir(exist_ok=True)
    for filename, pack in [("fangyuan.json", build_fangyuan()), ("gewu.json", build_gewu())]:
        path = out_dir / filename
        path.write_text(json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote {path} ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
