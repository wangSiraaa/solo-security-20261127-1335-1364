"""LP / 多面体数值核心的离线自测（无第三方依赖）。

运行：python3 tests/test_lp_core.py
- 手工边界例：最优/不可行/无界/负目标/平凡行
- 2000 个随机可行 LP：可行性、目标值与返回点严格一致、随机可行点最优性
- 多面体顶点枚举与单纯形范围查询的交叉一致性
"""
import os
import random
import sys
from fractions import Fraction

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fractional_lp import simplex_maximize, OPTIMAL, INFEASIBLE, UNBOUNDED  # noqa: E402
from polytope import Polytope  # noqa: E402

F = Fraction


def test_hand_cases() -> None:
    # 负 RHS 两阶段，max x
    st, val, x = simplex_maximize([[-F(1)], [F(1)]], [-F(1, 10), F(85, 100)], [F(1)])
    assert st == OPTIMAL and val == F(85, 100) and x[0] == F(85, 100)
    # 不可行
    assert simplex_maximize([[F(1)], [-F(1)]], [F(1), -F(2)], [F(1)])[0] == INFEASIBLE
    # 无界
    assert simplex_maximize([[-F(1)]], [F(0)], [F(1)])[0] == UNBOUNDED
    # 平凡 0 行
    assert simplex_maximize([[F(0)]], [F(0)], [F(1)])[0] == UNBOUNDED
    assert simplex_maximize([[F(0)]], [-F(1)], [F(1)])[0] == INFEASIBLE
    # 负目标 min x
    st, val, _ = simplex_maximize([[-F(1)]], [-F(1, 10)], [-F(1)])
    assert st == OPTIMAL and val == -F(1, 10)
    print("手工边界例通过")


def test_random_lps() -> None:
    random.seed(11)
    for _ in range(2000):
        n = random.randint(1, 3)
        xstar = [F(random.randint(1, 3)) for _ in range(n)]
        m = random.randint(1, 8)
        A, b = [], []
        for _ in range(m):
            while True:
                row = [F(random.randint(-2, 2)) for _ in range(n)]
                if any(v != 0 for v in row):
                    break
            val = sum(a * q for a, q in zip(row, xstar))
            if random.random() < 0.5:
                slack = F(random.randint(0, 2))
                A.append([-v for v in row])
                b.append(-(val - slack))
            else:
                A.append(row)
                b.append(val + F(random.randint(0, 3)))
        c = [F(random.randint(-2, 2)) for _ in range(n)]
        st, val, x = simplex_maximize(A, b, c)
        if st == UNBOUNDED:
            continue
        assert st == OPTIMAL
        for row, rhs in zip(A, b):
            assert sum(a * q for a, q in zip(row, x)) <= rhs + F(1, 10 ** 9)
        assert all(q >= 0 for q in x)
        assert val == sum(a * q for a, q in zip(c, x))
        for _ in range(6):
            pt = [F(random.randint(0, 4)) for _ in range(n)]
            if all(sum(a * q for a, q in zip(row, pt)) <= rhs for row, rhs in zip(A, b)):
                assert sum(a * q for a, q in zip(c, pt)) <= val + F(1, 10 ** 9)
    print("2000 随机 LP：可行性 + 目标一致 + 最优性探测通过")


def test_polytope_vertices_match_ranges() -> None:
    random.seed(3)
    poly = Polytope(3, "test")
    interior = [F(2), F(2), F(2)]  # 已知内点，所有区间都覆盖它 => 必然可行
    for _ in range(6):
        coeffs = [F(random.randint(-1, 2)) for _ in range(3)]
        if not any(coeffs):
            continue
        center = sum(a * q for a, q in zip(coeffs, interior))
        half = F(random.randint(1, 3))
        poly.add_interval(coeffs, center - half, center + half, "rand")
    assert poly.is_feasible()
    verts = poly.vertices()
    assert verts, "应有顶点"
    for j in range(3):
        e = [F(0)] * 3
        e[j] = F(1)
        lo, hi = poly.linear_range(e)
        col = [v[j] for v in verts]
        assert min(col) == lo and max(col) == hi, (j, lo, hi, min(col), max(col))
    for v in verts:
        assert poly.contains(v)
    print(f"多面体顶点枚举（{len(verts)} 顶点）与单纯形范围交叉一致")


if __name__ == "__main__":
    test_hand_cases()
    test_random_lps()
    test_polytope_vertices_match_ranges()
    print("全部数值核心自测通过")
