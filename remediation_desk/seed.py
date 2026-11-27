"""Deterministic three-terminal paper exercise.

Run ``python3 -m remediation_desk.seed`` to recreate ``data/desk.json``.
All versions, people, and maintenance events are fictional.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .desk import Desk
from .rules import issue_closure, refresh_ticket_status
from .scheduling import recommend_window

CODE = "FIC-BARCODE-DCP710"
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / "data" / "desk.json"

SHIFT_ORDER = [
    "SHIFT-YESTERDAY",
    "SHIFT-0-TONIGHT",
    "SHIFT-1-FRI-AM",
    "SHIFT-2-FRI-PM",
    "WINDOW-NEXT-AFTER-TWO-SHIFTS",
    "SHIFT-3-SAT-AM",
]


def comp(
    uid: str,
    function: str,
    version: str,
    *,
    enabled: bool,
    pids: list[tuple[int, str, str]] | None = None,
) -> dict[str, Any]:
    """pids are (pid, running_version, state)."""
    return {
        "uid": uid,
        "part_no": "DCP-710",
        "name": "fictional barcode decoder module",
        "installed_version": version,
        "function": function,
        "calls_part": True,
        "function_enabled": enabled,
        "processes": [
            {"pid": pid, "running_version": ver, "state": state}
            for pid, ver, state in (pids or [])
        ],
    }


def build_seed(path: str | Path | None = DEFAULT_PATH) -> Desk:
    desk = Desk(path)
    desk.data.clear()
    import copy

    from .models import DEFAULT_DATA

    desk.data.update(copy.deepcopy(DEFAULT_DATA))
    desk.data["shift_order"] = SHIFT_ORDER
    desk.data["current_shift"] = "SHIFT-0-TONIGHT"

    # R1 is the bulletin under which yesterday's scoped JIA closure was issued.
    # It is withdrawn and replaced by R2 after that historical event below.
    r1 = desk.add_advisory(
        code=CODE,
        revision=1,
        advisory_id="ADV-DCP710-R1",
        title="Fictional barcode decoder advisory (withdrawn revision)",
        part_numbers=["DCP-710"],
        affected_range=">=1.0.0,<1.9.0",
        fixed_version="1.9.0",
        enablement="FUNCTION_ENABLED",
        issued_at="SHIFT-YESTERDAY",
        note="Initial paper bulletin; upper bound later found to be wrong.",
    )

    # ------------------------------------------------------------------
    # Machines
    # ------------------------------------------------------------------
    desk.register_machine(
        "TERM-JIA",
        label="甲桌条码终端",
        desk="DESK-JIA",
        service={
            "tonight_demand": 40,
            "manual_capacity": {"tonight": 20, "SHIFT-1-FRI-AM": 0},
            "safe_manual": True,
            "closed_shifts": ["SHIFT-1-FRI-AM", "SHIFT-2-FRI-PM"],
            "appointments": {},
            "manual_backup": {},
            "control_through_shift": "SHIFT-2-FRI-PM",
            "next_window": "WINDOW-NEXT-AFTER-TWO-SHIFTS",
            "control_reason": "正在接待且有安全人工通道；明天该桌休息",
            "window_reason": "",
        },
    )
    desk.register_machine(
        "TERM-YI",
        label="乙桌条码终端",
        desk="DESK-YI",
        service={
            "tonight_demand": 0,
            "can_stop_tonight": True,
            "appointments": {"SHIFT-1-FRI-AM": 10},
            "manual_capacity": {"SHIFT-1-FRI-AM": 0},
            "manual_backup": {"SHIFT-1-FRI-AM": False},
            "safe_manual": False,
            "promise_from_shift": "SHIFT-1-FRI-AM",
            "promise": "Ten booked morning items recorded normally after process receipt.",
            "window_reason": "明早十件已约且当前无替班/人工代录",
        },
    )
    desk.register_machine(
        "TERM-BING",
        label="不停业单读终端（丙）",
        desk="MOBILE-COUNTER",
        service={
            "cannot_stop_service": True,
            "spare_no": "SPARE-BING-1",
            "order_kind": "NO_DOWNTIME_REPLACE",
        },
    )

    # Tickets are opened under the lineage; current rules use active R2.
    for no, kind, promise in [
        (
            "TERM-JIA",
            "CONTROL_OR_REPLACE",
            {"kind": "SAFE_MANUAL_CONTROL", "status": "AVAILABLE", "through_shift": "SHIFT-2-FRI-PM"},
        ),
        (
            "TERM-YI",
            "DOWNTIME_REPLACE",
            {"kind": "TONIGHT_WINDOW_REPLACE", "status": "AVAILABLE", "through_shift": "SHIFT-1-FRI-AM"},
        ),
        (
            "TERM-BING",
            "NO_DOWNTIME_REPLACE",
            {"kind": "NO_DOWNTIME_REPLACE", "status": "AVAILABLE", "through_shift": None},
        ),
    ]:
        desk.create_ticket(no, CODE, ticket_id=f"TKT-{no.split('-')[1]}", order_kind=kind, promise=promise)

    # ------------------------------------------------------------------
    # JIA: yesterday's exposure, time-boxed paper/manual control, old PIDs exit.
    # ------------------------------------------------------------------
    desk.submit_inventory(
        "TERM-JIA",
        [
            comp(
                "JIA-batch",
                "BATCH_READ",
                "1.8.4",
                enabled=True,
                pids=[(4101, "1.8.4", "RUNNING")],
            ),
            comp(
                "JIA-single",
                "SINGLE_READ",
                "1.8.4",
                enabled=True,
                pids=[(4102, "1.8.4", "RUNNING")],
            ),
        ],
        report_id="INV-JIA-YDAY-OPEN",
        captured_at="SHIFT-YESTERDAY",
        reporter="closing-count",
        whole_model="DeskBar X9",
        whole_version="7.2.0-fictional",
    )
    desk.add_disablement_receipt(
        "TERM-JIA",
        receipt_id="DIS-JIA-YDAY",
        issued_at="SHIFT-YESTERDAY",
        covered_uids=["JIA-batch", "JIA-single"],
        covered_functions=["BATCH_READ", "SINGLE_READ"],
        durable=False,
        temporary=True,
        old_processes_stopped=True,
        control="SIGNED_PAPER_SWITCHOFF_AND_MANUAL_QUEUE",
        valid_through_shift="SHIFT-2-FRI-PM",
        paper_ref="A-desk logbook p.18",
        note="Both callers switched off; staff instructed not to relaunch decoder.",
    )
    desk.submit_inventory(
        "TERM-JIA",
        [
            comp(
                "JIA-batch",
                "BATCH_READ",
                "1.8.4",
                enabled=False,
                pids=[(4101, "1.8.4", "EXITED")],
            ),
            comp(
                "JIA-single",
                "SINGLE_READ",
                "1.8.4",
                enabled=False,
                pids=[(4102, "1.8.4", "EXITED")],
            ),
        ],
        report_id="INV-JIA-YDAY-MANUAL",
        captured_at="SHIFT-YESTERDAY",
        reporter="supervisor-paper-check",
        whole_model="DeskBar X9",
        whole_version="7.2.0-fictional",
    )
    issue_closure(
        desk.data,
        "TERM-JIA",
        closure_id="CL-JIA-TMP-YDAY",
        kind="TEMPORARY_CONTROL",
        at="SHIFT-YESTERDAY",
        statement="Yesterday's scoped temporary closure; not a durable replacement closure.",
        scope={
            "summary": (
                "Covers only switched-off BATCH_READ/SINGLE_READ callers and exited "
                "PIDs 4101/4102. Manual completion is bounded to 20 pieces for "
                "tonight's staffing; it does not promise all 40 or normal scanner service."
            ),
            "max_tonight_completions": 20,
            "tonight_intake": 40,
            "carried_unfinished": 20,
            "valid_through_shift": "SHIFT-2-FRI-PM",
            "staffing_dependency": "NONE_REQUIRED",
        },
    )

    # Supplier withdraws R1 and issues corrected R2 before tonight's window.
    r2 = desk.add_advisory(
        code=CODE,
        revision=2,
        advisory_id="ADV-DCP710-R2",
        title="Fictional barcode decoder advisory (corrected revision)",
        part_numbers=["DCP-710"],
        affected_range=">=1.0.0,<2.0.0",
        fixed_version="2.0.0",
        enablement="FUNCTION_ENABLED",
        issued_at="SHIFT-0-TONIGHT",
        note=(
            "Correction includes 1.9.x subcomponents and expressly requires "
            "the old process to exit before replacement evidence is accepted."
        ),
    )
    desk.replace_advisory(
        r1["id"],
        r2,
        reason="Supplier withdrew R1 and corrected the affected version interval.",
        at="SHIFT-0-TONIGHT",
        withdrawn=True,
    )

    # A fresh tonight count confirms the control is still in place.
    desk.submit_inventory(
        "TERM-JIA",
        [
            comp(
                "JIA-batch",
                "BATCH_READ",
                "1.8.4",
                enabled=False,
                pids=[(4101, "1.8.4", "EXITED")],
            ),
            comp(
                "JIA-single",
                "SINGLE_READ",
                "1.8.4",
                enabled=False,
                pids=[(4102, "1.8.4", "EXITED")],
            ),
        ],
        report_id="INV-JIA-TONIGHT-CHECK",
        captured_at="SHIFT-0-TONIGHT",
        reporter="opening-count",
        whole_model="DeskBar X9",
        whole_version="7.2.0-fictional",
    )

    # ------------------------------------------------------------------
    # YI: exposed tonight, receives the one replacement window.
    # ------------------------------------------------------------------
    desk.submit_inventory(
        "TERM-YI",
        [
            comp(
                "YI-batch",
                "BATCH_READ",
                "1.8.4",
                enabled=True,
                pids=[(5101, "1.8.4", "RUNNING")],
            ),
            comp(
                "YI-single",
                "SINGLE_READ",
                "1.8.4",
                enabled=True,
                pids=[(5102, "1.8.4", "RUNNING")],
            ),
        ],
        report_id="INV-YI-OPEN",
        captured_at="SHIFT-0-TONIGHT",
        reporter="pre-window-count",
        whole_model="DeskBar X9",
        whole_version="7.2.0-fictional",
    )
    desk.add_staffing_report(
        "DESK-YI",
        "SHIFT-1-FRI-AM",
        False,
        report_id="STAFF-YI-AM-ORIGINAL",
        source="duty-roster",
        reliable=True,
        note="Ten appointments; no substitute/manual entry clerk.",
    )
    # A later volatile report claims a substitute may arrive. It is not a
    # confirmed roster change, so it is appended rather than overwriting history.
    desk.add_staffing_report(
        "DESK-YI",
        "SHIFT-1-FRI-AM",
        True,
        report_id="STAFF-YI-AM-RUMOR",
        source="unconfirmed-message",
        reliable=False,
        note="Report changed twice; do not move the safety/window decision on this alone.",
    )
    plan = recommend_window(
        [desk.machine("TERM-JIA")["service"] | {"machine_no": "TERM-JIA"},
         desk.machine("TERM-YI")["service"] | {"machine_no": "TERM-YI"}]
    )
    plan["id"] = "PLAN-TONIGHT-1"
    desk.data["plans"].append(plan)

    desk.add_repair_receipt(
        "TERM-YI",
        receipt_id="RR-YI-WINDOW",
        completed_at="SHIFT-0-TONIGHT",
        action="REPLACE_DCP710",
        part_no="DCP-710",
        from_version="1.8.4",
        to_version="2.0.1-fictional",
        covered_uids=["YI-batch", "YI-single"],
        paper_ref="window envelope 7",
        note="One part/revision fitted to both decoder slots; old board tagged.",
    )
    desk.submit_inventory(
        "TERM-YI",
        [
            {
                "uid": "YI-batch",
                "part_no": "DCP-710",
                "name": "fictional barcode decoder module",
                "installed_version": "2.0.1-fictional",
                "function": "BATCH_READ",
                "calls_part": True,
                "function_enabled": True,
                "processes": [
                    {"pid": 5101, "running_version": "1.8.4", "state": "EXITED"},
                    {"pid": 5201, "running_version": "2.0.1-fictional", "state": "RUNNING"},
                ],
            },
            {
                "uid": "YI-single",
                "part_no": "DCP-710",
                "name": "fictional barcode decoder module",
                "installed_version": "2.0.1-fictional",
                "function": "SINGLE_READ",
                "calls_part": True,
                "function_enabled": True,
                "processes": [
                    {"pid": 5102, "running_version": "1.8.4", "state": "EXITED"},
                    {"pid": 5202, "running_version": "2.0.1-fictional", "state": "RUNNING"},
                ],
            },
        ],
        report_id="INV-YI-NEW-PROCESS",
        captured_at="SHIFT-0-TONIGHT",
        reporter="post-replacement-process-count",
        whole_model="DeskBar X9",
        whole_version="7.2.0-fictional",
        raw_note="New PIDs and process executable versions checked against paper receipt.",
    )
    issue_closure(
        desk.data,
        "TERM-YI",
        closure_id="CL-YI-FINAL",
        kind="FINAL",
        route="REPLACED_NEW_PROCESS",
        at="SHIFT-0-TONIGHT",
        statement="Replaced and new process inventory receipt verified.",
        scope={
            "summary": "Covers BATCH_READ and SINGLE_READ instances and PIDs 5101/5102.",
            "morning_appointments_covered": 10,
            "staffing_dependency": "NONE_REQUIRED",
        },
    )

    # ------------------------------------------------------------------
    # BING: hot-swap promise, inventory-only direct closure, then regression.
    # ------------------------------------------------------------------
    desk.add_resource_event(
        "SPARE-BING-1",
        event="SPARE_RESERVED",
        machine_no="TERM-BING",
        available=True,
        event_id="RES-SPARE-RESERVED",
        note="Spare terminal makes a no-downtime replacement order supportable.",
    )
    desk.submit_inventory(
        "TERM-BING",
        [
            comp(
                "BING-single",
                "SINGLE_READ",
                "1.9.4",
                enabled=True,
                pids=[(6101, "1.9.4", "RUNNING")],
            )
        ],
        report_id="INV-BING-OPEN",
        captured_at="SHIFT-0-TONIGHT",
        reporter="roving-count",
        whole_model="QuickScan PaperOnly",
        whole_version="4.0.0-fictional",
    )
    # A fresh inventory alone proves exit + corrected running artifact.
    desk.submit_inventory(
        "TERM-BING",
        [
            {
                "uid": "BING-single",
                "part_no": "DCP-710",
                "name": "fictional barcode decoder module",
                "installed_version": "2.0.0-fictional",
                "function": "SINGLE_READ",
                "calls_part": True,
                "function_enabled": True,
                "processes": [
                    {"pid": 6101, "running_version": "1.9.4", "state": "EXITED"},
                    {"pid": 6201, "running_version": "2.0.0-fictional", "state": "RUNNING"},
                ],
            }
        ],
        report_id="INV-BING-CORRECTED-COUNT",
        captured_at="SHIFT-0-TONIGHT",
        reporter="field-count",
        raw_note="No separate repair receipt; process inventory is the receipt.",
    )
    issue_closure(
        desk.data,
        "TERM-BING",
        closure_id="CL-BING-BRIEF-FINAL",
        kind="FINAL",
        route="REPLACED_NEW_PROCESS",
        at="SHIFT-0-TONIGHT",
        statement="Closed directly from corrected field inventory and old PID exit evidence.",
        scope={"summary": "Positive intermediate state required by the exercise."},
    )

    # Regression: old version/process returns; spare is merely lent away.
    desk.submit_inventory(
        "TERM-BING",
        [
            comp(
                "BING-single",
                "SINGLE_READ",
                "1.9.4",
                enabled=True,
                pids=[
                    (6201, "2.0.0-fictional", "EXITED"),
                    (6301, "1.9.4", "RUNNING"),
                ],
            )
        ],
        report_id="INV-BING-ROLLBACK",
        captured_at="SHIFT-0-TONIGHT-LATER",
        reporter="repeat-process-count",
        raw_note="Machine returned to the old decoder process/version.",
    )
    desk.add_resource_event(
        "SPARE-BING-1",
        event="LOANED_OUT",
        machine_no=None,
        linked_machine="TERM-OTHER-9",
        available=False,
        event_id="RES-SPARE-LOANED",
        note="Spare was only lent to another person; that does not remediate TERM-BING.",
    )
    bing_ticket = next(t for t in desk.data["tickets"] if t["id"] == "TKT-BING")
    refresh_ticket_status(desk.data, bing_ticket)
    bing_ticket["promise"] = {
        "kind": "NO_DOWNTIME_REPLACE",
        "status": "IMPOSSIBLE",
        "through_shift": None,
        "reason": "Old process regressed and SPARE-BING-1 is loaned to TERM-OTHER-9.",
        "options": [
            "recover SPARE-BING-1 before promising hot swap",
            "schedule a downtime replacement window",
        ],
    }

    desk.save()
    return desk


def main() -> None:
    desk = build_seed()
    print(f"seeded {len(desk.data['machines'])} machines at {desk.path}")


if __name__ == "__main__":
    main()
