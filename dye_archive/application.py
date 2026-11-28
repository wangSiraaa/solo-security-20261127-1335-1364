"""留样领用申请与受理文书（虚构）。

任选一路的取舍在申请书中写明：本库选择路线 B（逐管逐段交接 + 独立参考重建），
并保留路线 A（随管封存小组）的替代说明，便于收件人理解为什么散管仍可救回、
代价是跨架关联不确定度。
"""
from __future__ import annotations

from .archive import (BUDGET_EQH, CROSS_SHELF_U, EVENT_U, GAP, OOT,
                      RATE_LIMIT, WIDE_WINDOW, leadability, worst_exposure)
from .lab import packet_for

APPLICANT = "外地复验实验室 EXT-LAB-7（虚构）"
ROUTE_B = "路线B：逐管、逐段交接，缺口依同层参考与排架图重建最不利暴露"
ROUTE_A = "路线A：存放时把有余地的样管封成不可拆换随管小组，整组履历完整才领出"


def _tube_block(world: dict, tube_id: str) -> dict:
    lead = leadability(world, tube_id)
    expo = worst_exposure(world, tube_id)
    rows = expo["rows"]
    rebuilt = [r for r in rows if r["kind"] in ("gap-clamp", "oot-clamp")]
    return {
        "tube": tube_id,
        "colorant": world["tubes"][tube_id]["colorant"],
        "lot": world["tubes"][tube_id]["lot"],
        "leadable": lead["leadable"],
        "eq_hours_5c": round(expo["eq_hours"], 2),
        "budget_eq_hours": BUDGET_EQH,
        "rebuilt_hours": expo["rebuilt_hours"],
        "rebuild_samples": [
            {"hour": r["hour"], "lo_c": round(r["lo"], 3),
             "hi_c": round(r["hi"], 3), "kind": r["kind"], "source": r["source"]}
            for r in rebuilt[:4]
        ],
        "reasons": lead["reasons"],
    }


def build_application(world: dict, requested: list[str]) -> dict:
    blocks = [_tube_block(world, t) for t in requested]
    all_leadable = all(b["leadable"] for b in blocks)
    return {
        "document_id": "APP-2026-0042（虚构）",
        "applicant": APPLICANT,
        "route_selected": ROUTE_B,
        "route_tradeoff": {
            "B": "保持逐管逐段交接；缺口由同层参考点 + 允许温变速率 "
                 f"{RATE_LIMIT}°C/h 夹逼，借用邻架叠加跨架不确定度 "
                 f"{CROSS_SHELF_U}°C，开门/过秤当小时另加 {EVENT_U}°C；"
                 "能救回散管，但承担跨架关联不确定度。",
            "A": "存放时即把有余地的样管封成不可拆换随管小组，任一段履历缺失"
                 "则整组不领；牺牲零散取用灵活性，换取零重建、零跨架假设。",
        },
        "archive_windows": {"P3_gap": GAP, "P4_oot": OOT,
                            "wide_bath_window": WIDE_WINDOW},
        "requested_tubes": blocks,
        "attached_packets": {t: packet_for(world, t) for t in requested},
        "intake_decision": (
            "受理：两支样管均取得完整可核源履历，随附逐管封存链而非柜子合格章"
            if all_leadable else
            "退回：存在不可领样管，见各管 reasons"),
    }
