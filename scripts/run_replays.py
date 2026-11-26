#!/usr/bin/env python3
"""Replay healthy and first-good-then-fail futures from the same seed."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from demo_lib import Recorder  # noqa: E402

ORDER_M2 = "orders/M2-gen6.json"
ORDER_M3 = "orders/M3-gen6.json"


def load_state(path: Path) -> dict:
    return json.loads((path / "controller-state.json").read_text(encoding="utf-8"))


def fresh(rec: Recorder, state: Path) -> None:
    shutil.rmtree(state, ignore_errors=True)
    rec.run(["init", "--force"], state_dir=state, note="同一起点：甲机已有一次第六代合格试启")


def save(rec: Recorder, path: Path) -> None:
    rec.write(path)
    print(f"wrote {path}")


def healthy() -> None:
    state = ROOT / "state" / "replay-healthy"
    out = ROOT / "records" / "replay-healthy.md"
    rec = Recorder(
        "回放一：以后一直健康",
        "策略：每台机器原则上见到第二次连续合格自检后再烧桥；班点仍未手动承诺的合格第六代由 reconcile 自动接班。",
    )
    fresh(rec, state)

    rec.run(["advance-shift", "--by", "1"], state_dir=state, note="进入第 1 班")
    rec.run(
        ["operate", "M2", "--operation", "read-archive-card CARD-1042", "--by", "staff-lin"],
        state_dir=state,
        note="第五代在第 1 班仍按旧合同接受现场操作",
    )
    rec.run(["boot", "M1", "--result", "pass", "--note", "甲机第二次自检：持续健康"], state_dir=state)
    rec.run(
        ["commit", "M1", "--by", "maintainer-zhou", "--reason", "两张连续合格真机回执，烧桥时机成熟"],
        state_dir=state,
        note="甲机在第二次合格回执后烧掉回旧版的桥",
    )

    cut = rec.run(
        ["install", ORDER_M2, "--power-loss-after", "written"],
        state_dir=state,
        expect=42,
        note="乙机安装：写槽后掉电",
    )
    rec.check("乙机掉电命令返回 42", cut.returncode == 42)
    rec.run(["resume", "M2"], state_dir=state, note="乙机上电续跑")
    rec.run(["boot", "M2", "--result", "pass", "--note", "乙机第一次试启合格"], state_dir=state)

    rec.run(["advance-shift", "--by", "1"], state_dir=state, note="进入第 2 班")
    rec.run(["boot", "M2", "--result", "pass", "--note", "乙机第二次自检合格"], state_dir=state)
    rec.run(
        ["commit", "M2", "--by", "maintainer-zhou", "--reason", "第二次连续自检合格"],
        state_dir=state,
    )
    rec.run(["install", ORDER_M3], state_dir=state, note="丙机第 2 班安装")
    rec.run(["boot", "M3", "--result", "pass", "--note", "丙机第一次试启合格"], state_dir=state)

    rec.run(["advance-shift", "--by", "1"], state_dir=state, note="进入第 3 班统一停用点")
    rec.run(["boot", "M3", "--result", "pass", "--note", "丙机第二次自检仍健康"], state_dir=state)
    rec.run(["reconcile"], state_dir=state, note="班点清理：丙机不得一直停在试装，自动承诺第六代")
    rec.run(["shifts"], state_dir=state, note="可按机器号过滤的全班次记录入口；此处显示全部")
    rec.run(["report"], state_dir=state, note="每台机器的最终解释和完整记录")

    data = load_state(state)
    for mid, m in data["machines"].items():
        rec.check(f"{mid} 最终为第六代已承诺", m["state"] == "gen6_committed" and m["min_boot_generation"] == 6)
        rec.check(f"{mid} 最近实际开机合格", m["last_boot"]["generation"] == 6 and m["last_boot"]["result"] == "pass")
    rec.check("丙机由班点策略自动承诺", data["machines"]["M3"]["decision"]["deadline_promotion"] is True)
    save(rec, out)


def flaky_wait() -> None:
    state = ROOT / "state" / "replay-flaky-wait"
    out = ROOT / "records" / "replay-flaky-wait.md"
    rec = Recorder(
        "回放二：首次成功后又失败；维护员暂不提前烧桥",
        "第一次成功不立即提高最低代次。若后续自检在停用点前失败，控制器按各机持久回退条件恢复第五代；到第 3 班后旧版禁止启动。",
    )
    fresh(rec, state)

    rec.run(["advance-shift", "--by", "1"], state_dir=state, note="进入第 1 班")
    rec.run(
        ["operate", "M2", "--operation", "locate-box BOX-77", "--by", "staff-chen"],
        state_dir=state,
        note="乙机第五代在安装前仍按旧合同工作",
    )
    rec.run(
        ["boot", "M1", "--result", "fail", "--note", "甲机第一次成功后的第二次自检：外设探针失败"],
        state_dir=state,
        note="甲机失败时尚未烧桥，原地恢复第五代",
    )
    rec.run(["install", ORDER_M2], state_dir=state, note="乙机第 1 班安装第六代")
    rec.run(["boot", "M2", "--result", "pass", "--note", "乙机第一次试启成功"], state_dir=state)

    rec.run(["advance-shift", "--by", "1"], state_dir=state, note="进入第 2 班")
    rec.run(
        ["operate", "M1", "--operation", "scan-delivery CART-9", "--by", "staff-lin"],
        state_dir=state,
        note="甲机已回退第五代，服务保留并继续接受旧合同操作",
    )
    rec.run(
        ["boot", "M2", "--result", "fail", "--note", "乙机首次成功后的第二次自检失败"],
        state_dir=state,
        note="第 2 班仍满足回退条件，乙机恢复第五代",
    )
    rec.run(["install", ORDER_M3], state_dir=state, note="丙机第 2 班安装")
    rec.run(["boot", "M3", "--result", "pass", "--note", "丙机第一次试启成功"], state_dir=state)

    rec.run(["advance-shift", "--by", "1"], state_dir=state, note="进入第 3 班；第五代统一停用生效")
    rec.run(
        ["boot", "M3", "--result", "fail", "--note", "丙机首次成功后的第二次自检失败"],
        state_dir=state,
        note="停用点已到，第五代也不能作为恢复启动，丙机停用待修",
    )
    blocked1 = rec.run(["boot", "M1", "--result", "pass", "--note", "班点后尝试启动旧版"], state_dir=state, expect=3)
    blocked2 = rec.run(["boot", "M2", "--result", "pass", "--note", "班点后尝试启动旧版"], state_dir=state, expect=3)
    rec.check("甲机旧版启动被阻止", blocked1.returncode == 3)
    rec.check("乙机旧版启动被阻止", blocked2.returncode == 3)
    rec.run(["reconcile"], state_dir=state, note="班点核对：无最近合格第六代，不允许伪装成仍在试装")
    rec.run(["report"], state_dir=state, note="每台机器的实际运行/停用记录")

    data = load_state(state)
    m1, m2, m3 = (data["machines"][x] for x in ("M1", "M2", "M3"))
    rec.check("甲、乙均未烧桥", m1["bridge_burned"] is False and m2["bridge_burned"] is False)
    rec.check("甲机在班点后登记停用", m1["state"] == "retired_stopped")
    rec.check("乙机在班点后登记停用", m2["state"] == "retired_stopped")
    rec.check("丙机因班点无法回退而停用待修", m3["state"] == "disabled_repair_required")
    rec.check("三机均保留已见到的失败记录", len(m1["failures"]) == 1 and len(m2["failures"]) == 1 and len(m3["failures"]) == 1)
    save(rec, out)


def early_burn_counterfactual() -> None:
    state = ROOT / "state" / "whatif-flaky-burn-early"
    out = ROOT / "records" / "whatif-flaky-burn-early.md"
    rec = Recorder(
        "反事实：失败未来中，每台都凭第一次回执提前烧桥",
        "控制器允许维护员显式承担提前风险；但提高最低代次后，下一次自检失败将不能原地回第五代。",
    )
    fresh(rec, state)

    rec.run(
        ["commit", "M1", "--accept-early-risk", "--by", "maintainer-zhao", "--reason", "资料室要求尽早终止第五代能力，凭甲机第一次回执提前烧桥"],
        state_dir=state,
        note="班 0：甲机最早可执行的不可逆承诺",
    )
    rec.run(["advance-shift", "--by", "1"], state_dir=state)
    rec.run(["boot", "M1", "--result", "fail", "--note", "提前烧桥后的第二次自检失败"], state_dir=state, note="最低代次已是 6，甲机立即失去原地恢复机会")

    rec.run(["install", ORDER_M2], state_dir=state)
    rec.run(["boot", "M2", "--result", "pass", "--note", "乙机第一次试启成功"], state_dir=state)
    rec.run(
        ["commit", "M2", "--accept-early-risk", "--by", "maintainer-zhao", "--reason", "缩短旧版可启动窗口"],
        state_dir=state,
        note="乙机也在第一次成功后立即烧桥",
    )

    rec.run(["advance-shift", "--by", "1"], state_dir=state)
    rec.run(["boot", "M2", "--result", "fail", "--note", "乙机第二次自检失败"], state_dir=state)
    rec.run(["install", ORDER_M3], state_dir=state)
    rec.run(["boot", "M3", "--result", "pass", "--note", "丙机第一次试启成功"], state_dir=state)
    rec.run(
        ["commit", "M3", "--accept-early-risk", "--by", "maintainer-zhao", "--reason", "统一提前承诺"],
        state_dir=state,
    )

    rec.run(["advance-shift", "--by", "1"], state_dir=state)
    rec.run(["boot", "M3", "--result", "fail", "--note", "丙机第二次自检失败"], state_dir=state)
    rec.run(["reconcile"], state_dir=state)
    rec.run(["report"], state_dir=state, note="提前烧桥反事实的三机停用记录")

    data = load_state(state)
    for mid, m in data["machines"].items():
        rec.check(f"{mid} 已烧桥且最低代次为 6", m["bridge_burned"] is True and m["min_boot_generation"] == 6)
        rec.check(f"{mid} 第二次失败后停用待修", m["state"] == "disabled_repair_required")
    save(rec, out)


def main() -> int:
    healthy()
    flaky_wait()
    early_burn_counterfactual()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
