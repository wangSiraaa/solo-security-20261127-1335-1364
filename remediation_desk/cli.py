"""Command line interface for the offline remediation desk."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .desk import Desk
from .models import ClosureKind
from .rules import issue_closure, refresh_ticket_status, verify_closure
from .seed import DEFAULT_PATH, build_seed
from .views import answer_payload, engineer_board, format_dossier, machine_dossier


def _desk(args: argparse.Namespace) -> Desk:
    return Desk(args.data)


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _load_json_file(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def cmd_seed(args: argparse.Namespace) -> int:
    desk = build_seed(args.data)
    print(f"seeded fictional three-terminal desk at {desk.path}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    desk = _desk(args)
    dossier = machine_dossier(desk.data, args.machine_no)
    if args.json:
        _print_json(dossier)
    else:
        print(format_dossier(dossier))
    return 0


def cmd_board(args: argparse.Namespace) -> int:
    desk = _desk(args)
    board = engineer_board(desk.data, args.code)
    if args.json:
        _print_json(board)
        return 0
    for machine in board:
        print(f"== {machine['machine_no']} {machine['label']} ==")
        for ticket in machine["tickets"]:
            print(
                f"  {ticket['ticket_id']} [{ticket['status']}] "
                f"open={ticket['open_count']} ready={ticket['final_routes_available']}"
            )
            for item in ticket.get("items", ticket["open_items"]):
                flag = "OPEN   " if item["is_open"] else "CLEARED"
                print(f"    [{flag}] {item['key']}: {item['current_state']}")
                if item["is_open"]:
                    print(f"      next evidence: {item['clearing_evidence_hint']}")
            for evidence in ticket["next_evidence"]:
                print(f"      missing route evidence: {evidence}")
    return 0


def cmd_answer(args: argparse.Namespace) -> int:
    desk = _desk(args)
    payload = answer_payload(desk.data, args.code)
    if args.json:
        _print_json(payload)
        return 0

    active = payload["active_advisory_id"]
    print(f"active corrected bulletin: {active or 'NONE (old bulletin withdrawn only)'}")
    if not active:
        print(payload["new_closure_candidates"]["reason"])
    print("\nEffective final closures under current evidence:")
    for row in payload["effective_final_closures"]:
        c = row["closure"]
        print(f"  - {c['machine_no']} {c['id']} via {c['route']}")
        print(f"    {row['validity']['scope_explanation']}")
    print("\nFinal closures not currently effective under an active bulletin:")
    for row in payload["invalidated_final_closures"]:
        c = row["closure"]
        print(f"  - {c['machine_no']} {c['id']} remains an immutable paper record, but is NOT effective")
        for missing in row["validity"]["current_missing"]:
            print(f"      * {missing}")
    print("\nScoped temporary closures:")
    for row in payload["temporary_closures"]:
        c = row["closure"]
        v = row["validity"]
        state = "holds" if v["effective_under_current_bulletin"] else "does not hold/expired"
        print(f"  - {c['machine_no']} {c['id']} {state} through {c.get('valid_through_shift')}")
        print(f"    {v['scope_explanation']}")
    print("\nFinal closure qualification:")
    for row in payload.get("final_qualifying_machines", []):
        state = (
            f"already has effective {row['effective_closure_id']}"
            if row["effective_closure_id"]
            else "can receive a new closure"
        )
        print(f"  - {row['machine_no']}: {state}; routes={','.join(row['routes'])}")

    print("\nNew final-closure candidates:")
    candidates = payload["new_closure_candidates"].get("candidates", [])
    if not candidates:
        print("  (none)")
    for candidate in candidates:
        print(f"  - {candidate['machine_no']} {candidate['ticket_id']}: {candidate['routes']}")
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    desk = _desk(args)
    plans = desk.data.get("plans", [])
    if args.json:
        _print_json(plans[-1] if plans else {})
        return 0
    if not plans:
        print("No plan recorded.")
        return 1
    plan = plans[-1]
    print(f"window -> {plan['window_machine']}")
    print(f"control first -> {plan['control_first_machine']}")
    for order in plan["control_order"]:
        print(f"- {order['machine_no']}: {order['action']}")
        for key in ("why", "tonight_completable", "carried_unfinished", "do_not_promise", "promise", "control_through_shift"):
            if key in order and order[key] not in (None, ""):
                print(f"    {key}: {order[key]}")
    print(plan["staffing_report_policy"])
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    desk = _desk(args)
    result = verify_closure(desk.data, args.closure_id)
    if args.json:
        _print_json(result)
    else:
        for key, value in result.items():
            print(f"{key}: {value}")
    return 0 if result["record_valid"] else 2


def cmd_inventory(args: argparse.Namespace) -> int:
    desk = _desk(args)
    payload = _load_json_file(args.file)
    components = payload.get("components", payload) if isinstance(payload, dict) else payload
    row = desk.submit_inventory(
        args.machine_no,
        components,
        report_id=args.report_id,
        captured_at=args.at,
        reporter=args.reporter,
        whole_model=payload.get("whole_model", "") if isinstance(payload, dict) else "",
        whole_version=payload.get("whole_version", "") if isinstance(payload, dict) else "",
        raw_note=payload.get("raw_note", "") if isinstance(payload, dict) else "",
    )
    for ticket in desk.data["tickets"]:
        if ticket["machine_no"] == args.machine_no:
            refresh_ticket_status(desk.data, ticket)
    desk.save()
    print(row["id"])
    return 0


def cmd_receipt(args: argparse.Namespace) -> int:
    desk = _desk(args)
    uids = args.uid or []
    row = desk.add_repair_receipt(
        args.machine_no,
        receipt_id=args.receipt_id,
        completed_at=args.at,
        part_no=args.part,
        from_version=args.from_version,
        to_version=args.to_version,
        covered_uids=uids,
        paper_ref=args.paper_ref,
        note=args.note,
    )
    desk.save()
    print(row["id"])
    return 0


def cmd_disable(args: argparse.Namespace) -> int:
    desk = _desk(args)
    row = desk.add_disablement_receipt(
        args.machine_no,
        receipt_id=args.receipt_id,
        issued_at=args.at,
        covered_uids=args.uid or [],
        covered_functions=args.function or [],
        durable=not args.temporary,
        temporary=args.temporary,
        old_processes_stopped=not args.processes_not_stopped,
        valid_through_shift=args.until,
        paper_ref=args.paper_ref,
        note=args.note,
    )
    desk.save()
    print(row["id"])
    return 0


def cmd_staffing(args: argparse.Namespace) -> int:
    desk = _desk(args)
    row = desk.add_staffing_report(
        args.desk,
        args.shift,
        args.available,
        source=args.source,
        reliable=not args.unreliable,
        report_id=args.report_id,
        note=args.note,
    )
    desk.save()
    print(row["id"])
    return 0


def cmd_resource(args: argparse.Namespace) -> int:
    desk = _desk(args)
    row = desk.add_resource_event(
        args.resource_no,
        event=args.event,
        machine_no=args.machine_no,
        available=args.available,
        linked_machine=args.linked_machine,
        event_id=args.event_id,
        at=args.at,
        note=args.note,
    )
    desk.save()
    print(row["id"])
    return 0


def cmd_close(args: argparse.Namespace) -> int:
    desk = _desk(args)
    kind = {
        "final": ClosureKind.FINAL.value,
        "temporary": ClosureKind.TEMPORARY_CONTROL.value,
        "withdrawn": ClosureKind.WITHDRAWN_BULLETIN.value,
    }[args.kind]
    scope = _load_json_file(args.scope_file) if args.scope_file else {}
    try:
        closure = issue_closure(
            desk.data,
            args.machine_no,
            closure_id=args.closure_id,
            kind=kind,
            route=args.route,
            at=args.at,
            statement=args.statement,
            scope=scope,
        )
    except ValueError as exc:
        print(f"cannot close: {exc}", file=sys.stderr)
        return 2
    desk.save()
    print(closure["id"])
    return 0


def cmd_withdraw(args: argparse.Namespace) -> int:
    desk = _desk(args)
    adv = desk.withdraw_advisory(args.advisory_id, args.reason, at=args.at)
    desk.save()
    print(f"{adv['id']} {adv['status']}")
    return 0


def cmd_correct_advisory(args: argparse.Namespace) -> int:
    desk = _desk(args)
    payload = _load_json_file(args.file)
    old_id = payload["old_id"]
    reason = payload.get("reason", "supplier correction")
    at = payload.get("at", args.at)
    advisory_payload = dict(payload["advisory"])
    new_adv = desk.add_advisory(**advisory_payload)
    desk.replace_advisory(
        old_id,
        new_adv,
        reason=reason,
        at=at,
        withdrawn=bool(payload.get("withdrawn", False)),
    )
    desk.save()
    print(f"{old_id} -> {new_adv['id']}")
    return 0


def cmd_advance(args: argparse.Namespace) -> int:
    desk = _desk(args)
    desk.data["current_shift"] = args.shift
    order = desk.data.setdefault("shift_order", [])
    if args.shift not in order:
        order.append(args.shift)
    desk.save()
    print(args.shift)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="desk", description="Offline fictional remediation desk")
    parser.add_argument("--data", default=str(DEFAULT_PATH), help="JSON data file")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("seed", help="recreate canonical three-terminal data")
    p.set_defaults(func=cmd_seed)

    p = sub.add_parser("show", aliases=["ticket"], help="show a machine ticket/dossier")
    p.add_argument("machine_no")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("board", help="engineer workboard")
    p.add_argument("--code", default="FIC-BARCODE-DCP710")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_board)

    p = sub.add_parser("answer", help="show which closure remains effective/receivable")
    p.add_argument("--code", default="FIC-BARCODE-DCP710")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_answer)

    p = sub.add_parser("plan", help="show tonight's window/control plan")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("verify", help="verify a closure id")
    p.add_argument("closure_id")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("inventory", help="submit a full inventory JSON file")
    p.add_argument("machine_no")
    p.add_argument("--file", required=True)
    p.add_argument("--report-id")
    p.add_argument("--at", default="")
    p.add_argument("--reporter", default="cli")
    p.set_defaults(func=cmd_inventory)

    p = sub.add_parser("receipt", help="file a repair/replacement receipt")
    p.add_argument("machine_no")
    p.add_argument("--receipt-id")
    p.add_argument("--at", default="")
    p.add_argument("--part", default="DCP-710")
    p.add_argument("--from-version", default="")
    p.add_argument("--to-version", default="")
    p.add_argument("--uid", action="append")
    p.add_argument("--paper-ref", default="")
    p.add_argument("--note", default="")
    p.set_defaults(func=cmd_receipt)

    p = sub.add_parser("disable", help="file a disablement receipt")
    p.add_argument("machine_no")
    p.add_argument("--receipt-id")
    p.add_argument("--at", default="")
    p.add_argument("--uid", action="append")
    p.add_argument("--function", action="append")
    p.add_argument("--temporary", action="store_true")
    p.add_argument("--until")
    p.add_argument("--processes-not-stopped", action="store_true")
    p.add_argument("--paper-ref", default="")
    p.add_argument("--note", default="")
    p.set_defaults(func=cmd_disable)

    p = sub.add_parser("staffing", help="append a staffing report")
    p.add_argument("--desk", required=True)
    p.add_argument("--shift", required=True)
    available = p.add_mutually_exclusive_group(required=True)
    available.add_argument("--available", dest="available", action="store_true")
    available.add_argument("--unavailable", dest="available", action="store_false")
    p.set_defaults(available=False)
    p.add_argument("--source", default="cli")
    p.add_argument("--unreliable", action="store_true")
    p.add_argument("--report-id")
    p.add_argument("--note", default="")
    p.set_defaults(func=cmd_staffing)

    p = sub.add_parser("resource", help="append a spare/resource event")
    p.add_argument("resource_no")
    p.add_argument("--event", required=True)
    p.add_argument("--machine-no")
    p.add_argument("--linked-machine")
    p.add_argument("--available", type=lambda x: x.lower() == "true")
    p.add_argument("--event-id")
    p.add_argument("--at", default="")
    p.add_argument("--note", default="")
    p.set_defaults(func=cmd_resource)

    p = sub.add_parser("close", help="issue a closure if evidence is sufficient")
    p.add_argument("machine_no")
    p.add_argument("--kind", choices=["final", "temporary", "withdrawn"], default="final")
    p.add_argument("--route")
    p.add_argument("--closure-id")
    p.add_argument("--at", default="")
    p.add_argument("--statement", default="")
    p.add_argument("--scope-file")
    p.set_defaults(func=cmd_close)

    p = sub.add_parser("withdraw", help="withdraw an advisory")
    p.add_argument("advisory_id")
    p.add_argument("--reason", required=True)
    p.add_argument("--at", default="")
    p.set_defaults(func=cmd_withdraw)

    p = sub.add_parser("correct-advisory", help="withdraw an old bulletin and append its corrected successor")
    p.add_argument("--file", required=True)
    p.add_argument("--at", default="")
    p.set_defaults(func=cmd_correct_advisory)

    p = sub.add_parser("advance", help="set current shift")
    p.add_argument("shift")
    p.set_defaults(func=cmd_advance)

    # Allow the convenient ``desk show M --data file`` ordering as well.
    seen_parsers: set[int] = set()
    for subparser in sub.choices.values():
        if id(subparser) in seen_parsers:
            continue
        seen_parsers.add(id(subparser))
        subparser.add_argument(
            "--data",
            default=argparse.SUPPRESS,
            help="JSON data file (can also be given before the command)",
        )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
