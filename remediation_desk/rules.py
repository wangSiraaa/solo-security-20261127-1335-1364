"""Advisory matching, finding reconciliation, and closure rules."""
from __future__ import annotations

from typing import Any

from .models import ClosureKind, ItemKind, ItemStatus, TicketStatus
from .versions import version_in_range


# ---------------------------------------------------------------------------
# Inventory lookup
# ---------------------------------------------------------------------------


def machine_reports(data: dict[str, Any], machine_no: str) -> list[dict[str, Any]]:
    return sorted(
        (r for r in data["inventory_reports"] if r["machine_no"] == machine_no),
        key=lambda r: r["seq"],
    )


def latest_report(data: dict[str, Any], machine_no: str) -> dict[str, Any] | None:
    rows = machine_reports(data, machine_no)
    return rows[-1] if rows else None


def find_component(report: dict[str, Any] | None, uid: str) -> dict[str, Any] | None:
    if not report:
        return None
    for comp in report.get("components", []):
        if comp.get("uid") == uid:
            return comp
    return None


def latest_component(data: dict[str, Any], machine_no: str, uid: str) -> dict[str, Any] | None:
    report = latest_report(data, machine_no)
    return find_component(report, uid)


def advisories_for_code(data: dict[str, Any], code: str) -> list[dict[str, Any]]:
    rows = [a for a in data["advisories"] if a["code"] == code]
    return sorted(rows, key=lambda a: a["revision"])


def active_advisory(data: dict[str, Any], code: str) -> dict[str, Any] | None:
    for adv in reversed(advisories_for_code(data, code)):
        if adv["status"] == "ACTIVE":
            return adv
    return None


def latest_lineage_advisory(data: dict[str, Any], code: str) -> dict[str, Any] | None:
    rows = advisories_for_code(data, code)
    return rows[-1] if rows else None


def advisory_covers_part(adv: dict[str, Any], comp: dict[str, Any]) -> bool:
    return comp.get("part_no") in adv.get("part_numbers", [])


def version_affected(adv: dict[str, Any], version: str | None) -> bool:
    return bool(version) and version_in_range(version, adv["affected_range"])


# ---------------------------------------------------------------------------
# Ticket/finding synchronization
# ---------------------------------------------------------------------------


def _ensure_item(ticket: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    existing = next((i for i in ticket["items"] if i["key"] == item["key"]), None)
    if existing:
        return existing
    item.setdefault("status", ItemStatus.OPEN.value)
    item.setdefault("status_history", [])
    ticket["items"].append(item)
    return item


def sync_ticket_items(data: dict[str, Any], ticket: dict[str, Any]) -> dict[str, Any]:
    """Create stable findings for every affected part/process ever reported."""
    lineage_adv = latest_lineage_advisory(data, ticket["advisory_code"])
    if lineage_adv is None:
        return ticket

    for report in machine_reports(data, ticket["machine_no"]):
        for comp in report.get("components", []):
            if not advisory_covers_part(lineage_adv, comp):
                continue

            uid = comp["uid"]
            function = comp.get("function")
            has_running_affected_process = any(
                proc.get("state", "RUNNING") == "RUNNING"
                and version_affected(lineage_adv, proc.get("running_version"))
                for proc in comp.get("processes", [])
            )
            enablement = lineage_adv.get("enablement", "FUNCTION_ENABLED")
            in_enabled_scope = (
                enablement == "ALWAYS"
                or (
                    enablement == "FUNCTION_ENABLED"
                    and bool(function)
                    and (bool(comp.get("function_enabled")) or has_running_affected_process)
                )
            )
            if (
                in_enabled_scope
                and version_affected(lineage_adv, comp.get("installed_version"))
            ):
                _ensure_item(
                    ticket,
                    {
                        "key": f"installed:{uid}",
                        "kind": ItemKind.INSTALLED_COMPONENT.value,
                        "uid": uid,
                        "function": function,
                        "part_no": comp.get("part_no"),
                        "affected_version": comp.get("installed_version"),
                        "first_seen_report": report["id"],
                        "required_evidence": (
                            "A later full inventory showing the fixed part/version, "
                            "or a signed durable disablement covering this function "
                            "with the old process exited."
                        ),
                    },
                )

            for proc in comp.get("processes", []):
                proc_version = proc.get("running_version")
                if not version_affected(lineage_adv, proc_version):
                    continue
                pid = proc.get("pid")
                _ensure_item(
                    ticket,
                    {
                        "key": f"process:{uid}:{pid}",
                        "kind": ItemKind.RUNNING_PROCESS.value,
                        "uid": uid,
                        "pid": pid,
                        "function": function,
                        "part_no": comp.get("part_no"),
                        "affected_version": proc_version,
                        "first_seen_report": report["id"],
                        "required_evidence": (
                            "An inventory/process receipt showing this PID exited and "
                            "a new process running the corrected version."
                        ),
                    },
                )
    return ticket


# ---------------------------------------------------------------------------
# Current-state assessment
# ---------------------------------------------------------------------------


def _process_state(
    data: dict[str, Any], machine_no: str, uid: str, pid: int
) -> tuple[bool, str, str | None]:
    """Return (is_old_copy_currently_running, state, evidence_report_id)."""
    latest_record: dict[str, Any] | None = None
    latest_record_report: str | None = None
    for report in machine_reports(data, machine_no):
        comp = find_component(report, uid)
        if not comp:
            continue
        for proc in comp.get("processes", []):
            if proc.get("pid") == pid:
                latest_record = proc
                latest_record_report = report["id"]

    latest = latest_report(data, machine_no)
    comp = find_component(latest, uid)
    running_pids: set[int] = set()
    if comp:
        for proc in comp.get("processes", []):
            state = proc.get("state", "RUNNING")
            if state == "RUNNING":
                running_pids.add(proc["pid"])

    if latest_record and latest_record.get("state") == "EXITED":
        return False, "EXITED", latest_record_report
    if pid in running_pids:
        return True, "RUNNING", latest_record_report
    if latest and latest.get("full_process_list", True):
        return False, "EXITED_INFERRED_FROM_FULL_LIST", latest["id"]
    return True, "UNKNOWN_NO_EXIT_EVIDENCE", latest_record_report


def _current_fixed_process(
    data: dict[str, Any], machine_no: str, uid: str, adv: dict[str, Any], old_pids: set[int]
) -> dict[str, Any] | None:
    report = latest_report(data, machine_no)
    comp = find_component(report, uid)
    if not comp:
        return None
    for proc in comp.get("processes", []):
        if proc.get("state", "RUNNING") != "RUNNING":
            continue
        if proc.get("pid") in old_pids:
            continue
        version = proc.get("running_version")
        if version and not version_affected(adv, version):
            return {"process": proc, "report_id": report["id"]}
    return None


def _receipts_for_machine(data: dict[str, Any], collection: str, machine_no: str):
    return [r for r in data[collection] if r["machine_no"] == machine_no]


def _required_function_targets(historical_functions: dict[str, set[str]]) -> set[tuple[str, str]]:
    return {
        (uid, function)
        for uid, functions in historical_functions.items()
        for function in functions
    }


def _receipt_covered_targets(
    receipts: list[dict[str, Any]], required: set[tuple[str, str]]
) -> set[tuple[str, str]]:
    covered: set[tuple[str, str]] = set()
    for receipt in receipts:
        for uid in receipt.get("covered_uids", []):
            covered.update(target for target in required if target[0] == uid)
        for function in receipt.get("covered_functions", []):
            covered.update(target for target in required if target[1] == function)
    return covered


def assess_ticket(
    data: dict[str, Any],
    ticket_or_id: dict[str, Any] | str,
    *,
    advisory_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if isinstance(ticket_or_id, str):
        ticket = next(t for t in data["tickets"] if t["id"] == ticket_or_id)
    else:
        ticket = ticket_or_id

    sync_ticket_items(data, ticket)
    machine_no = ticket["machine_no"]
    code = ticket["advisory_code"]
    adv = advisory_override or active_advisory(data, code)
    latest = latest_report(data, machine_no)

    item_views: list[dict[str, Any]] = []
    current_old_installed: list[dict[str, Any]] = []
    current_old_processes: list[dict[str, Any]] = []
    uids_with_old_processes: set[str] = set()
    old_pids_by_uid: dict[str, set[int]] = {}

    for item in ticket["items"]:
        view = dict(item)
        is_open = False
        evidence: list[str] = []
        current_state = "NO_ACTIVE_BULLETIN" if adv is None else "UNKNOWN"

        if adv is not None and item["kind"] == ItemKind.INSTALLED_COMPONENT.value:
            comp = latest_component(data, machine_no, item["uid"])
            if comp is None:
                is_open = False
                current_state = "COMPONENT_ABSENT_IN_LATEST_INVENTORY"
                if latest:
                    evidence.append(latest["id"])
            elif advisory_covers_part(adv, comp) and version_affected(
                adv, comp.get("installed_version")
            ):
                is_open = True
                current_state = (
                    f"OLD_INSTALLED_VERSION {comp.get('installed_version')}"
                )
                current_old_installed.append(item)
            else:
                current_state = (
                    f"FIXED_OR_OUT_OF_RANGE {comp.get('installed_version')}"
                )
                if latest:
                    evidence.append(latest["id"])

        elif item["kind"] == ItemKind.RUNNING_PROCESS.value:
            uid = item["uid"]
            pid = int(item["pid"])
            uids_with_old_processes.add(uid)
            old_pids_by_uid.setdefault(uid, set()).add(pid)
            running, state, evidence_id = _process_state(data, machine_no, uid, pid)
            current_state = state
            if running:
                is_open = True
                current_old_processes.append(item)
            else:
                if evidence_id:
                    evidence.append(evidence_id)

        view.update(
            {
                "is_open": is_open,
                "current_state": current_state,
                "current_evidence_ids": evidence,
                "clearing_evidence_hint": item["required_evidence"],
            }
        )
        item["status"] = ItemStatus.OPEN.value if is_open else ItemStatus.CLEARED.value
        item_views.append(view)

    # ---------------- replacement route ----------------
    replacement_missing: list[str] = []
    replacement_evidence: list[str] = []
    if adv is None:
        replacement_missing.append("No ACTIVE corrected advisory is in the desk.")
    if latest is None:
        replacement_missing.append("No terminal inventory report has been filed.")
    if current_old_installed:
        replacement_missing.append(
            "Latest inventory still has affected installed part(s): "
            + ", ".join(i["key"] for i in current_old_installed)
        )
    if current_old_processes:
        replacement_missing.append(
            "An old decoder process is still running: "
            + ", ".join(i["key"] for i in current_old_processes)
        )

    if adv is not None:
        for uid in sorted(uids_with_old_processes):
            fixed = _current_fixed_process(
                data, machine_no, uid, adv, old_pids_by_uid.get(uid, set())
            )
            if not fixed:
                replacement_missing.append(
                    f"Missing new-process inventory receipt for {uid}: prove old PID "
                    "exit and show a running corrected version."
                )
            else:
                replacement_evidence.append(fixed["report_id"])

    if latest and not current_old_installed and not current_old_processes:
        replacement_evidence.append(latest["id"])

    repair_receipts = _receipts_for_machine(data, "repair_receipts", machine_no)
    replacement_evidence.extend(r["id"] for r in repair_receipts)

    replacement = {
        "eligible": not replacement_missing,
        "missing": replacement_missing,
        "evidence_ids": sorted(set(replacement_evidence)),
    }

    # ---------------- disablement route ----------------
    historical_functions: dict[str, set[str]] = {}
    for report in machine_reports(data, machine_no):
        for comp in report.get("components", []):
            if adv is not None and not advisory_covers_part(adv, comp):
                continue
            uid = comp.get("uid")
            function = comp.get("function")
            if uid and function:
                historical_functions.setdefault(uid, set()).add(function)

    disable_missing: list[str] = []
    temporary_missing: list[str] = []
    disable_evidence: list[str] = []

    if not historical_functions:
        disable_missing.append("No optional decoder function binding is known.")
        temporary_missing.append("No optional decoder function binding is known.")

    for uid, functions in sorted(historical_functions.items()):
        comp = latest_component(data, machine_no, uid)
        if comp is None:
            disable_missing.append(f"{uid} absent; disablement cannot be verified in place.")
            temporary_missing.append(
                f"{uid} absent; temporary switch-off cannot be verified in place."
            )
            continue
        if comp.get("function_enabled", False):
            disable_missing.append(f"{uid}:{comp.get('function')} is still enabled.")
            temporary_missing.append(f"{uid}:{comp.get('function')} is still enabled.")

    if current_old_processes:
        msg = "Old process(es) still running; switching a flag is not enough."
        disable_missing.append(msg)
        temporary_missing.append(msg)

    disable_receipts = _receipts_for_machine(data, "disablement_receipts", machine_no)
    durable_receipts = [r for r in disable_receipts if r.get("durable")]
    temporary_receipts = [r for r in disable_receipts if r.get("temporary")]

    required_targets = _required_function_targets(historical_functions)
    durable_covered = _receipt_covered_targets(durable_receipts, required_targets)
    durable_missing_targets = required_targets - durable_covered
    if durable_missing_targets:
        disable_missing.append(
            "Missing durable/signed disablement receipt covering every calling "
            "function instance: "
            + ", ".join(f"{uid}:{function}" for uid, function in sorted(durable_missing_targets))
        )
    elif not any(r.get("old_processes_stopped") for r in durable_receipts):
        disable_missing.append("Disablement receipt does not certify old processes stopped.")
    else:
        disable_evidence.extend(r["id"] for r in durable_receipts)

    temporary_covered = _receipt_covered_targets(temporary_receipts, required_targets)
    temporary_missing_targets = required_targets - temporary_covered
    if temporary_missing_targets:
        temporary_missing.append(
            "Missing time-boxed temporary switch-off receipt covering: "
            + ", ".join(
                f"{uid}:{function}" for uid, function in sorted(temporary_missing_targets)
            )
        )
    elif not any(r.get("old_processes_stopped") for r in temporary_receipts):
        temporary_missing.append(
            "Temporary control does not certify old processes are stopped."
        )
    else:
        disable_evidence.extend(r["id"] for r in temporary_receipts)

    if latest:
        disable_evidence.append(latest["id"])

    disablement = {
        "eligible": not disable_missing,
        "missing": disable_missing,
        "evidence_ids": sorted(set(disable_evidence)),
    }
    temporary_control = {
        "eligible": not temporary_missing,
        "missing": temporary_missing,
        "evidence_ids": sorted(
            set(disable_evidence + [r["id"] for r in temporary_receipts])
        ),
    }

    open_count = sum(1 for i in item_views if i["is_open"])
    result = {
        "ticket_id": ticket["id"],
        "machine_no": machine_no,
        "advisory_code": code,
        "active_advisory_id": adv["id"] if adv else None,
        "open_item_count": open_count,
        "items": item_views,
        "routes": {
            "REPLACED_NEW_PROCESS": replacement,
            "FUNCTIONS_DISABLED": disablement,
            "TEMPORARY_CONTROL": temporary_control,
        },
        "can_close_final": bool(adv)
        and bool(item_views)
        and (replacement["eligible"] or disablement["eligible"]),
        "can_close_temporary": temporary_control["eligible"],
        "eligible_final_routes": [
            name
            for name, assess in (
                ("REPLACED_NEW_PROCESS", replacement),
                ("FUNCTIONS_DISABLED", disablement),
            )
            if assess["eligible"]
        ],
    }
    ticket["last_assessment"] = {
        key: result[key]
        for key in (
            "active_advisory_id",
            "open_item_count",
            "can_close_final",
            "can_close_temporary",
            "eligible_final_routes",
        )
    }
    return result


# ---------------------------------------------------------------------------
# Closures
# ---------------------------------------------------------------------------


def _shift_index(data: dict[str, Any], shift: str | None) -> int | None:
    if not shift:
        return None
    order = data.get("shift_order") or []
    try:
        return order.index(shift)
    except ValueError:
        return None


def issue_closure(
    data: dict[str, Any],
    machine_no: str,
    *,
    closure_id: str | None = None,
    kind: str = ClosureKind.FINAL.value,
    route: str | None = None,
    ticket_id: str | None = None,
    at: str | None = None,
    statement: str = "",
    scope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ticket = None
    if ticket_id:
        ticket = next(t for t in data["tickets"] if t["id"] == ticket_id)
    else:
        candidates = [t for t in data["tickets"] if t["machine_no"] == machine_no]
        if not candidates:
            raise KeyError(f"no ticket for {machine_no}")
        ticket = candidates[-1]

    assessment = assess_ticket(data, ticket)
    at_shift = at or data.get("current_shift", "")

    if kind == ClosureKind.FINAL.value:
        if not assessment["active_advisory_id"]:
            raise ValueError(
                "Cannot issue an advisory closure while the bulletin is withdrawn "
                "without an active corrected successor."
            )
        chosen_route = route or (
            assessment["eligible_final_routes"][0]
            if assessment["eligible_final_routes"]
            else None
        )
        if chosen_route not in assessment["eligible_final_routes"]:
            missing = assessment["routes"].get(chosen_route or "", {}).get("missing")
            raise ValueError(
                "Final closure evidence is insufficient"
                + (f" for {chosen_route}: {'; '.join(missing)}" if missing else "")
            )
        evidence_ids = assessment["routes"][chosen_route]["evidence_ids"]
        valid_through = None
        ticket["status"] = TicketStatus.CLOSED.value
        history_reason = f"final closure via {chosen_route}"

    elif kind == ClosureKind.TEMPORARY_CONTROL.value:
        temp = assessment["routes"]["TEMPORARY_CONTROL"]
        if route and route != "TEMPORARY_CONTROL":
            raise ValueError("Temporary closures use the TEMPORARY_CONTROL route.")
        if not temp["eligible"]:
            raise ValueError(
                "Temporary control evidence is insufficient: "
                + "; ".join(temp["missing"])
            )
        chosen_route = "TEMPORARY_CONTROL"
        evidence_ids = temp["evidence_ids"]
        temp_receipts = [
            r
            for r in data["disablement_receipts"]
            if r["machine_no"] == machine_no and r.get("temporary")
        ]
        valid_through = next(
            (
                r.get("valid_through_shift")
                for r in reversed(temp_receipts)
                if r.get("valid_through_shift")
            ),
            None,
        )
        ticket["status"] = TicketStatus.MITIGATED.value
        history_reason = "time-boxed temporary control closure"

    elif kind == ClosureKind.WITHDRAWN_BULLETIN.value:
        if assessment["active_advisory_id"] is not None:
            raise ValueError(
                "Cannot issue a withdrawal write-off while an ACTIVE successor bulletin exists."
            )
        chosen_route = "BULLETIN_WITHDRAWN"
        withdrawn_ids = [
            a["id"]
            for a in data["advisories"]
            if a["code"] == ticket["advisory_code"] and a["status"] == "WITHDRAWN"
        ]
        evidence_ids = withdrawn_ids
        valid_through = None
        ticket["status"] = TicketStatus.CANCELED.value
        history_reason = "bulletin withdrawn; ticket written off without a rectification claim"

    else:
        raise ValueError(f"unsupported closure kind: {kind}")

    cid = closure_id or f"CL-{len(data['closures']) + 1:04d}"
    if any(c["id"] == cid for c in data["closures"]):
        cid = f"{cid}-{len(data['closures']) + 1}"

    closure = {
        "id": cid,
        "ticket_id": ticket["id"],
        "machine_no": machine_no,
        "advisory_code": ticket["advisory_code"],
        "issued_under_advisory_id": assessment["active_advisory_id"],
        "issued_at_shift": at_shift,
        "kind": kind,
        "route": chosen_route,
        "evidence_ids": evidence_ids,
        "valid_through_shift": valid_through,
        "statement": statement,
        "scope": scope or {},
        "record_version": 1,
    }
    data["closures"].append(closure)
    ticket.setdefault("status_history", []).append(
        {"status": ticket["status"], "at": at_shift, "reason": history_reason, "closure_id": cid}
    )
    return closure


def verify_closure(data: dict[str, Any], closure_id: str) -> dict[str, Any]:
    closure = next(c for c in data["closures"] if c["id"] == closure_id)
    ticket = next(t for t in data["tickets"] if t["id"] == closure["ticket_id"])
    assessment = assess_ticket(data, ticket)

    evidence_collections = (
        "advisories",
        "inventory_reports",
        "repair_receipts",
        "disablement_receipts",
        "staffing_reports",
        "resource_events",
    )
    existing_evidence: set[str] = set()
    for collection in evidence_collections:
        existing_evidence.update(row["id"] for row in data.get(collection, []))
    missing_evidence = [e for e in closure["evidence_ids"] if e not in existing_evidence]

    current_index = _shift_index(data, data.get("current_shift"))
    until_index = _shift_index(data, closure.get("valid_through_shift"))
    expired = (
        current_index is not None
        and until_index is not None
        and current_index > until_index
    )
    record_valid = not missing_evidence
    staffing_dependency = closure.get("scope", {}).get("staffing_dependency", "NONE_REQUIRED")
    explanation = closure.get("scope", {}).get("summary", closure.get("statement", ""))

    if closure["kind"] == ClosureKind.WITHDRAWN_BULLETIN.value:
        effective = assessment["active_advisory_id"] is None and record_valid
        return {
            "closure_id": closure_id,
            "machine_no": closure["machine_no"],
            "kind": closure["kind"],
            "route": closure["route"],
            "record_valid": record_valid,
            "missing_evidence_ids": missing_evidence,
            "expired_by_shift": False,
            "technical_scope_holds": False,
            "effective_under_current_bulletin": effective,
            "rectification_closure": False,
            "historical_maintenance_receipt_valid": record_valid,
            "staffing_dependency": closure.get("scope", {}).get(
                "staffing_dependency", "NONE_REQUIRED"
            ),
            "scope_explanation": explanation
            or "Bulletin withdrawal write-off; this is not a technical rectification closure.",
            "current_missing": [],
        }

    if closure["kind"] == ClosureKind.FINAL.value:
        route = closure["route"]
        route_state = assessment["routes"].get(route, {})
        effective_under_current_bulletin = (
            assessment["active_advisory_id"] is not None
            and route_state.get("eligible", False)
        )
        technical_scope_holds = route_state.get("eligible", False)
    else:
        temp = assessment["routes"]["TEMPORARY_CONTROL"]
        effective_under_current_bulletin = temp["eligible"] and not expired
        technical_scope_holds = temp["eligible"]

    return {
        "closure_id": closure_id,
        "machine_no": closure["machine_no"],
        "kind": closure["kind"],
        "route": closure["route"],
        "record_valid": record_valid,
        "missing_evidence_ids": missing_evidence,
        "expired_by_shift": expired,
        "technical_scope_holds": technical_scope_holds,
        "effective_under_current_bulletin": effective_under_current_bulletin,
        "rectification_closure": True,
        "historical_maintenance_receipt_valid": record_valid,
        "staffing_dependency": staffing_dependency,
        "scope_explanation": explanation,
        "current_missing": assessment["routes"]
        .get(closure.get("route", ""), {})
        .get("missing", []),
    }


def refresh_ticket_status(data: dict[str, Any], ticket: dict[str, Any]) -> dict[str, Any]:
    """Reopen a closed/mitigated ticket when fresh evidence regresses it."""
    assessment = assess_ticket(data, ticket)
    at_shift = data.get("current_shift", "")

    effective_final = any(
        c["ticket_id"] == ticket["id"]
        and c["kind"] == ClosureKind.FINAL.value
        and verify_closure(data, c["id"])["effective_under_current_bulletin"]
        for c in data["closures"]
    )
    if ticket["status"] == TicketStatus.CLOSED.value and not effective_final:
        ticket["status"] = TicketStatus.OPEN.value
        ticket.setdefault("status_history", []).append(
            {
                "status": TicketStatus.OPEN.value,
                "at": at_shift,
                "reason": "fresh inventory contradicts the final closure; reopened without deleting history",
            }
        )
    elif ticket["status"] == TicketStatus.MITIGATED.value and not assessment[
        "can_close_temporary"
    ]:
        ticket["status"] = TicketStatus.OPEN.value
        ticket.setdefault("status_history", []).append(
            {
                "status": TicketStatus.OPEN.value,
                "at": at_shift,
                "reason": "temporary control evidence no longer holds",
            }
        )
    return assessment


def closure_candidates(data: dict[str, Any], code: str) -> dict[str, Any]:
    adv = active_advisory(data, code)
    if adv is None:
        return {
            "active_advisory_id": None,
            "candidates": [],
            "reason": (
                "Old advisory is withdrawn and no ACTIVE corrected successor exists; "
                "issue a withdrawal cancellation/maintenance receipt, not a new "
                "advisory rectification closure."
            ),
        }

    candidates = []
    for ticket in data["tickets"]:
        if ticket["advisory_code"] != code:
            continue
        assessment = assess_ticket(data, ticket)
        effective_final = any(
            c["ticket_id"] == ticket["id"]
            and c["kind"] == ClosureKind.FINAL.value
            and verify_closure(data, c["id"])["effective_under_current_bulletin"]
            for c in data["closures"]
        )
        if not effective_final and assessment["can_close_final"]:
            candidates.append(
                {
                    "ticket_id": ticket["id"],
                    "machine_no": ticket["machine_no"],
                    "routes": assessment["eligible_final_routes"],
                    "evidence": {
                        route: assessment["routes"][route]["evidence_ids"]
                        for route in assessment["eligible_final_routes"]
                    },
                }
            )
    return {"active_advisory_id": adv["id"], "candidates": candidates, "reason": ""}
