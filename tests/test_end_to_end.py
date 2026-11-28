"""端到端离线回归：观测→多面体→维护依据→调度→驱动→重组核验→反事实。

运行：python3 tests/test_end_to_end.py
依次调用各阶段脚本入口，断言题目要求的关键性质全部成立。
"""
import json
import os
import subprocess
import sys
from fractions import Fraction

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))


def _run(script: str) -> None:
    r = subprocess.run(
        [sys.executable, os.path.join(ROOT, "src", script)],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert r.returncode == 0, f"{script} 失败:\n{r.stderr}"


def test_full_pipeline() -> None:
    _run("patrol_analysis.py")
    _run("verify.py")

    basis = json.load(open(os.path.join(ROOT, "outputs", "maintenance_basis.json")))
    report = json.load(open(os.path.join(ROOT, "outputs", "verification_report.json")))

    # 1) 歧义真实存在：接头损耗范围有非零宽度
    for name, rng in basis["joint_loss_ranges_db"].items():
        if name in ("x2", "x3", "x4"):
            assert rng[1] - rng[0] > 0.1, f"{name} 歧义域过窄: {rng}"

    # 2) 端到端损耗是有面积的凸包（>=3 顶点）
    assert len(basis["path_loss_hull_p1_p2_db"]) >= 3

    # 3) 参考件与跨端一致性通过
    assert basis["reference_check"]["passed"]
    assert all(c["consistent"] for c in basis["cross_end_checks"])

    # 4) 接入台独立复核一致
    assert report["_handoff_cross_check"]["all_match"]

    # 4b) 物理真值必须落在推出的可行域内
    assert report["_truth_containment"]["truth_inside_patrol_polytope"]
    assert report["_truth_containment"]["truth_inside_trained_polytope"]

    # 5) 选定自适应方案容量高于保守方案，且全部件按时重组、清单核验通过
    base = report["baseline_adaptive"]
    cons = report["baseline_conservative"]
    assert base["visitor_capacity_slots"] > cons["visitor_capacity_slots"]
    for item, info in base["content_verification"].items():
        assert info["complete"] and info["manifest_verified"], item
    assert not base["over_window"]

    # 6) 自适应访客档低于保守档（训练收束后可降档）
    assert base["level_table"]["visitor"] < cons["level_table"]["visitor"]

    # 7) 反事实一：回波改挂导致边缘时隙收回，承诺件仍按时
    cf1 = report["cf1_echo_reassigned"]
    assert cf1["reclaimed"], "回波改挂应触发边缘收回"
    assert cf1["visitor_capacity_slots"] < base["visitor_capacity_slots"]
    for info in cf1["content_verification"].values():
        assert info["complete"]

    # 8) 反事实二：慢接收器强制留空 ACK 窗并登记逾窗，目录页仍按时
    cf2 = report["cf2_slow_receiver"]
    assert cf2["over_window"], "慢接收器应产生留空逾窗"
    assert cf2["over_window"][0]["kind"] == "ack"
    assert cf2["content_verification"]["catalog_page_1"]["complete"]
    assert cf2["content_verification"]["catalog_page_2"]["complete"]
    assert cf2["content_verification"]["visitor_sample"]["complete"]
    assert cf2["ack_seqs"]["visitor"] == []  # 访客回执未能在窗内完成

    # 9) 压力请求被显式拒绝（不全空闲、不全拒、不增大功率）
    assert report["stress_adaptive"]["rejected"]

    # 10) 任何方案都无半双工恢复违规；正常接收器方案无截止期违规
    for key, r in report.items():
        if isinstance(r, dict) and "half_duplex_violations" in r:
            assert not r["half_duplex_violations"], key
    for key in ("baseline_adaptive", "baseline_conservative", "cf1_echo_reassigned"):
        assert not report[key]["deadline_violations"], (key, report[key]["deadline_violations"])

    print("端到端回归全部断言通过")


def test_gate_blocks_without_basis() -> None:
    """维护依据缺失时接入台必须拒绝执行。"""
    basis = os.path.join(ROOT, "outputs", "maintenance_basis.json")
    backup = basis + ".bak"
    os.rename(basis, backup)
    try:
        r = subprocess.run(
            [sys.executable, os.path.join(ROOT, "src", "verify.py")],
            cwd=ROOT, capture_output=True, text=True,
        )
        assert r.returncode != 0 and "门禁失败" in r.stderr
    finally:
        os.rename(backup, basis)
    print("维护依据门禁测试通过")


def test_half_duplex_checker_detects_violation() -> None:
    from scheduler import SlotOp, Schedule, check_half_duplex
    from fractions import Fraction as F
    bad = Schedule("probe")
    bad.ops = [
        SlotOp(13, "TX", "VISITOR", "visitor", 0, F(-6)),
        SlotOp(14, "RX", "VISITOR", "visitor_ack", 0),
    ]
    assert check_half_duplex(bad, 1, 2), "r=2 紧邻收发必须被判违规"
    good = Schedule("probe")
    good.ops = [
        SlotOp(13, "TX", "VISITOR", "visitor", 0, F(-6)),
        SlotOp(16, "RX", "VISITOR", "visitor_ack", 0),
    ]
    assert not check_half_duplex(good, 1, 2), "间隔 3 格在 r=2 下应合规"
    print("半双工恢复时间核验测试通过")


if __name__ == "__main__":
    test_full_pipeline()
    test_gate_blocks_without_basis()
    test_half_duplex_checker_detects_violation()
    print("全部端到端测试通过")
