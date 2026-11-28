"""维护分析：从真正收到过的区间观测推出仍然可能的损耗组合。

严格离线：本模块不读取 ground_truth.json，输入只有
  inputs/patrol_observations.json、branch_switch_log.json、reference_part.json、topology.json
输出：
  outputs/maintenance_basis.json     —— 维护依据（调度器与接入台的唯一事实来源）
  outputs/path_loss_hull.json        —— (P1,P2) 可能组合的凸包顶点
  outputs/handoff_constraints.json   —— 同一接头误差跨多条光路的共享约束（接入台核用）
"""
from __future__ import annotations

import json
import os
import sys
from fractions import Fraction
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from plant import fixed_path_loss, path_loss_expr, PATH_JOINTS  # noqa: E402
from polytope import Polytope  # noqa: E402

F = Fraction
ROOT = os.path.join(os.path.dirname(__file__), "..")
JOINT_IDX = {"j1": 0, "j2": 1, "j3": 2, "j4": 3}


def _coeffs_for(joints: List[str]) -> List[Fraction]:
    row = [F(0)] * 4
    for j in joints:
        row[JOINT_IDX[j]] = F(1)
    return row


def build_polytope(observations: dict, label: str = "patrol") -> Polytope:
    """把每一条区间观测翻成多面体的一对线性半平面。"""
    poly = Polytope(4, name=label)

    # 1) OTDR 回波区间（含同距离格叠合的求和约束）
    for ob in observations["echo_intervals"]:
        lo, hi = ob["loss_interval_db"]
        tag = (
            f"OTDR {ob['end']} 端 / 分支{ob['branch_state']} / "
            f"{ob['distance_bin_km']}km 格 ({'+'.join(ob['joints_in_bin'])})"
        )
        poly.add_interval(_coeffs_for(ob["joints_in_bin"]), F(str(lo)), F(str(hi)), tag)

    # 2) 两端巡测的端到端功率区间：pathloss = 固定项 + 接头项
    for ob in observations["end_to_end_power_intervals"]:
        path = ob["path"]
        lo, hi = ob["pathloss_interval_db"]
        expr = path_loss_expr(path)
        fixed = fixed_path_loss(path)
        tag = f"巡测功率区间 {path}（含固定 {float(fixed):.2f}dB）"
        poly.add_interval(expr, F(str(lo)) - fixed, F(str(hi)) - fixed, tag)

    return poly


def check_reference(ref: dict) -> dict:
    ok = (
        ref["accepted_reading_interval_db"][0]
        <= ref["observed_reading_db"]
        <= ref["accepted_reading_interval_db"][1]
        and ref["observed_distance_bin_km"] == ref["distance_km_known"]
    )
    return {
        "part": ref["reference_part_id"],
        "reading_db": ref["observed_reading_db"],
        "accepted_band_db": ref["accepted_reading_interval_db"],
        "distance_bin_ok": ref["observed_distance_bin_km"] == ref["distance_km_known"],
        "passed": ok,
    }


def check_cross_end_consistency(observations: dict) -> List[dict]:
    """同一物理量在两端/两种分支状态下的独立区间必须相交，否则观测互相矛盾。"""
    index: Dict[Tuple[str, ...], List[dict]] = {}
    for ob in observations["echo_intervals"]:
        key = tuple(sorted(ob["joints_in_bin"]))
        index.setdefault(key, []).append(ob)
    checks = []
    for key, obs in index.items():
        if len(obs) < 2:
            continue
        lo = max(o["loss_interval_db"][0] for o in obs)
        hi = min(o["loss_interval_db"][1] for o in obs)
        checks.append(
            {
                "joint_sum": "+".join(key),
                "independent_intervals": [o["loss_interval_db"] for o in obs],
                "intersection": [lo, hi],
                "consistent": lo <= hi,
            }
        )
    return checks


def analyze(observations: dict, ref: dict, topology: dict, label: str = "patrol") -> dict:
    poly = build_polytope(observations, label=label)
    assert poly.is_feasible(), "观测互斥，可行域为空"

    var_ranges = poly.variable_ranges()
    paths = {}
    hulls = {}
    for path in ("R1", "R2"):
        expr = path_loss_expr(path)
        fixed = fixed_path_loss(path)
        lo_j, hi_j = poly.linear_range(expr)
        paths[path] = {
            "fixed_db": float(fixed),
            "joint_term_range_db": [float(lo_j), float(hi_j)],
            "end_to_end_range_db": [float(lo_j + fixed), float(hi_j + fixed)],
            "passes_through": PATH_JOINTS[path],
        }
        hulls[path] = [float(lo_j + fixed), float(hi_j + fixed)]

    # (P1, P2) 二维投影凸包：完整的“仍然可能”端到端组合
    hull = poly.projected_hull(path_loss_expr("R1"), path_loss_expr("R2"))
    fixed1, fixed2 = fixed_path_loss("R1"), fixed_path_loss("R2")
    hull_abs = [(float(p1 + fixed1), float(p2 + fixed2)) for p1, p2 in hull]

    result = {
        "label": label,
        "polytope_dimension": poly.n,
        "facet_count": len(poly.export_constraints()),
        "vertex_count": len(poly.vertices()),
        "reference_check": check_reference(ref),
        "cross_end_checks": check_cross_end_consistency(observations),
        "joint_loss_ranges_db": {
            k: [float(lo), float(hi)] for k, (lo, hi) in var_ranges.items()
        },
        "paths": paths,
        "path_loss_hull_p1_p2_db": hull_abs,
        "topology_used": topology,
    }
    return result, poly


def main() -> None:
    with open(os.path.join(ROOT, "inputs", "patrol_observations.json")) as fh:
        observations = json.load(fh)
    with open(os.path.join(ROOT, "inputs", "reference_part.json")) as fh:
        ref = json.load(fh)
    with open(os.path.join(ROOT, "inputs", "topology.json")) as fh:
        topology = json.load(fh)
    with open(os.path.join(ROOT, "inputs", "branch_switch_log.json")) as fh:
        switch_log = json.load(fh)

    result, poly = analyze(observations, ref, topology)

    out_dir = os.path.join(ROOT, "outputs")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "maintenance_basis.json"), "w") as fh:
        json.dump(
            {
                **result,
                "branch_switch_events": switch_log["events"],
                "basis_status": "issued",
            },
            fh,
            indent=2,
            ensure_ascii=False,
        )
    with open(os.path.join(out_dir, "path_loss_hull.json"), "w") as fh:
        json.dump(
            {
                "axes": ["P1_end_to_end_loss_to_R1_db", "P2_end_to_end_loss_to_R2_db"],
                "hull_vertices": result["path_loss_hull_p1_p2_db"],
            },
            fh,
            indent=2,
        )
    with open(os.path.join(out_dir, "handoff_constraints.json"), "w") as fh:
        json.dump(
            {
                "_comment": "同一接头变量 x1..x4 在 R1/R2 两条光路上共享；接入台须用这些半平面独立复核，禁止按路径分别假设独立损耗。",
                "variables": ["x1_j1", "x2_j2", "x3_j3", "x4_j4"],
                "half_planes": poly.export_constraints(),
                "path_expressions": {
                    "P1": {"fixed_db": float(fixed_path_loss("R1")), "joint_coeffs": [float(v) for v in path_loss_expr("R1")]},
                    "P2": {"fixed_db": float(fixed_path_loss("R2")), "joint_coeffs": [float(v) for v in path_loss_expr("R2")]},
                },
            },
            fh,
            indent=2,
            ensure_ascii=False,
        )

    print(poly.summary())
    print("\n端到端损耗可能范围：")
    for path, info in result["paths"].items():
        print(f"  {path}: {info['end_to_end_range_db']} dB")
    print(f"\n(P1,P2) 凸包 {len(result['path_loss_hull_p1_p2_db'])} 个顶点，"
          f"参考件校验={'通过' if result['reference_check']['passed'] else '失败'}，"
          f"跨端一致性={all(c['consistent'] for c in result['cross_end_checks'])}")
    print("维护依据已签发 → outputs/maintenance_basis.json")


if __name__ == "__main__":
    main()
