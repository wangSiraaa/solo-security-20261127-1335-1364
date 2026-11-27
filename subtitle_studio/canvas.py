from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class CanvasReadback:
    lock_id: str
    width: int
    height: int
    matrix: list[list[int]]
    ascii_art: str
    unicode_art: str
    filled_cells: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "lock_id": self.lock_id,
            "width": self.width,
            "height": self.height,
            "matrix": self.matrix,
            "ascii": self.ascii_art,
            "canvas": self.unicode_art,
            "filled_cells": self.filled_cells,
        }


class VirtualCanvas:
    """A tiny integer grid.  It never touches a real display or system font."""

    def __init__(self, width: int = 16, height: int = 6):
        self.width = width
        self.height = height
        self.cells: list[list[int]] = [[0 for _ in range(width)] for _ in range(height)]

    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height

    def paint(self, x: int, y: int, value: int = 1) -> None:
        if not self.in_bounds(x, y):
            from .errors import CanvasOverflow

            raise CanvasOverflow(x, y, self.width, self.height)
        self.cells[y][x] = value

    def filled_count(self) -> int:
        return sum(sum(row) for row in self.cells)

    def matrix(self) -> list[list[int]]:
        return [row[:] for row in self.cells]

    def render_ascii(self, filled: str = "#", empty: str = ".") -> str:
        return "\n".join("".join(filled if v else empty for v in row) for row in self.cells)

    def render_unicode(self) -> str:
        return self.render_ascii(filled="█", empty="·")
