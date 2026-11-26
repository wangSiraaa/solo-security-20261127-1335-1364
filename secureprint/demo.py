#!/usr/bin/env python3
"""Interactive demo of the secret print-room release service.

Usage::

    python3 -m secureprint.demo
"""

import json

from . import ReleaseService, LocKind, Side
from .scenarios import build_one_free_cell_world, run_two_arrival_changes


def _pp(title, obj):
    print("\n" + "=" * 72)
    print(title)
    print("-" * 72)
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def demo_reconstruction_and_tray():
    print("\n### 场景一：旧工单的背面回报 / 敞口废纸盘")
    svc = ReleaseService()
    svc.add_cell("C1", owner="甲")
    svc.sign_commitment("甲", "J1", in_person=True)
    svc.submit_job("J1", "甲", pages=1, in_person=True)
    svc.register_sheet("S1", "J1", 1)
    svc.report_face("S1", Side.FRONT, candidates=["甲"])
    svc.report_face("S1", Side.BACK, candidates=["甲"])
    svc.report_receipt("S1", LocKind.CELL, cell_id="C1")
    slip = svc.sign_claim("甲")
    _pp("S1 初始还原", svc.reconstruct("S1"))

    print("\n-- 换人后旧工单传来背面回报：可能属于甲或乙")
    svc.report_face("S1", Side.BACK, job_id="OLD-JOB",
                    candidates=["甲", "乙"], reported_by="上一班")
    print("原领条是否仍有效:", svc.validate_ticket(slip.ticket_id))
    _pp("S1 背面变含糊后的还原", svc.reconstruct("S1"))

    print("\n-- 恢复两面归属为甲；只把落纸回执改成敞口废纸盘")
    svc.report_face("S1", Side.BACK, candidates=["甲"])
    svc.report_receipt("S1", LocKind.TRAY, detail="敞口废纸盘")
    _pp("S1 回执改成废纸盘后的还原", svc.reconstruct("S1"))
    print("台账（应为空——未登记安全送达）:", svc.ledger_entries())


def demo_two_pickups():
    print("\n### 场景二：仅剩一个空安全格，甲乙两人的临时到场变化")
    svc = ReleaseService()
    info = build_one_free_cell_world(svc, jia_job="JIA", yi_job="YI")
    print("起始空格:", info["free"])
    res = run_two_arrival_changes(svc)
    for label, data in res.items():
        d = data["decision"]
        print(f"\n-- {label}  到场={data['arrival']}  建议顺序={d['recommended_order']}")
        for name, cand in d["candidates"].items():
            print(f"   方案 {name}: 完成={cand['completes']} "
                  f"空手={cand['empty_handed']}")
            for r in cand["risks"]:
                print(f"      风险: {r}")


def main():
    demo_reconstruction_and_tray()
    demo_two_pickups()
    print("\n完成。")


if __name__ == "__main__":
    main()
