"""接入台总编排与核验（第二阶段）。

前置门禁：outputs/maintenance_basis.json 必须已由 patrol_analysis.py 签发，
否则拒绝执行（对应“签发维护依据后，接入台才收到接收器与收件单”）。

运行四组对照并落盘：
  baseline       ：自适应（选定）与保守两方案，正常接收器
  cf1_reassign   ：单换一笔回波所属分支 → 边缘时隙收回
  cf2_slow_rx    ：仅换慢恢复接收器 → 哪些窗必须留空、另一路哪份件仍按时到
  stress         ：额外访客帧请求，演示拒绝/逾窗核实
"""
from __future__ import annotations

import json
import os
import sys
from fractions import Fraction
from typing import Dict

sys.path.insert(0, os.path.dirname(__file__))

from plant import Receiver, fixed_path_loss, path_loss_expr, TX_LEVELS_DBM  # noqa: E402
from framing import build_payloads, fragment, Frame, verify_manifest  # noqa: E402
from scheduler import (  # noqa: E402
    ReceiverSpec,
    build_conservative,
    build_adaptive,
    check_half_duplex,
    check_deadlines,
)
from training import narrow_with_training, path_ubs, reassign_echo_branch  # noqa: E402
from patrol_analysis import build_polytope  # noqa: E402
from driver import PhysicalPlant, Driver, run_schedule  # noqa: E402

F = Fraction
ROOT = os.path.join(os.path.dirname(__file__), "..")
OUT = os.path.join(ROOT, "outputs")


def _load(path: str):
    with open(path) as fh:
        return json.load(fh)


def gate_check() -> dict:
    basis_path = os.path.join(OUT, "maintenance_basis.json")
    if not os.path.exists(basis_path):
        raise SystemExit(
            "门禁失败：维护依据尚未签发。请先运行 python3 src/patrol_analysis.py"
        )
    return _load(basis_path)


def handoff_cross_check() -> dict:
    """接入台用移交的半平面独立重建多面体，核对与维护依据的范围一致。"""
    handoff = _load(os.path.join(OUT, "handoff_constraints.json"))
    basis = _load(os.path.join(OUT, "maintenance_basis.json"))
    poly = build_polytope(_load(os.path.join(ROOT, "inputs", "patrol_observations.json")),
                          "handoff-rebuild")
    report = {}
    for path in ("R1", "R2"):
        lo, hi = poly.linear_range(path_loss_expr(path))
        got = [float(lo + fixed_path_loss(path)), float(hi + fixed_path_loss(path))]
        expect = basis["paths"][path]["end_to_end_range_db"]
        report[path] = {
            "rebuilt_range_db": got,
            "basis_range_db": expect,
            "match": got == expect,
        }
    report["all_match"] = all(v["match"] for v in report.values())
    return report


def make_receivers(cfg: dict) -> Dict[str, Receiver]:
    return {
        name: Receiver(name, F(str(spec["sensitivity_dbm"])), spec["recovery_slots"])
        for name, spec in cfg.items()
        if name.startswith("R")
    }


def make_frames(manifest: dict) -> tuple:
    payload_dir = os.path.join(OUT, "payloads")
    blobs = {name: open(os.path.join(payload_dir, f"{name}.bin"), "rb").read()
             for name in manifest}
    # 每份件作为一条独立逻辑流的单帧（整份承载，末帧标志置位），
    # 两份目录页各自独立编 seq，避免序号碰撞；调度时隙与帧一一对应。
    cat1 = fragment("catalog_page_1", blobs["catalog_page_1"],
                    len(blobs["catalog_page_1"]), "ARCHIVE", "R1")
    cat2 = fragment("catalog_page_2", blobs["catalog_page_2"],
                    len(blobs["catalog_page_2"]), "ARCHIVE", "R1")
    catalog_frames = cat1 + cat2
    # 两份目录页各占一个独立逻辑件缓冲区，seq 用 0/1 路由，各自都是末帧
    for idx, fr in enumerate(catalog_frames):
        fr.stream = "catalog"
        fr.seq = idx
        fr.is_last = True
    visitor_frames = fragment("visitor", blobs["visitor_sample"],
                              len(blobs["visitor_sample"]), "VISITOR", "R2")
    ack_catalog = [
        Frame("catalog", i, True, f"ACK-CAT-{i+1}".encode(), "R1", "ARCHIVE")
        for i in range(2)
    ]
    ack_visitor = [
        Frame("visitor", 0, True, b"ACK-VIS-0", "R2", "VISITOR")
    ]
    return blobs, catalog_frames, visitor_frames, ack_catalog, ack_visitor


def _specs(receivers: Dict[str, Receiver]) -> Dict[str, ReceiverSpec]:
    return {
        name: ReceiverSpec(name, r.sensitivity_dbm, r.recovery_slots)
        for name, r in receivers.items()
    }


def run_case(
    case_name: str,
    sch,
    receivers: Dict[str, Receiver],
    frames: tuple,
    orders: dict,
    plant: PhysicalPlant,
    manifest: dict,
    blobs: dict,
) -> dict:
    _, catalog_frames, visitor_frames, ack_catalog, ack_visitor = frames
    drivers = {
        "ARCHIVE": Driver("ARCHIVE", "R1", receivers["R1"].sensitivity_dbm,
                          sch.level_table, plant),
        "VISITOR": Driver("VISITOR", "R2", receivers["R2"].sensitivity_dbm,
                          sch.level_table, plant),
    }
    result = run_schedule(
        sch,
        drivers,
        {"catalog": catalog_frames, "visitor": visitor_frames},
        {"catalog": ack_catalog, "visitor": ack_visitor},
        receivers,
        orders,
    )
    # 内容核验：每份件重组体 SHA256 与清单逐项比对
    content = {}
    for item in ["catalog_page_1", "catalog_page_2", "visitor_sample"]:
        info = result["reassembly"][item]
        if info["complete"]:
            info["manifest_verified"] = verify_manifest(blobs[item], manifest[item])
        content[item] = info
    result["content_verification"] = content
    result["half_duplex_violations"] = check_half_duplex(
        sch, receivers["R1"].recovery_slots, receivers["R2"].recovery_slots
    )
    result["deadline_violations"] = check_deadlines(sch)
    result["case"] = case_name
    return result


def main() -> None:
    gate_check()
    observations = _load(os.path.join(ROOT, "inputs", "patrol_observations.json"))
    orders = _load(os.path.join(ROOT, "inputs", "stage2_delivery_orders.json"))
    truth = _load(os.path.join(ROOT, "inputs", "ground_truth.json"))
    plant = PhysicalPlant(os.path.join(ROOT, "inputs", "ground_truth.json"))

    os.makedirs(os.path.join(OUT, "payloads"), exist_ok=True)
    manifest = build_payloads(os.path.join(OUT, "payloads"))
    frames = make_frames(manifest)
    blobs = frames[0]

    # 巡测多面体与训练收窄
    patrol_poly = build_polytope(observations, "patrol")
    patrol_ub = path_ubs(patrol_poly)
    train_h = F(str(truth["training_halfwidth_db"]))
    training_intervals = {
        path: (
            float(plant.true_path_loss(path) - train_h),
            float(plant.true_path_loss(path) + train_h),
        )
        for path in ("R1", "R2")
    }
    trained_poly = narrow_with_training(observations, training_intervals, "trained")
    trained_ub = path_ubs(trained_poly)

    all_reports = {}

    # ---------------- 基线：两方案对比，选定 adaptive ----------------
    recv = make_receivers(_load(os.path.join(ROOT, "inputs", "stage2_receivers.json")))
    specs = _specs(recv)
    cons = build_conservative(patrol_ub, specs,
                              recv["R1"].recovery_slots, recv["R2"].recovery_slots)
    adapt = build_adaptive(patrol_ub, trained_ub, specs,
                           recv["R1"].recovery_slots, recv["R2"].recovery_slots)
    all_reports["baseline_conservative"] = run_case(
        "baseline_conservative", cons, recv, frames, orders, plant, manifest, blobs)
    all_reports["baseline_adaptive"] = run_case(
        "baseline_adaptive", adapt, recv, frames, orders, plant, manifest, blobs)

    # ---------------- 反事实一：回波分支改挂 ----------------
    alt_obs = reassign_echo_branch(observations)
    alt_poly = build_polytope(alt_obs, "cf1-reassigned")
    alt_trained = narrow_with_training(alt_obs, training_intervals, "cf1-trained")
    alt_ub = path_ubs(alt_trained)
    cf1 = build_adaptive(patrol_ub, alt_ub, specs,
                         recv["R1"].recovery_slots, recv["R2"].recovery_slots,
                         echo_signature_match=False)
    all_reports["cf1_echo_reassigned"] = run_case(
        "cf1_echo_reassigned", cf1, recv, frames, orders, plant, manifest, blobs)
    all_reports["cf1_echo_reassigned"]["polytope_ranges_db"] = {
        path: [float(v) for v in alt_poly.linear_range(path_loss_expr(path))]
        for path in ("R1", "R2")
    }

    # ---------------- 反事实二：慢恢复接收器 ----------------
    slow = make_receivers(_load(os.path.join(ROOT, "inputs", "stage2_receivers_slow.json")))
    slow_specs = _specs(slow)
    cf2 = build_adaptive(patrol_ub, trained_ub, slow_specs,
                         slow["R1"].recovery_slots, slow["R2"].recovery_slots)
    all_reports["cf2_slow_receiver"] = run_case(
        "cf2_slow_receiver", cf2, slow, frames, orders, plant, manifest, blobs)
    all_reports["cf2_slow_receiver"]["forced_guards"] = [
        {"slot": op.slot, "station": op.station, "note": op.note}
        for op in sorted(cf2.ops, key=lambda o: o.slot) if op.op == "GUARD"
    ]

    # ---------------- 压力请求：演示拒绝与逾窗核实 ----------------
    stress = build_adaptive(patrol_ub, trained_ub, specs,
                            recv["R1"].recovery_slots, recv["R2"].recovery_slots,
                            stress_request=True)
    all_reports["stress_adaptive"] = run_case(
        "stress_adaptive", stress, recv, frames, orders, plant, manifest, blobs)
    stress_cons = build_conservative(patrol_ub, specs,
                                     recv["R1"].recovery_slots, recv["R2"].recovery_slots)
    all_reports["stress_conservative"] = run_case(
        "stress_conservative", stress_cons, recv, frames, orders, plant, manifest, blobs)

    all_reports["_handoff_cross_check"] = handoff_cross_check()
    # 物理层独立核验：隐藏真值必须落在维护分析推出的可行域内（分析不得把真值排除）
    truth_x = [F(str(truth["true_splice_loss_db"][f"j{i}"])) for i in range(1, 5)]
    all_reports["_truth_containment"] = {
        "truth_inside_patrol_polytope": patrol_poly.contains(truth_x),
        "truth_inside_trained_polytope": trained_poly.contains(truth_x),
    }
    assert all_reports["_truth_containment"]["truth_inside_patrol_polytope"], \
        "维护可行域错误地排除了物理真值"
    assert all_reports["_truth_containment"]["truth_inside_trained_polytope"], \
        "训练收窄后的可行域错误地排除了物理真值"
    all_reports["_training_intervals_db"] = training_intervals
    all_reports["_patrol_ubs_db"] = {k: float(v) for k, v in patrol_ub.items()}
    all_reports["_trained_ubs_db"] = {k: float(v) for k, v in trained_ub.items()}
    all_reports["_tx_levels_dbm"] = [float(v) for v in TX_LEVELS_DBM]

    with open(os.path.join(OUT, "verification_report.json"), "w") as fh:
        json.dump(all_reports, fh, indent=2, ensure_ascii=False, default=float)
    print("核验报告已写出 → outputs/verification_report.json")
    _print_summary(all_reports)


def _print_summary(rep: dict) -> None:
    for key in ["baseline_conservative", "baseline_adaptive",
                "cf1_echo_reassigned", "cf2_slow_receiver",
                "stress_adaptive", "stress_conservative"]:
        r = rep[key]
        cv = r["content_verification"]
        print(f"\n== {key} ==")
        print(f"  访客容量时隙={r['visitor_capacity_slots']} "
              f"收回={r['reclaimed']} 拒绝={r['rejected']}")
        for item, info in cv.items():
            print(f"  {item}: 完整重组={info['complete']} "
                  f"清单核验={info.get('manifest_verified')}")
        print(f"  回执 catalog={r['ack_seqs']['catalog']} "
              f"visitor={r['ack_seqs']['visitor']}")
        print(f"  逾窗={r['over_window']} 半双工违规={r['half_duplex_violations']}")
    hc = rep["_handoff_cross_check"]
    print("\n接入台半平面独立复核：", "全部一致" if hc["all_match"] else "不一致")


if __name__ == "__main__":
    main()
