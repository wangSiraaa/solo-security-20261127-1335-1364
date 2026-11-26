"""Rule-based switching-boundary planner."""

from __future__ import annotations

from typing import Any

from .domain import (
    NEW_STATION,
    OLD_STATION,
    SWITCH_WINDOWS,
    cabinet_label,
    station_label,
)


def _earliest_new_authority_tick(cabinet: dict[str, Any]) -> int:
    if cabinet.get("deactivation_receipt_id"):
        return int(cabinet["updated_tick"])
    return int(cabinet["lease_through_tick"]) + 1


def build_plan(engine, at: int | None = None) -> dict[str, Any]:
    snap = engine.authority_snapshot(at)
    tick = snap["tick"]
    matrix = engine.order_matrix(tick)
    orders = matrix["orders"]
    cabinets = {c["code"]: c for c in snap["cabinets"]}

    urgent_new_site: list[dict[str, Any]] = []
    joint_orders: list[dict[str, Any]] = []
    for item in orders:
        if item["status"] == "completed":
            continue
        if item["delivery_site"] == "new-site":
            urgent_new_site.append(item)
        if len(item["required_cabinets"]) == 2:
            joint_orders.append(item)

    boundaries = []
    for code, cab in cabinets.items():
        earliest_new = _earliest_new_authority_tick(cab)
        if cab.get("deactivation_receipt_id"):
            action = "hold-new-generation"
            when = int(cab["updated_tick"])
            reason = "已有持久停用回执，边界不可回退"
        elif tick in SWITCH_WINDOWS and cab["new_route_online"]:
            action = "durable-switch-now"
            when = tick
            reason = "当前在换代联络窗口且新址线路可达，可立即取持久停用回执"
        else:
            action = "wait-for-receipt-or-lease-fence"
            when = earliest_new
            reason = "无回执时最早只能按可信租约到期形成新代边界；若提前拿到回执则以回执为准"
        boundaries.append(
            {
                "cabinet": code,
                "cabinet_label": cabinet_label(code),
                "action": action,
                "earliest_new_authority_tick": earliest_new,
                "recommended_action_tick": when,
                "reason": reason,
            }
        )

    # Plan A: release an urgent new-site cabinet immediately.
    early_actions: list[dict[str, Any]] = []
    early_feasible = True
    early_notes: list[str] = []
    for item in urgent_new_site:
        code = item["required_cabinets"][0]
        auth = matrix["authority"][code]
        if auth["station"] != NEW_STATION:
            cab = cabinets[code]
            if tick in SWITCH_WINDOWS and cab["new_route_online"]:
                early_actions.append(
                    {"op": "successful_switch", "cabinet": code, "at": tick}
                )
                early_notes.append(f"{cabinet_label(code)}在班初窗口立即换代并取持久回执")
            else:
                early_feasible = False
                early_notes.append(f"{cabinet_label(code)}当前没有换代窗口或不可达，无法立即开新站柜")
        early_notes.append(
            f"{item['order_id']} 可在材料齐且线路可达时由新站签发，期限拍点 {item['deadline_tick']}"
        )

    for item in joint_orders:
        required = item["required_cabinets"]
        convergence = max(
            _earliest_new_authority_tick(cabinets[c]) for c in required
        )
        deadline = int(item["deadline_tick"])
        if convergence <= deadline:
            early_notes.append(
                f"{item['order_id']} 最早第 {convergence} 拍可由新站共同签发，仍赶得及班末"
            )
        else:
            early_notes.append(
                f"{item['order_id']} 在尽早切甲后会处于跨站分裂；无乙柜回执时最早第 "
                f"{convergence} 拍归一，晚于期限 {deadline}，须留待办"
            )

    early_plan = {
        "id": "early-per-cabinet",
        "title": "按柜尽早交回/接回",
        "feasible_for_urgent_new_site": early_feasible and bool(urgent_new_site),
        "actions": early_actions,
        "notes": early_notes,
    }

    # Plan B: keep old dual-cabinet service until old work is finished.
    old_notes: list[str] = []
    old_dual_feasible = True
    for item in joint_orders:
        old_view = item["stations"][OLD_STATION]
        finish_tick = max(tick, int(item["material_ready_tick"]))
        if not old_view["common_authority"]:
            old_dual_feasible = False
            old_notes.append(f"{item['order_id']} 的旧代边界已被持久回执切断，不能再留给旧站")
        elif finish_tick <= int(item["deadline_tick"]) and old_view["routes_reachable"]:
            old_notes.append(
                f"保持两柜旧代至第 {finish_tick} 拍材料收齐后，由旧站办完 {item['order_id']}"
            )
        else:
            old_dual_feasible = False
            old_notes.append(f"{item['order_id']} 已无法由旧站在期限前经馆内线办结")

    deferred_wait = None
    for item in urgent_new_site:
        if item.get("flexible_next_shift"):
            old_notes.append(
                f"{item['order_id']} 已约定可等下一班；旧站联签办结后再整体接回"
            )
            deferred_wait = "one-shift"
        else:
            old_dual_feasible = False if not joint_orders else old_dual_feasible
            old_notes.append(
                f"{item['order_id']} 的客人只等到第 {item.get('patience_tick')} 拍；"
                "不立即切柜会失去该单或违反等待约定"
            )

    old_plan = {
        "id": "old-dual-then-takeback",
        "title": "先让旧站办完双柜，再整体接回",
        "feasible_for_joint": old_dual_feasible and bool(joint_orders),
        "new_site_wait": deferred_wait,
        "actions": [
            {"op": "hold_switch", "until": "joint_completed_or_tick_3"},
            {"op": "next_shift_global_switch_or_lease_fence", "at": 4},
        ],
        "notes": old_notes,
    }

    # Deterministic local policy: hard patience wins only if early switch is
    # actually available; an explicitly flexible request permits preserving a
    # feasible deadline-bound joint order.
    selected = "none"
    reason = "没有待处理单，仅持续推导各柜签发站"
    hard_urgent = [i for i in urgent_new_site if not i.get("flexible_next_shift")]
    flexible_urgent = [i for i in urgent_new_site if i.get("flexible_next_shift")]
    if hard_urgent and early_plan["feasible_for_urgent_new_site"]:
        selected = "early-per-cabinet"
        reason = "存在只等第一拍末且未同意延班的新址单；班初窗口必须先取甲柜持久停用回执"
    elif flexible_urgent and old_plan["feasible_for_joint"]:
        selected = "old-dual-then-takeback"
        reason = "新址单已同意多等一班，而旧站仍可在班末前兑现两柜联签；先保留双柜旧代边界"
    elif early_plan["actions"]:
        selected = "early-per-cabinet"
        reason = "没有可完整保留的旧站双柜路径，按可取得回执的柜尽早切换"

    return {
        "ok": True,
        "tick": tick,
        "selected_boundary": selected,
        "selection_reason": reason,
        "station_labels": {
            OLD_STATION: station_label(OLD_STATION),
            NEW_STATION: station_label(NEW_STATION),
        },
        "boundaries": boundaries,
        "plans": {
            "early_per_cabinet": early_plan,
            "old_dual_then_takeback": old_plan,
        },
    }


def apply_selected_boundary(engine, at: int | None = None) -> dict[str, Any]:
    """Execute the safe immediate cut-over actions selected by the planner."""

    plan = build_plan(engine, at)
    applied: list[dict[str, Any]] = []
    if plan["selected_boundary"] == "early-per-cabinet":
        for action in plan["plans"]["early_per_cabinet"]["actions"]:
            if action.get("op") != "successful_switch":
                continue
            code = action["cabinet"]
            cabinet = next(c for c in plan["boundaries"] if c["cabinet"] == code)
            if cabinet["action"] == "durable-switch-now":
                engine.successful_switch(code)
                applied.append({"op": "successful_switch", "cabinet": code})

    return {
        "ok": True,
        "applied": applied,
        "plan": plan,
        "authority_snapshot": engine.authority_snapshot(at),
    }
