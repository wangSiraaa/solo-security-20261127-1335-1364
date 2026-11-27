"""Built-in acceptance scenario.

Run with:  ./handoff demo
It uses an in-memory state, so it never touches the operator's state file.
"""

from __future__ import annotations

from typing import Any

from .core import HandOffError, handle, new_state, seed_demo


def _active_count(state: dict[str, Any], order_id: str) -> int:
    order = state["orders"][order_id]
    return sum(1 for ref in order["entries"] if ref.get("status") != "reversed")


def _formal_sample_count(state: dict[str, Any], order_id: str) -> int:
    order = state["orders"][order_id]
    ids = {e["id"] for e in state["entries"]
           if e.get("order_id") == order_id and e.get("status") in {"formal", "formal_claim"}
           and e.get("capability") == "read_sample"}
    return len(ids)


def _check(checks: list[dict[str, Any]], name: str, condition: bool, detail: Any = None) -> None:
    checks.append({"name": name, "passed": bool(condition), "detail": detail})
    if not condition:
        raise AssertionError(name)


def _main_provenance_scenario(checks: list[dict[str, Any]], steps: list[dict[str, Any]]) -> dict[str, Any]:
    state = new_state()
    seed_demo(state)

    r1 = handle(state, {
        "op": "input", "at": "T1",
        "interface": "J2", "capability": "read_sample",
        "value": "SPEC-V-001", "order": "WO-V", "fixture": "F1",
    })
    steps.append({"case": "trusted original reader while locked", "result": r1})
    _check(checks, "original locked read is formal", r1["decision"] == "formal" and r1["order_id"] == "WO-V", r1)
    _check(checks, "WO-V has exactly one formal sample reading", _formal_sample_count(state, "WO-V") == 1)

    handle(state, {"op": "remove", "interface": "J2", "reason": "reader head replacement"})
    handle(state, {"op": "install", "interface": "J2", "component": "BLK-READ-2", "circuit": "C-SCAN"})
    w_bad = handle(state, {"op": "witness", "interface": "J2", "capability": "read_sample"})
    handle(state, {"op": "freeze", "circuit": "C-SCAN", "reason": "reader identity unconfirmed"})
    r2 = handle(state, {
        "op": "input", "at": "T2",
        "interface": "J2", "capability": "read_sample",
        "value": "SPEC-V-002", "order": "WO-V", "fixture": "F1",
    })
    steps.append({"case": "same port after reconnect has unconfirmed source", "result": r2})
    _check(checks, "unconfirmed reconnect read is quarantined", r2["decision"] == "quarantined", r2)
    _check(checks, "unconfirmed read does not post another sample to WO-V", _formal_sample_count(state, "WO-V") == 1)
    _check(checks, "sample read is retained for possible claim", r2["retention"] == "retained")

    r_knob = handle(state, {
        "op": "input", "at": "T3",
        "interface": "J1", "capability": "turn_page",
        "value": "page=2", "order": "WO-V", "fixture": "F1",
    })
    steps.append({"case": "independent knob circuit remains continuous", "result": r_knob})
    _check(checks, "continuous knob input remains formal", r_knob["decision"] == "formal", r_knob)
    _check(checks, "knob adds an operation but no second sample", _formal_sample_count(state, "WO-V") == 1)

    claim_q1 = handle(state, {"op": "claim", "quarantine": r2["quarantine_id"], "order": "WO-V"})
    steps.append({"case": "current trust cannot retroactively prove unknown interval", "result": claim_q1})
    _check(checks, "unresolved source claim is rejected", claim_q1["status"] == "claim_rejected", claim_q1)

    handle(state, {"op": "remove", "interface": "J2"})
    handle(state, {"op": "install", "interface": "J2", "component": "BLK-DUO-1", "circuit": "C-SCAN"})
    w_ok = handle(state, {"op": "witness", "interface": "J2", "capability": "read_sample"})
    _check(checks, "restored custodian component is rewitnessed", w_ok["trusted"] is True, w_ok)

    handle(state, {"op": "pattern_unload", "fixture": "F1"})
    handle(state, {"op": "fixture_load", "fixture": "F1", "pattern": "P-RELOAD", "locked": True})

    r3 = handle(state, {
        "op": "input", "at": "T4",
        "interface": "J2", "capability": "read_sample",
        "value": "SPEC-R-NEXT", "order": "WO-V", "fixture": "F1",
    })
    steps.append({"case": "trusted reader but arrival fixture is the next pattern", "result": r3})
    _check(checks, "fixture mismatch read is quarantined", r3["decision"] == "quarantined", r3)
    _check(checks, "fixture mismatch does not add sample to original WO-V", _formal_sample_count(state, "WO-V") == 1)
    _check(checks, "original order still contains its sample plus continuous knob operation", _active_count(state, "WO-V") == 2)

    claim_wrong = handle(state, {"op": "claim", "quarantine": r3["quarantine_id"], "order": "WO-V"})
    steps.append({"case": "historical next-pattern read cannot be claimed to original order", "result": claim_wrong})
    _check(checks, "claim to original order rejected by historical context", claim_wrong["status"] == "claim_rejected", claim_wrong)

    claim_right = handle(state, {"op": "claim", "quarantine": r3["quarantine_id"], "order": "WO-R"})
    steps.append({"case": "same read can be returned to matching next order", "result": claim_right})
    _check(checks, "claim to matching next order succeeds", claim_right["status"] == "claimed", claim_right)
    _check(checks, "WO-V still has only its original sample", _formal_sample_count(state, "WO-V") == 1)
    _check(checks, "WO-R receives the context-matching entry", _formal_sample_count(state, "WO-R") == 1)

    return {"WO_V_sample_entries": _formal_sample_count(state, "WO-V"), "WO_V_total_active": _active_count(state, "WO-V"), "quarantine_open": sum(q["status"] == "unclaimed" for q in state["quarantine"])}


def _backup_scenario(checks: list[dict[str, Any]], steps: list[dict[str, Any]]) -> dict[str, Any]:
    state = new_state()
    seed_demo(state)
    buffered = handle(state, {
        "op": "buffer_read", "interface": "J2", "value": "VOLATILE-UNRETRIEVED",
        "order": "WO-V", "fixture": "F1",
    })
    advice = handle(state, {"op": "advise"})
    steps.append({"case": "advice before retrieving volatile buffer", "result": advice})
    _check(checks, "advice says stay on original while volatile read is buffered",
           advice["recommendation"] == "stay_original_until_volatile_reading_is_collected", advice)

    blocked = None
    try:
        handle(state, {"op": "bus_switch_backup", "board": "BLK-BACKUP-9"})
    except HandOffError as exc:
        blocked = {"code": exc.code, "message": exc.message, "details": exc.details}
    steps.append({"case": "switch without explicit loss acceptance is blocked", "result": blocked})
    _check(checks, "switch is blocked pending loss confirmation", blocked and blocked["code"] == "confirmation_required", blocked)

    switched = handle(state, {"op": "bus_switch_backup", "board": "BLK-BACKUP-9", "accept_loss": True})
    steps.append({"case": "confirmed switch records real volatile loss", "result": switched})
    _check(checks, "one volatile reading is recorded lost", len(switched["disconnect"]["lost"]) == 1, switched)
    _check(checks, "lever is on backup board", state["bus"]["lever"] == "backup")

    handle(state, {"op": "backup_attach", "component": "BLK-READ-2"})
    occupied = None
    try:
        handle(state, {"op": "backup_attach", "component": "BLK-DUO-1"})
    except HandOffError as exc:
        occupied = {"code": exc.code, "message": exc.message}
    _check(checks, "backup board rejects a second simultaneous item", occupied and occupied["code"] == "backup_slot_occupied", occupied)

    handoff = handle(state, {"op": "handoff_create", "id": "H-WORKER", "worker": "worker-7",
                             "subject": "WO-R", "mode": "backup"})
    _check(checks, "worker handoff waits for a real job receipt",
           handoff["handoff"]["status"] == "awaiting_receipt", handoff)
    job_receipt = handle(state, {"op": "receipt_record", "id": "JOB-RECEIPT-1",
                                 "handoff": "H-WORKER", "kind": "maintenance", "status": "completed"})
    _check(checks, "worker handoff is honored only when receipt arrives",
           job_receipt["handoff"]["status"] == "honored", job_receipt)

    original_conflict = None
    try:
        handle(state, {"op": "bus_connect_original"})
    except HandOffError as exc:
        original_conflict = {"code": exc.code}
    _check(checks, "original cannot be connected while backup is live",
           original_conflict and original_conflict["code"] == "lever_conflict", original_conflict)

    handle(state, {"op": "backup_detach"})
    handle(state, {"op": "bus_disconnect_backup"})
    handle(state, {"op": "bus_connect_original"})
    _check(checks, "lever can return to original after backup disconnect", state["bus"]["lever"] == "original")
    return {"losses": len(state["losses"])}


def _reset_scenario(checks: list[dict[str, Any]], steps: list[dict[str, Any]]) -> dict[str, Any]:
    # Late report: one beat of stale trust is corrected when the real receipt lands.
    state = new_state()
    seed_demo(state)
    e1 = handle(state, {"op": "input", "interface": "J2", "capability": "read_sample",
                       "value": "BEFORE-RESET", "order": "WO-V", "fixture": "F1"})
    reset_effective_seq = state["seq"]
    stale = handle(state, {"op": "input", "interface": "J2", "capability": "read_sample",
                           "value": "AFTER-RESET-BEFORE-RECEIPT", "order": "WO-V", "fixture": "F1"})
    _check(checks, "without receipt yet, continuous witness allows one beat", stale["decision"] == "formal", stale)

    receipt = handle(state, {
        "op": "receipt_record", "id": "RST-LATE", "kind": "reset", "scope": "backplane",
        "effective_seq": reset_effective_seq,
    })
    steps.append({"case": "late backplane reset receipt corrects stale posting", "result": receipt})
    _check(checks, "late receipt reverses exactly the stale entry",
           len(receipt["reversed_entries"]) == 1 and receipt["reversed_entries"][0]["entry_id"] == stale["entry_id"], receipt)
    _check(checks, "pre-reset entry remains on formal order", _active_count(state, "WO-V") == 1)
    _check(checks, "reversed reading is in quarantine", len(receipt["quarantined"]) == 1)

    after_freeze = handle(state, {"op": "input", "interface": "J2", "capability": "read_sample",
                                  "value": "FROZEN", "order": "WO-V", "fixture": "F1"})
    _check(checks, "invalidated mapping blocks later input", after_freeze["decision"] == "quarantined", after_freeze)
    handle(state, {"op": "witness", "interface": "J2", "capability": "read_sample"})
    rewitnessed = handle(state, {"op": "input", "interface": "J2", "capability": "read_sample",
                                 "value": "REWITNESSED", "order": "WO-V", "fixture": "F1"})
    _check(checks, "post-reset rewitness restores formal posting", rewitnessed["decision"] == "formal", rewitnessed)

    # Early report variant on a fresh state.
    early = new_state()
    seed_demo(early)
    early_receipt = handle(early, {"op": "receipt_record", "id": "RST-EARLY", "kind": "reset", "scope": "backplane"})
    early_bad = handle(early, {"op": "input", "interface": "J2", "capability": "read_sample",
                              "value": "AFTER-EARLY-REPORT", "order": "WO-V", "fixture": "F1"})
    _check(checks, "early reset report freezes channel before bad posting",
           early_bad["decision"] == "quarantined" and not early_receipt["reversed_entries"], early_bad)
    return {"late_reversals": 1, "early_bad_postings_prevented": 1}


def run_demo() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    steps: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    try:
        summaries.append({"provenance": _main_provenance_scenario(checks, steps)})
        summaries.append({"backup": _backup_scenario(checks, steps)})
        summaries.append({"reset": _reset_scenario(checks, steps)})
        passed = all(c["passed"] for c in checks)
    except Exception as exc:  # noqa: BLE001 - demo should report failure JSON
        return {"all_assertions_passed": False, "error": type(exc).__name__, "message": str(exc),
                "checks": checks, "steps": steps}
    return {
        "all_assertions_passed": passed,
        "check_count": len(checks),
        "passed_count": sum(c["passed"] for c in checks),
        "checks": checks,
        "summaries": summaries,
        "key_decisions": [s for s in steps if s["case"] in {
            "trusted original reader while locked",
            "same port after reconnect has unconfirmed source",
            "continuous knob input remains formal",
            "trusted reader but arrival fixture is the next pattern",
        }],
    }


if __name__ == "__main__":
    import json

    print(json.dumps(run_demo(), ensure_ascii=False, indent=2))
