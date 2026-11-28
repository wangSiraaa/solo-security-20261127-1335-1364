"""离线观测生成器（教具）。

读取 inputs/ground_truth.json 中的虚构真值，按巡测仪的量化宽度生成
*区间型*观测，写入 inputs/patrol_observations.json。后续维护分析
（patrol_analysis.py）只允许接触这些区间，不接触真值。
"""
import json
import os
from fractions import Fraction

import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from plant import (  # noqa: E402
    JOINT_POSITIONS_KM,
    PATH_FIBER_KM,
    PATH_JOINTS,
    PATH_SPLITTER,
    FIBER_LOSS_PER_KM,
    otamar_bins,
    total_path_loss,
    distance_bin,
    DISTANCE_BIN_KM,
)

ROOT = os.path.join(os.path.dirname(__file__), "..")
F = Fraction


def f2s(v: Fraction) -> float:
    return float(v)


def main() -> None:
    with open(os.path.join(ROOT, "inputs", "ground_truth.json")) as fh:
        truth = json.load(fh)
    x = [F(str(truth["true_splice_loss_db"][f"j{i}"])) for i in range(1, 5)]
    h_otdr = F(str(truth["otdr_interval_halfwidth_db"]))
    h_pwr = F(str(truth["patrol_power_halfwidth_db"]))
    launch = F(str(truth["patrol_launch_dbm"]))

    bins = otamar_bins()
    echo_obs = []
    # 教具设定：A 端 2.0km 格在分支 on/off 两态下读数相同 —— 该格内 j2 的主干
    # 回波与分支器反射强耦合，切换分支只改变波形形态、不改变区间读数；
    # 切换记录的作用是确认两次巡测确实发生过状态切换（见 branch_switch_log.json）。
    for end, states in bins.items():
        for state, groups in states.items():
            for bin_label, joints in groups.items():
                true_sum = sum(x[int(j[1]) - 1] for j in joints)
                echo_obs.append(
                    {
                        "end": end,
                        "branch_state": state,
                        "distance_bin_km": float(bin_label),
                        "joints_in_bin": joints,
                        "loss_interval_db": [f2s(true_sum - h_otdr), f2s(true_sum + h_otdr)],
                    }
                )

    # 两端正常巡测：在发射端注入已知巡测功率，读两路径的整体收信功率区间。
    power_obs = []
    for path in ("R1", "R2"):
        pl = total_path_loss(path, x)
        rx = launch - pl
        power_obs.append(
            {
                "path": path,
                "launch_dbm": f2s(launch),
                "received_interval_dbm": [f2s(rx - h_pwr), f2s(rx + h_pwr)],
                "pathloss_interval_db": [f2s(pl - h_pwr), f2s(pl + h_pwr)],
            }
        )

    out = {
        "_comment": "全部为区间观测；同距离格回波已按 OTDR 分辨率盒叠合。",
        "distance_bin_width_km": float(DISTANCE_BIN_KM),
        "echo_intervals": echo_obs,
        "end_to_end_power_intervals": power_obs,
    }
    with open(os.path.join(ROOT, "inputs", "patrol_observations.json"), "w") as fh:
        json.dump(out, fh, indent=2, ensure_ascii=False)

    # 分支切换记录：哪段窗里分支器处于 on/off
    switch_log = {
        "_comment": "分支切换记录：与回波区间的 branch_state 字段对应。",
        "events": [
            {"window": "patrol-1", "state": "on"},
            {"window": "patrol-2", "state": "off"},
            {"window": "patrol-3", "state": "on"},
        ],
    }
    with open(os.path.join(ROOT, "inputs", "branch_switch_log.json"), "w") as fh:
        json.dump(switch_log, fh, indent=2, ensure_ascii=False)

    # 校正参考件：已知反射系数的标准接头，用于核验区间宽度与距离格标定。
    ref = {
        "_comment": "校正参考件：参考损耗 0.50dB，巡测读数应落在给定容差带内。",
        "reference_part_id": "REF-0dB50",
        "known_loss_db": 0.50,
        "accepted_reading_interval_db": [0.42, 0.58],
        "observed_reading_db": 0.47,
        "distance_km_known": 1.5,
        "observed_distance_bin_km": float(distance_bin(JOINT_POSITIONS_KM["j1"])),
    }
    with open(os.path.join(ROOT, "inputs", "reference_part.json"), "w") as fh:
        json.dump(ref, fh, indent=2, ensure_ascii=False)

    # 拓扑描述（距离、固定光纤长度、分支器损耗）——分析端可见的确定量
    topo = {
        "fiber_loss_db_per_km": float(FIBER_LOSS_PER_KM),
        "splitter_loss_db": float(PATH_SPLITTER["R2"]),
        "joint_positions_km": {k: float(v) for k, v in JOINT_POSITIONS_KM.items()},
        "path_fiber_km": {k: float(v) for k, v in PATH_FIBER_KM.items()},
        "path_joints": PATH_JOINTS,
    }
    with open(os.path.join(ROOT, "inputs", "topology.json"), "w") as fh:
        json.dump(topo, fh, indent=2, ensure_ascii=False)

    print("已生成 patrol_observations.json / branch_switch_log.json / reference_part.json / topology.json")


if __name__ == "__main__":
    main()
