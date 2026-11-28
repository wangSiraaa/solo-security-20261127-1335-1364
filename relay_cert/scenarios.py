"""演示场景库：清晰场、争议场、回读翻转场、裁判裁边场。

两个接力队、每队四棒；两面相机看到同一组越线，
B 判线相对 A 有固定行数偏移。真值只供渲染，认证端不可见。
"""
from __future__ import annotations

from .model import CameraTruth, Leg, Scene, TraceSpec

L = 5
WIDTH = 46
HEIGHT = 64
OFFSET = 4


def _legs_ambiguous() -> list[Leg]:
    # 码集分两层：
    # - 每棒都有两处以上独有色，遮住任意一格仍有独有格可锁定（旋转下亦然）；
    # - A2 与 B1 是跨队的同构码，第 4 格被遮时已知四格完全相同，
    #   于是 E2/E3 跨队互换：两种完整组合下第二名的队伍归属不同，
    #   第一名（E1=A1）在所有组合下都确定可署。
    return [
        Leg("A1", "甲", 1, ["R", "R", "G", "Y", "M"]),
        Leg("A2", "甲", 2, ["R", "G", "B", "C", "M"]),
        Leg("A3", "甲", 3, ["G", "Y", "Y", "C", "M"]),
        Leg("A4", "甲", 4, ["B", "B", "Y", "C", "M"]),
        Leg("B1", "乙", 1, ["C", "Y", "R", "M", "G"]),
        Leg("B2", "乙", 2, ["M", "M", "R", "G", "B"]),
        Leg("B3", "乙", 3, ["C", "C", "G", "Y", "M"]),
        Leg("B4", "乙", 4, ["B", "B", "Y", "G", "M"]),
    ]


def _legs_clean() -> list[Leg]:
    legs = _legs_ambiguous()
    # 清晰场把争议码 B1 也改成多格独立的图样，任何转动下都唯一可认。
    legs[4] = Leg("B1", "乙", 1, ["C", "Y", "R", "M", "G"])
    return legs


def _specs(ambiguous: bool) -> list[TraceSpec]:
    if ambiguous:
        # E1 用 A1 的双 R 骨架锁定甲队第一；E7/E8 是两队最后一棒，
        # 都遮住第 4 格后 A4 与 B4 已知四格相同，跨队互换 -> 两种完整组合，
        # 队伍总冠军归属待核，但第一名事件与其余名次仍可署。
        return [
            TraceSpec("E1", "A1", col=4, row0=6, length=8, rotation=0, mask_cell=2),
            TraceSpec("E2", "B1", col=10, row0=12, length=8, rotation=0, mask_cell=None),
            TraceSpec("E3", "A2", col=18, row0=22, length=8, rotation=0, mask_cell=None),
            TraceSpec("E4", "B2", col=26, row0=30, length=8, rotation=0, mask_cell=2),
            # E5/E6 擦肩：相邻列只共一格，共有列在时间窗内画成 X 混色，
            # 两杆各留四格连通独有列，认证端不得把它们并成一条迹。
            TraceSpec("E5", "A3", col=32, row0=38, length=8, rotation=0, mask_cell=None),
            TraceSpec("E6", "B3", col=36, row0=42, length=7, rotation=0, mask_cell=1),
            TraceSpec("E7", "A4", col=12, row0=52, length=8, rotation=0, mask_cell=3),
            TraceSpec("E8", "B4", col=22, row0=60, length=8, rotation=0, mask_cell=3),
        ]
    return [
        TraceSpec("E1", "A1", col=4, row0=6, length=8, rotation=1, mask_cell=2),
        TraceSpec("E2", "B1", col=12, row0=12, length=8, rotation=2, mask_cell=None),
        TraceSpec("E3", "A2", col=20, row0=22, length=8, rotation=0, mask_cell=None),
        TraceSpec("E4", "B2", col=28, row0=30, length=8, rotation=1, mask_cell=2),
        TraceSpec("E5", "A3", col=4, row0=38, length=8, rotation=2, mask_cell=None),
        TraceSpec("E6", "B3", col=14, row0=46, length=8, rotation=0, mask_cell=1),
        TraceSpec("E7", "A4", col=24, row0=54, length=8, rotation=1, mask_cell=None),
        TraceSpec("E8", "B4", col=34, row0=60, length=8, rotation=0, mask_cell=None),
    ]


def build_scene(kind: str) -> Scene:
    ambiguous = kind in ("ambiguous", "flip", "crop")
    legs = _legs_ambiguous() if ambiguous else _legs_clean()
    specs = _specs(ambiguous)
    flipped_b = kind == "flip"
    scene = Scene(
        scene_id=kind,
        width=WIDTH,
        height=HEIGHT + 4,
        line_offset_rows=OFFSET,
        legs=legs,
        camera_a=CameraTruth("A", [TraceSpec(**vars(s)) for s in specs], flipped=False),
        camera_b=CameraTruth("B", [TraceSpec(**vars(s)) for s in specs], flipped=flipped_b),
    )
    return scene
