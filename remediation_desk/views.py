"""Read models for ticket lookup and the engineer workboard."""
from __future__ import annotations

from typing import Any

from .rules import assess_ticket, closure_candidates, verify_closure


def machine_dossier(data: dict[str, Any], machine_no: str) -> dict[str, Any]:
    machine = next((m for m in data["machines"] if m["machine_no"] == machine_no), None)
    if machine is None:
        raise KeyError(machine_no)

    reports = [r for r in data["inventory_reports"] if r["machine_no"] == machine_no]
    latest = max(reports, key=lambda r: r["seq"]) if reports else None
    tickets = [t for t in data["tickets"] if t["machine_no"] == machine_no]
    assessments = [assess_ticket(data, t) for t in tickets]
    closures = [c for c in data["closures"] if c["machine_no"] == machine_no]
    closure_checks = [verify_closure(data, c["id"]) for c in closures]
    resources = []
    spare_no = machine.get("service", {}).get("spare_no")
    if spare_no:
        resources = [e for e in data["resource_events"] if e["resource_no"] == spare_no]

    return {
        "machine": machine,
        "latest_inventory": latest,
        "report_ids": [r["id"] for r in reports],
        "tickets": tickets,
        "assessments": assessments,
        "closures": closures,
        "closure_checks": closure_checks,
        "resource_events": resources,
    }


def engineer_board(data: dict[str, Any], advisory_code: str | None = None) -> list[dict[str, Any]]:
    board = []
    for machine in data["machines"]:
        no = machine["machine_no"]
        tickets = [
            t
            for t in data["tickets"]
            if t["machine_no"] == no
            and (advisory_code is None or t["advisory_code"] == advisory_code)
        ]
        rows = []
        for ticket in tickets:
            assessment = assess_ticket(data, ticket)
            open_items = [i for i in assessment["items"] if i["is_open"]]
            next_evidence = []
            for route in ("REPLACED_NEW_PROCESS", "FUNCTIONS_DISABLED"):
                next_evidence.extend(assessment["routes"][route]["missing"])
            rows.append(
                {
                    "ticket_id": ticket["id"],
                    "status": ticket["status"],
                    "order_kind": ticket.get("order_kind"),
                    "promise": ticket.get("promise"),
                    "open_count": assessment["open_item_count"],
                    "items": assessment["items"],
                    "open_items": open_items,
                    "final_routes_available": assessment["eligible_final_routes"],
                    "next_evidence": list(dict.fromkeys(next_evidence)),
                }
            )
        board.append({"machine_no": no, "label": machine.get("label", ""), "tickets": rows})
    return board


def answer_payload(data: dict[str, Any], code: str) -> dict[str, Any]:
    candidates = closure_candidates(data, code)
    effective_final = []
    invalidated_final = []
    temporary = []
    for closure in data["closures"]:
        check = verify_closure(data, closure["id"])
        row = {"closure": closure, "validity": check}
        if closure["kind"] == "FINAL":
            if check["effective_under_current_bulletin"]:
                effective_final.append(row)
            else:
                invalidated_final.append(row)
        elif closure["kind"] == "TEMPORARY_CONTROL":
            temporary.append(row)

    qualifying_final = []
    for ticket in data["tickets"]:
        if ticket["advisory_code"] != code:
            continue
        assessment = assess_ticket(data, ticket)
        if not assessment["can_close_final"]:
            continue
        existing = next(
            (
                row["closure"]["id"]
                for row in effective_final
                if row["closure"]["ticket_id"] == ticket["id"]
            ),
            None,
        )
        qualifying_final.append(
            {
                "machine_no": ticket["machine_no"],
                "ticket_id": ticket["id"],
                "routes": assessment["eligible_final_routes"],
                "effective_closure_id": existing,
                "receivable": existing is None,
            }
        )

    return {
        "advisory_code": code,
        "active_advisory_id": candidates["active_advisory_id"],
        "new_closure_candidates": candidates,
        "final_qualifying_machines": qualifying_final,
        "effective_final_closures": effective_final,
        "invalidated_final_closures": invalidated_final,
        "temporary_closures": temporary,
        "plans": data.get("plans", []),
    }


def format_dossier(dossier: dict[str, Any]) -> str:
    m = dossier["machine"]
    lines = [f"== {m['machine_no']} {m.get('label', '')} =="]
    latest = dossier["latest_inventory"]
    if latest:
        lines.append(
            f"whole image: {latest.get('whole_model', '')} {latest.get('whole_version', '')} "
            f"[report {latest['id']} @ {latest.get('captured_at', '')}]"
        )
    else:
        lines.append("whole image: no inventory report")

    for assessment in dossier["assessments"]:
        lines.append(
            f"-- ticket {assessment['ticket_id']} / {assessment['active_advisory_id']} "
            f"open={assessment['open_item_count']} final-routes={assessment['eligible_final_routes']}"
        )
        for item in assessment["items"]:
            flag = "OPEN   " if item["is_open"] else "CLEARED"
            lines.append(
                f"  [{flag}] {item['key']} {item.get('current_state', '')}"
            )
            if item["is_open"]:
                lines.append(f"          evidence needed: {item['clearing_evidence_hint']}")
        for route, result in assessment["routes"].items():
            if result["eligible"]:
                lines.append(f"  route ready: {route} evidence={','.join(result['evidence_ids'])}")
            elif result["missing"]:
                lines.append(f"  route blocked: {route}")
                for missing in result["missing"]:
                    lines.append(f"      - {missing}")

    for closure, check in zip(dossier["closures"], dossier["closure_checks"]):
        state = "EFFECTIVE" if check["effective_under_current_bulletin"] else "NOT-EFFECTIVE-NOW"
        lines.append(
            f"  closure {closure['id']} {closure['kind']} {closure['route']} -> {state}"
        )
        lines.append(f"      scope: {check['scope_explanation']}")
    return "\n".join(lines)
