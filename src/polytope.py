"""损耗可行域：区间线性约束构成的分数坐标凸多面体。

变量 x = (x1..xk) 为各接头的真实插入损耗（dB），约束形如
    lo <= a^T x <= hi            （回波区间、巡测功率区间、训练脉冲区间）
另含 x >= 0。提供：
  - 单变量 / 线性组合的精确上下界（两阶段单纯形）
  - 全部顶点枚举（活动约束联立 + 可行性校验）
  - 到端到端损耗对 (P1, P2) 的投影凸包
  - 追加训练脉冲观测后的收束副本
"""
from __future__ import annotations

from fractions import Fraction
from itertools import combinations
from typing import Dict, List, Sequence, Tuple

from fractional_lp import simplex_maximize, INFEASIBLE

F = Fraction


class Polytope:
    def __init__(self, n: int, name: str = ""):
        self.n = n
        self.name = name
        self._A: List[List[F]] = []
        self._b: List[F] = []
        self._tag: List[str] = []
        for j in range(n):  # -x_j <= 0 等价 x_j >= 0
            row = [F(0)] * n
            row[j] = F(-1)
            self._A.append(row)
            self._b.append(F(0))
            self._tag.append(f"nonneg x{j+1}")

    # ---------- 构造 ----------
    def add_interval(
        self,
        coeffs: Sequence[F],
        lo: F,
        hi: F,
        tag: str,
    ) -> None:
        coeffs = [F(v) for v in coeffs]
        # a^T x <= hi
        self._A.append(coeffs.copy())
        self._b.append(F(hi))
        self._tag.append(f"{tag} <= {float(hi):.4f}")
        # -a^T x <= -lo
        self._A.append([-v for v in coeffs])
        self._b.append(F(-lo))
        self._tag.append(f"{tag} >= {float(lo):.4f}")

    def copy(self) -> "Polytope":
        p = Polytope.__new__(Polytope)
        p.n = self.n
        p.name = self.name
        p._A = [row.copy() for row in self._A]
        p._b = self._b.copy()
        p._tag = self._tag.copy()
        return p

    # ---------- 查询 ----------
    def linear_range(self, coeffs: Sequence[F]) -> Tuple[F, F]:
        coeffs = [F(v) for v in coeffs]
        s_hi, v_hi = simplex_maximize(self._A, self._b, coeffs)[1:]
        s_lo, v_lo = simplex_maximize(self._A, self._b, [-v for v in coeffs])[1:]
        return (-s_lo, s_hi)

    def variable_ranges(self) -> Dict[str, Tuple[F, F]]:
        out: Dict[str, Tuple[F, F]] = {}
        for j in range(self.n):
            e = [F(0)] * self.n
            e[j] = F(1)
            out[f"x{j+1}"] = self.linear_range(e)
        return out

    def is_feasible(self) -> bool:
        status, _, _ = simplex_maximize(self._A, self._b, [F(0)] * self.n)
        return status != INFEASIBLE

    def contains(self, point: Sequence[F], eps: F = F(1, 1000000)) -> bool:
        point = [F(v) for v in point]
        for row, rhs in zip(self._A, self._b):
            if sum(a * x for a, x in zip(row, point)) > rhs + eps:
                return False
        return True

    # ---------- 顶点与投影 ----------
    def vertices(self) -> List[Tuple[F, ...]]:
        """枚举 n 个活动超平面的交点，保留可行点并去重。"""
        facets = list(range(len(self._A)))
        found: Dict[Tuple[F, ...], None] = {}
        for combo in combinations(facets, self.n):
            mat = [self._A[i] for i in combo]
            rhs = [self._b[i] for i in combo]
            sol = _solve_square(mat, rhs)
            if sol is None:
                continue
            if self.contains(sol):
                found[tuple(sol)] = None
        return list(found.keys())

    def projected_hull(self, c1: Sequence[F], c2: Sequence[F]) -> List[Tuple[F, F]]:
        """把顶点投影到 (c1·x, c2·x)，返回二维凸包（逆时针，分数坐标）。"""
        pts = {
            (
                sum(a * x for a, x in zip(c1, v)),
                sum(a * x for a, x in zip(c2, v)),
            )
            for v in self.vertices()
        }
        return _convex_hull(list(pts))

    # ---------- 导出给接入台 ----------
    def export_constraints(self) -> List[Dict]:
        """同一接头变量在多条光路上共享：约束原样移交接入台独立复核。"""
        rows = []
        for row, rhs, tag in zip(self._A, self._b, self._tag):
            rows.append(
                {
                    "a": [float(v) for v in row],
                    "b": float(rhs),
                    "source": tag,
                }
            )
        return rows

    def summary(self) -> str:
        lines = [f"多面体 {self.name or '(unnamed)'}: {len(self._A)} 个半平面, {len(self.vertices())} 个顶点"]
        for name, (lo, hi) in self.variable_ranges().items():
            lines.append(f"  {name} ∈ [{float(lo):.4f}, {float(hi):.4f}] dB")
        return "\n".join(lines)


def _solve_square(mat: List[List[F]], rhs: List[F]) -> List[F] | None:
    n = len(mat)
    aug = [row[:] + [rhs[i]] for i, row in enumerate(mat)]
    row = 0
    for col in range(n):
        pivot = next((r for r in range(row, n) if aug[r][col] != 0), None)
        if pivot is None:
            return None
        aug[row], aug[pivot] = aug[pivot], aug[row]
        pv = aug[row][col]
        aug[row] = [v / pv for v in aug[row]]
        for r in range(n):
            if r != row and aug[r][col] != 0:
                f = aug[r][col]
                aug[r] = [a - f * b for a, b in zip(aug[r], aug[row])]
        row += 1
    return [aug[i][-1] for i in range(n)]


def _convex_hull(pts: List[Tuple[F, F]]) -> List[Tuple[F, F]]:
    """Andrew 单调链（分数坐标，含共线剔除）。"""
    pts = sorted(set(pts))
    if len(pts) <= 1:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: List[Tuple[F, F]] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: List[Tuple[F, F]] = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]
