"""赛前方案甲：选择能从两面相机各自区别的杆带编码。

代价是明算账的两项有限资源：
* 色格数：色板大小 P 与码长 L；
* 临时补杆数：被选中的杆带若本场磨损/遮死，需要备好的同编码替杆数量。

可判性分级：
* two_camera：两面都看全时，任意两码在允许转动下可区别（0 错配）；
* rotation_mask：任一面单独看、遮住任意一格时仍可区别（更贵但抗遮挡）。
输出选中码、被拒码原因、最小见证格集合与临时补杆清单。
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass

from .certify import band_distance


def all_rotations(code: tuple[str, ...]) -> set[tuple[str, ...]]:
    n = len(code)
    return {tuple(code[(i + s) % n] for i in range(n)) for s in range(n)}


def generate_codes(colors: list[str], length: int) -> list[tuple[str, ...]]:
    """枚举并去掉转动等价的码。"""
    out: list[tuple[str, ...]] = []
    seen_set: set[tuple[str, ...]] = set()
    for raw in itertools.product(colors, repeat=length):
        canon = min(all_rotations(raw))
        if canon not in seen_set:
            seen_set.add(canon)
            out.append(canon)
    return out


def _as_band(code: tuple[str, ...], masked: int | None) -> list[str | None]:
    return [None if i == masked else k for i, k in enumerate(code)]


def distinguish_two_camera(c1: tuple[str, ...], c2: tuple[str, ...]) -> bool:
    """全码可见、允许转动：循环移位后不能完全重合。"""
    return band_distance(list(c1), list(c2)) not in (0,)


def distinguish_masked(c1: tuple[str, ...], c2: tuple[str, ...]) -> bool:
    """任一面单独看，遮住任意一格后仍不得零错配兼容。"""
    for m in range(len(c1)):
        band = _as_band(c1, m)
        if band_distance(band, list(c2)) == 0:
            return False
    return True


def distinguishing_cells(c1: tuple[str, ...], c2: tuple[str, ...]) -> list[int]:
    """在允许转动下，列出能区分两码的色带格（取最佳对齐后的差异格）。"""
    best_shift, best = 0, None
    for shift in range(len(c1)):
        dist = sum(1 for i in range(len(c1)) if c1[(i + shift) % len(c1)] != c2[i])
        if best is None or dist > best:
            best, best_shift = dist, shift
    return [i for i in range(len(c1)) if c1[(i + best_shift) % len(c1)] != c2[i]]


@dataclass
class CodebookPlan:
    colors: list[str]
    length: int
    level: str
    selected: list[list[str]]
    rejected: list[dict]
    spare_poles: list[dict]
    witness_cells: list[dict]
    budget: dict

    def to_dict(self) -> dict:
        return {
            "colors": self.colors,
            "length": self.length,
            "level": self.level,
            "selected_codes": [list(c) for c in self.selected],
            "rejected": self.rejected,
            "spare_poles": self.spare_poles,
            "witness_cells": self.witness_cells,
            "budget": self.budget,
        }


def design_codebook(colors: list[str], length: int, need: int,
                    level: str = "two_camera",
                    spare_per_selected: int = 1,
                    max_codes: int = 400) -> CodebookPlan:
    codes = generate_codes(colors, length)[:max_codes]
    ok = distinguish_masked if level == "rotation_mask" else distinguish_two_camera
    selected: list[tuple[str, ...]] = []
    rejected: list[dict] = []

    def feasible(code: tuple[str, ...], pool: list[tuple[str, ...]]) -> bool:
        return all(ok(code, other) for other in pool)

    # 贪心：每次加入与已选池全可判、且最“稀有”的码（与剩余池冲突最多者优先，
    # 让后续选择空间最大）。
    pool = list(codes)
    while pool and len(selected) < need:
        candidates = [c for c in pool if feasible(c, selected)]
        if not candidates:
            break

        def rarity(c: tuple[str, ...]) -> int:
            return sum(0 if ok(c, other) else 1 for other in pool)

        pick = max(candidates, key=lambda c: (rarity(c), c))
        selected.append(pick)
        pool.remove(pick)
    for code in codes:
        if code in selected:
            continue
        blockers = [list(other) for other in selected if not ok(code, other)]
        rejected.append({"code": list(code),
                         "reason": "与已选码在该可判等级下不可区分" if blockers else "容量外备用",
                         "blockers": blockers})
    if len(selected) < need:
        rejected.append({
            "code": None,
            "reason": f"色格预算 P={len(colors)} L={length} 不足以选出 {need} 个 {level} 码，"
                      f"仅得 {len(selected)} 个；需增加色板或码长",
            "blockers": [],
        })

    spare_poles = [
        {"code": list(code), "spare_poles": spare_per_selected,
         "note": "磨损或遮死时用同编码临时补杆，登记图样不变"}
        for code in selected
    ]
    # 最小见证格：对每对选中码求差异格，再用贪心击中集压缩。
    pair_sets: list[tuple[list[str], list[str], list[int]]] = []
    for c1, c2 in itertools.combinations(selected, 2):
        cells = distinguishing_cells(c1, c2)
        pair_sets.append((list(c1), list(c2), cells))
    uncovered = list(pair_sets)
    witness: list[dict] = []
    while uncovered:
        scores: dict[int, int] = {}
        for _, _, cells in uncovered:
            for cell in cells:
                scores[cell] = scores.get(cell, 0) + 1
        cell = max(scores, key=lambda k: (scores[k], -k))
        covered = [p for p in uncovered if cell in p[2]]
        witness.append({
            "band_cell": cell,
            "covers_pairs": [{"a": a, "b": b} for a, b, _ in covered],
        })
        uncovered = [p for p in uncovered if cell not in p[2]]

    budget = {
        "palette_size": len(colors),
        "code_length": length,
        "needed_codes": need,
        "selected_codes": len(selected),
        "temporary_spare_poles": len(selected) * spare_per_selected,
        "total_poles_on_site": len(selected) * (1 + spare_per_selected),
    }
    return CodebookPlan(colors, length, level, selected, rejected, spare_poles,
                        witness, budget)
