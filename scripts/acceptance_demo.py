#!/usr/bin/env python3
"""Run the three requested acceptance cases and save a factual transcript."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from demo_lib import Recorder  # noqa: E402

STATE = ROOT / "state" / "acceptance"
POWER_STATE = ROOT / "state" / "power-resume"
COMPONENT = ROOT / "packages" / "gen6" / "files" / "components" / "reader.pack"
ORDER_M2 = "orders/M2-gen6.json"
ORDER_M3 = "orders/M3-gen6.json"
OUT = ROOT / "records" / "acceptance-demo.md"


def load_state(state_dir: Path) -> dict:
    return json.loads((state_dir / "controller-state.json").read_text(encoding="utf-8"))


def main() -> int:
    original_bytes = COMPONENT.read_bytes()
    rec = Recorder(
        "验收演示：正确切换、未获准组件中止、试启失败不得接班",
        "本记录由 `scripts/acceptance_demo.py` 实际执行生成。所有状态均来自 CLI 与持久 JSON。",
    )
    try:
        shutil.rmtree(STATE, ignore_errors=True)
        shutil.rmtree(POWER_STATE, ignore_errors=True)

        rec.run(["init", "--force"], state_dir=STATE, note="建立三台模拟机的同一起点")
        rec.run(["verify", "packages/gen6"], state_dir=STATE, note="发布方第六代包摘要核对")

        # 1. Happy path: valid bytes/components and a valid signed pass receipt.
        rec.run(["install", ORDER_M2], state_dir=STATE, note="第一单：合格第六代写槽并切换")
        rec.run(
            ["boot", "M2", "--result", "pass", "--note", "验收用第一次试启，回执由模拟设备签名"],
            state_dir=STATE,
            note="正确回执：试启通过并登记接班",
        )
        state = load_state(STATE)
        m2 = state["machines"]["M2"]
        rec.check("乙机 b 槽为第 6 代且有合格回执", m2["slots"]["b"]["generation"] == 6 and m2["last_boot"]["result"] == "pass")
        rec.check("乙机已登记第六代接班但未烧桥", m2["active_slot"] == "b" and m2["bridge_burned"] is False)

        # 2. Tamper one companion component with an unapproved digest.
        COMPONENT.write_bytes(original_bytes + b"\nUNAPPROVED replacement/tamper bytes\n")
        bad_verify = rec.run(["verify", "packages/gen6"], state_dir=STATE, expect=2, note="篡改后核对必须失败")
        rec.check("核对器指出 reader 摘要未获准", "components/reader.pack" in bad_verify.stderr and "未获准" in bad_verify.stderr)

        bad_install = rec.run(["install", ORDER_M3], state_dir=STATE, expect=2, note="同一类切换必须在写槽/切换前停止")
        rec.check("安装命令以退出码 2 停止", bad_install.returncode == 2)
        state = load_state(STATE)
        m3 = state["machines"]["M3"]
        rec.check("丙机未写入 b 槽、未切换试启槽", "b" not in m3["slots"] and m3["trial_slot"] is None)
        rec.check("丙机仍在 a 槽第五代", m3["active_slot"] == "a" and m3["slots"]["a"]["generation"] == 5)
        rec.check("被拒安装单已持久留痕", state["orders"]["TICKET-M3-G6-001"]["phase"] == "rejected")

        rerun_original = rec.run(
            ["install", ORDER_M2],
            state_dir=STATE,
            expect=2,
            note="已经成功过的原乙机单再次遇到被换组件时，仍在核对阶段停止",
        )
        rec.check("原安装单重跑返回 2", rerun_original.returncode == 2)
        state = load_state(STATE)
        m2_order = state["orders"]["TICKET-M2-G6-001"]
        rec.check("原乙机单保留 completed 审计状态", m2_order["status"] == "completed")
        rec.check("原乙机单追加了拒绝尝试", len(m2_order.get("rejected_attempts", [])) == 1)

        # 3. Restore original bytes; qualified material but failed trial.
        COMPONENT.write_bytes(original_bytes)
        rec.run(["verify", "packages/gen6"], state_dir=STATE, note="恢复原件后重新核对通过")
        rec.run(["install", ORDER_M3], state_dir=STATE, note="恢复后丙机安装成功并切换")
        rec.run(
            ["boot", "M3", "--result", "fail", "--note", "只让丙机试启外设探针失败"],
            state_dir=STATE,
            note="目标机试启回报失败",
        )
        state = load_state(STATE)
        m3 = state["machines"]["M3"]
        rec.check("材料合格", m3["slots"]["b"]["approved"] is True)
        rec.check("失败后 b 槽标记 failed", m3["slots"]["b"]["status"] == "failed")
        rec.check("没有把第六代登记为现役接班", m3["active_slot"] == "a" and m3["trial_slot"] is None)
        rec.check("满足回退条件并恢复到第五代", m3["state"] == "rolled_back_gen5" and m3["last_boot"]["generation"] == 5)
        rec.check("存在失败回执和恢复自检回执", len([r for r in m3["receipts"] if r["result"] == "fail"]) == 1)
        rec.run(["show", "M3"], state_dir=STATE, note="丙机按机器号的完整解释")

        # 4. Durable resume after simulated power loss.
        rec.run(["init", "--force"], state_dir=POWER_STATE, note="另取干净状态演示掉电续跑")
        cut = rec.run(
            ["install", ORDER_M2, "--power-loss-after", "written"],
            state_dir=POWER_STATE,
            expect=42,
            note="写完槽后立即模拟掉电",
        )
        rec.check("掉电命令返回 42", cut.returncode == 42)
        state = load_state(POWER_STATE)
        rec.check("持久安装单停在 written", state["orders"]["TICKET-M2-G6-001"]["phase"] == "written")
        rec.run(["resume", "M2"], state_dir=POWER_STATE, note="重新上电后从持久记录接续到等待开机")
        rec.run(["boot", "M2", "--result", "pass", "--note", "掉电恢复后的试启"], state_dir=POWER_STATE)
        state = load_state(POWER_STATE)
        rec.check("掉电恢复后仍能正确接班", state["machines"]["M2"]["last_boot"]["generation"] == 6)

        rec.write(OUT)
        print(f"acceptance transcript written: {OUT}")
        return 0
    finally:
        COMPONENT.write_bytes(original_bytes)


if __name__ == "__main__":
    raise SystemExit(main())
