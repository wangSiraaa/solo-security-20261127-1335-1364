"""接收窗短训练脉冲：用一次端到端收信读数收窄整体损耗范围。

训练脉冲不揭示各接头单独损耗（回波歧义仍在），只在多面体上追加两条
端到端区间约束，得到收窄后的可行域。所有数值仍以分数精确处理。
另提供反事实构造：单换一笔回波所属分支后的观测集。
"""
from __future__ import annotations

import json
import os
import sys
from fractions import Fraction
from typing import Dict, Tuple

sys.path.insert(0, os.path.dirname(__file__))

from plant import fixed_path_loss, path_loss_expr  # noqa: E402
from patrol_analysis import build_polytope  # noqa: E402
from polytope import Polytope  # noqa: E402

F = Fraction
ROOT = os.path.join(os.path.dirname(__file__), "..")


def narrow_with_training(
    observations: dict,
    training_pathloss: Dict[str, Tuple[float, float]],
    label: str = "trained",
) -> Polytope:
    """在巡测多面体上追加训练脉冲的端到端损耗区间（lo,hi）。"""
    poly = build_polytope(observations, label=label)
    for path, (lo, hi) in training_pathloss.items():
        expr = path_loss_expr(path)
        fixed = fixed_path_loss(path)
        poly.add_interval(
            expr,
            F(str(lo)) - fixed,
            F(str(hi)) - fixed,
            f"训练脉冲 {path}",
        )
    assert poly.is_feasible(), "训练脉冲观测与巡测可行域冲突"
    return poly


def path_ubs(poly: Polytope) -> Dict[str, Fraction]:
    return {
        path: fixed_path_loss(path) + poly.linear_range(path_loss_expr(path))[1]
        for path in ("R1", "R2")
    }


def reassign_echo_branch(observations: dict) -> dict:
    """反事实一：单换一笔回波所属的分支。

    把 A 端 on 态 2.0km 格中记为 (j2+j4) 的叠合回波，改挂为分支臂单独承载
    （即按 j4 solo 解释），其余观测不变。这是维护员对同一组原始读数的
    另一种归属假设，用于检验边缘时隙是否需要收回。
    """
    alt = json.loads(json.dumps(observations))
    for ob in alt["echo_intervals"]:
        if (
            ob["end"] == "A"
            and ob["branch_state"] == "on"
            and ob["distance_bin_km"] == 2.0
            and ob["joints_in_bin"] == ["j2", "j4"]
        ):
            ob["joints_in_bin"] = ["j4"]
            ob["_reassigned"] = "原 (j2+j4) 叠合回波改挂为 j4 solo"
    return alt
