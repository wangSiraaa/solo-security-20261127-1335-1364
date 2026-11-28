"""接入台独立复核：只用移交的半平面重建可行域，核对维护依据给出的范围。

这模拟“把同一处接头误差在多条光路上的约束交给接入台核用”——接入台不持有
维护端的多面体对象，只拿到 handoff_constraints.json 的半平面与路径表达式，
自行构造 LP 求各路径端到端损耗范围，再与 maintenance_basis.json 比对。
"""
from __future__ import annotations

import json
import os
import sys
from fractions import Fraction

sys.path.insert(0, os.path.dirname(__file__))

from fractional_lp import simplex_maximize  # noqa: E402
from plant import fixed_path_loss, path_loss_expr  # noqa: E402

F = Fraction
ROOT = os.path.join(os.path.dirname(__file__), "..")


def main() -> None:
    handoff = json.load(open(os.path.join(ROOT, "outputs", "handoff_constraints.json")))
    basis = json.load(open(os.path.join(ROOT, "outputs", "maintenance_basis.json")))

    A = [[F(str(v)) for v in hp["a"]] for hp in handoff["half_planes"]]
    b = [F(str(hp["b"])) for hp in handoff["half_planes"]]

    print("接入台独立复核（仅用移交半平面，不引用维护端对象）")
    all_match = True
    for path in ("R1", "R2"):
        expr = [F(str(v)) for v in path_loss_expr(path)]
        fixed = fixed_path_loss(path)
        _, hi_j, _ = simplex_maximize(A, b, expr)
        _, neg_lo, _ = simplex_maximize(A, b, [-v for v in expr])
        rebuilt = [float(-neg_lo + fixed), float(hi_j + fixed)]
        issued = basis["paths"][path]["end_to_end_range_db"]
        match = rebuilt == issued
        all_match &= match
        print(f"  {path}: 重建 {rebuilt}  维护依据 {issued}  {'一致' if match else '不一致'}")

    # 共享变量约束完整性：R1 与 R2 表达式确实共用 x1/x2，体现跨光路耦合
    p1 = handoff["path_expressions"]["P1"]["joint_coeffs"]
    p2 = handoff["path_expressions"]["P2"]["joint_coeffs"]
    shared = [i for i in range(4) if p1[i] and p2[i]]
    print(f"  跨光路共享接头变量: x{shared and [i + 1 for i in shared]}")
    assert shared, "两条光路应共享至少一个接头变量"
    assert all_match, "接入台复核发现范围不一致，拒绝采信"
    print("接入台复核通过：半平面可独立重建，范围与维护依据一致。")


if __name__ == "__main__":
    main()
