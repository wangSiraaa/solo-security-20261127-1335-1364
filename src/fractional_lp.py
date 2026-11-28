"""两阶段单纯形（精确分数运算，无第三方依赖）。

求解标准型：
    maximize    c^T x
    subject to  A x <= b
                x >= 0
全部系数使用 fractions.Fraction，避免浮点容差问题。规模很小（个位数变量、
十几条约束），Bland 防循环规则足够。

实现要点（教科书两阶段表）：
  1. 预处理平凡 0 行；b_i<0 的约束整行乘 -1 并补人工变量 a。
  2. 第一阶段最小化 sum(a)：用人工变量做初始基，目标行按基显式消元。
  3. 逐出/封禁人工变量后，第二阶段换真实目标行，重新按当前基消元。
  4. 每次 pivot 同时更新约束表与目标行，目标值始终等于目标行 RHS。
"""
from fractions import Fraction
from typing import List, Sequence, Tuple

INFEASIBLE = "infeasible"
OPTIMAL = "optimal"
UNBOUNDED = "unbounded"

F = Fraction


def _pivot_row(tableau: List[List[F]], row: int, col: int) -> None:
    pv = tableau[row][col]
    tableau[row] = [v / pv for v in tableau[row]]
    for r in range(len(tableau)):
        if r == row:
            continue
        f = tableau[r][col]
        if f != 0:
            tableau[r] = [a - f * q for a, q in zip(tableau[r], tableau[row])]


def simplex_maximize(
    A: Sequence[Sequence[F]],
    b: Sequence[F],
    c: Sequence[F],
) -> Tuple[str, Fraction, List[F]]:
    m, n = len(A), len(c)

    # 预处理平凡约束
    fa, fb = [], []
    for row, rhs in zip(A, b):
        row = [F(v) for v in row]
        rhs = F(rhs)
        if all(v == 0 for v in row):
            if rhs < 0:
                return INFEASIBLE, F(0), [F(0)] * n
            continue
        fa.append(row)
        fb.append(rhs)
    A, b, m = fa, fb, len(fa)

    if m == 0:
        # 只有 x>=0：目标有正系数则无界，否则最优 0
        if any(F(v) > 0 for v in c):
            return UNBOUNDED, F(0), [F(0)] * n
        return OPTIMAL, F(0), [F(0)] * n

    neg = [i for i in range(m) if b[i] < 0]
    k = len(neg)
    ncols = n + m + k
    art_col = {i: n + m + t for t, i in enumerate(neg)}

    tableau: List[List[F]] = []
    basis: List[int] = []
    for i in range(m):
        row = [F(0)] * (ncols + 1)
        for j in range(n):
            row[j] = F(A[i][j])
        row[n + i] = F(1)
        rhs = b[i]
        if i in art_col:
            # 整行乘 -1：结构列与本征松弛列都翻号，再补 +1 人工变量做初始基
            for j in range(n):
                row[j] = -row[j]
            row[n + i] = F(-1)
            row[art_col[i]] = F(1)
            rhs = -rhs
            basis.append(art_col[i])
        else:
            basis.append(n + i)
        row[-1] = rhs
        tableau.append(row)

    def canonical_objective(coeffs: Sequence[F]) -> List[F]:
        """构造 z = coeffs·vars 的目标行 [ -coeffs | 0(RHS) ]，再按当前基消元。
        coeffs 覆盖全部变量列（结构+松弛+人工），未给定列隐式为 0。"""
        obj = [F(0)] * (ncols + 1)
        for j, v in enumerate(coeffs):
            obj[j] = -F(v)
        for r, bc in enumerate(basis):
            f = obj[bc]
            if f != 0:
                obj = [a - f * q for a, q in zip(obj, tableau[r])]
        return obj

    def bland(obj: List[F], blocked: set) -> Tuple[str, List[F]]:
        while True:
            enter = next(
                (j for j in range(ncols) if j not in blocked and obj[j] < 0), None
            )
            if enter is None:
                return OPTIMAL, obj
            leave, best = None, None
            for r in range(m):
                if tableau[r][enter] > 0:
                    ratio = tableau[r][-1] / tableau[r][enter]
                    if best is None or ratio < best or (
                        ratio == best and basis[r] < basis[leave]
                    ):
                        best, leave = ratio, r
            if leave is None:
                return UNBOUNDED, obj
            f = obj[enter]
            _pivot_row(tableau, leave, enter)
            basis[leave] = enter
            if f != 0:
                obj = [a - f * q for a, q in zip(obj, tableau[leave])]

    # ---- 第一阶段：max -sum(人工) ----
    art_set = set(art_col.values())
    if k:
        p1 = [F(0)] * ncols
        for ac in art_set:
            p1[ac] = F(-1)
        obj = canonical_objective(p1)
        status, obj = bland(obj, set())
        if status == UNBOUNDED:
            return INFEASIBLE, F(0), [F(0)] * n
        residual = sum(tableau[r][-1] for r, bc in enumerate(basis) if bc in art_set)
        if residual > 0:
            return INFEASIBLE, F(0), [F(0)] * n
        # 零值人工变量仍在基：换到任一可用非基、非人工列
        base_cols = set(basis)
        for r, bc in enumerate(basis):
            if bc in art_set:
                nxt = next(
                    (j for j in range(n + m) if j not in base_cols and tableau[r][j] != 0),
                    None,
                )
                if nxt is not None:
                    _pivot_row(tableau, r, nxt)
                    base_cols.discard(bc)
                    base_cols.add(nxt)
                    basis[r] = nxt

    # ---- 第二阶段：真实目标 ----
    obj = canonical_objective(c)
    status, obj = bland(obj, art_set)
    if status == UNBOUNDED:
        return UNBOUNDED, F(0), [F(0)] * n

    values = [F(0)] * n
    for r, bc in enumerate(basis):
        if bc < n:
            values[bc] = tableau[r][-1]
    return OPTIMAL, obj[-1], values
