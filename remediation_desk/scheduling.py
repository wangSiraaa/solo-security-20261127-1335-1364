"""Operational decision for the single tonight replacement window."""
from __future__ import annotations

from typing import Any


def recommend_window(desks: list[dict[str, Any]]) -> dict[str, Any]:
    """Choose the replacement window and time-boxed control order.

    The input uses paper capacity numbers; no real personnel data is needed.
    The rule is deliberately conservative about volatile staffing reports.
    """
    if len(desks) != 2:
        raise ValueError("the canonical exercise has exactly two duty desks")

    by_no = {d["machine_no"]: d for d in desks}

    def hard_manual_gap(desk: dict[str, Any]) -> int:
        """Demand that cannot survive scanner disablement without a fallback."""
        gap = 0
        for shift, appointments in desk.get("appointments", {}).items():
            backup = desk.get("manual_backup", {}).get(shift, False)
            capacity = desk.get("manual_capacity", {}).get(shift, 0)
            if not backup:
                gap += max(0, int(appointments) - int(capacity))
        return gap

    ranked = []
    for desk in desks:
        tomorrow_gap = hard_manual_gap(desk)
        ranked.append((tomorrow_gap, bool(desk.get("safe_manual")), desk["machine_no"]))

    # Largest no-fallback hard commitment gets the replacement window.
    window_machine = max(desks, key=hard_manual_gap)["machine_no"]
    control_machine = next(no for no in by_no if no != window_machine)

    window_desk = by_no[window_machine]
    control_desk = by_no[control_machine]

    control_capacity = control_desk.get("manual_capacity", {}).get("tonight", 0)
    tonight_demand = control_desk.get("tonight_demand", 0)
    unfinished = max(0, int(tonight_demand) - int(control_capacity))

    return {
        "window_machine": window_machine,
        "control_first_machine": control_machine,
        "control_order": [
            {
                "machine_no": control_machine,
                "action": "Disable and lock BATCH_READ and SINGLE_READ callers before "
                "accepting another scanned item; open the paper/manual queue.",
                "why": control_desk.get("control_reason", "safe manual fallback and live exposure"),
                "tonight_completable": control_capacity,
                "carried_unfinished": unfinished,
                "do_not_promise": f"all {tonight_demand} tonight items",
                "control_through_shift": control_desk.get("control_through_shift"),
                "next_window": control_desk.get("next_window"),
            },
            {
                "machine_no": window_machine,
                "action": "Stop the old decoder process at window start, replace the "
                "part, start the corrected process, and file the new-process inventory receipt.",
                "why": window_desk.get("window_reason", "hard next-shift commitment without manual backup"),
                "promise_from_shift": window_desk.get("promise_from_shift"),
                "promise": window_desk.get("promise"),
            },
        ],
        "staffing_report_policy": (
            "Append a new staffing report; never rewrite yesterday's closure. A "
            "changed substitute report can change current capacity planning, but "
            "an unsigned/volatile report does not move the one tonight window. "
            "The old closure remains valid only if its stored scope explicitly "
            "bounds capacity and does not promise substitute-dependent normal service."
        ),
        "baseline": (
            "Whenever a desk is open, either show an effective final closure or "
            "keep every affected calling function disabled with old processes exited."
        ),
    }
