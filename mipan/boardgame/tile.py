"""带方向的路径片。

每片有 4 个端口：0=北 1=东 2=南 3=西，两两配对接成两条支路。
三种基础配对（type），可旋转（rotation 0..3，顺时针 90° 倍数）。
外观（appearance）只是印在片上的花纹：外观相同的两片，配对可以不同，
这正是题目里"外观相同但连向不同暗区"的两片。
"""

from __future__ import annotations

from dataclasses import dataclass
import random

SIDES = ("N", "E", "S", "W")

# 三种基础配对（rotation=0 时）
BASE_PAIRS = {
    "straight": ((0, 2), (1, 3)),   # 南北直通、东西直通
    "curve": ((0, 1), (2, 3)),      # 东北弯、西南弯
    "cross": ((0, 3), (1, 2)),      # 西北弯、东南弯
}
TILE_TYPES = tuple(BASE_PAIRS.keys())
APPEARANCES = ("木纹", "青石", "苔痕", "铜锈")


@dataclass(frozen=True)
class Tile:
    type: str
    rotation: int = 0
    appearance: str = "木纹"

    def pairs(self) -> tuple[tuple[int, int], tuple[int, int]]:
        r = self.rotation % 4
        return tuple(
            tuple(sorted(((a + r) % 4, (b + r) % 4)))
            for a, b in BASE_PAIRS[self.type]
        )  # type: ignore[return-value]

    def partner(self, side: int) -> int:
        """从某一侧进入，从哪一侧出来。"""
        for a, b in self.pairs():
            if a == side % 4:
                return b
            if b == side % 4:
                return a
        raise ValueError("bad side")

    def glyph(self) -> str:
        return {"straight": "│", "curve": "╮", "cross": "╯"}[self.type]

    def describe(self) -> str:
        return f"{self.appearance}·{self.type}·r{self.rotation}"


def random_tile(rng: random.Random | None = None) -> Tile:
    rng = rng or random
    return Tile(
        type=rng.choice(TILE_TYPES),
        rotation=rng.randrange(4),
        appearance=rng.choice(APPEARANCES),
    )


def tile_to_dict(t: Tile) -> dict:
    return {"type": t.type, "rotation": t.rotation, "appearance": t.appearance}


def tile_from_dict(d: dict) -> Tile:
    return Tile(type=d["type"], rotation=int(d["rotation"]), appearance=d["appearance"])
