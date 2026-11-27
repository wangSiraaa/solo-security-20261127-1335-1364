"""Command-line interface for the local collaborative editor."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .privacy import assert_clean, safe_json_dumps, scan_for_leaks
from .workspace import Workspace


def _print_json(value: Any) -> None:
    print(safe_json_dumps(value), end="")


def _anchors_from_args(values: Optional[Sequence[str]]) -> List[Tuple[str, str]]:
    anchors: List[Tuple[str, str]] = []
    for value in values or []:
        if "=" not in value:
            raise argparse.ArgumentTypeError("anchor must be ID=TEXT")
        anchor_id, text = value.split("=", 1)
        anchors.append((anchor_id, text))
    if not anchors:
        raise argparse.ArgumentTypeError("at least one --anchor is required")
    return anchors


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="collab-editor")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="initialize a workspace")
    p.add_argument("--root", required=True)
    p.add_argument("--role", choices=["museum", "coop"], default="museum")
    p.add_argument("--author", required=True)
    p.add_argument("--anchor", action="append", help="public anchor, e.g. A1=正文")
    p.add_argument("--force", action="store_true")

    p = sub.add_parser("deliver", help="deliver an autonomous public history to coop")
    p.add_argument("--from", dest="source", required=True)
    p.add_argument("--to", required=True)
    p.add_argument("--packet")

    p = sub.add_parser("text", help="print current public text")
    p.add_argument("--root", required=True)

    p = sub.add_parser("edit", help="make a public edit or a museum mixed edit")
    p.add_argument("--root", required=True)
    p.add_argument("--anchor", required=True)
    p.add_argument("--text", required=True, help="replacement text")
    p.add_argument("--old")
    p.add_argument("--start", type=int)
    p.add_argument("--end", type=int)
    p.add_argument("--occurrence", type=int, default=0)
    p.add_argument("--mixed-note", help="museum-only note to update in one mixed transaction")
    p.add_argument("--note-id", help="existing private annotation to revise in the mixed transaction")
    p.add_argument("--note-placement", choices=["before", "after"], default="before")

    p = sub.add_parser("note", help="add a pure museum-private annotation")
    p.add_argument("--root", required=True)
    p.add_argument("--anchor", required=True)
    p.add_argument("--text", required=True)
    p.add_argument("--placement", choices=["before", "after"], default="before")

    p = sub.add_parser("undo", help="undo own latest/specified operation")
    p.add_argument("--root", required=True)
    p.add_argument("--op")
    p.add_argument("--mixed", help="museum internal mixed op id; applies delivery policy")

    p = sub.add_parser("redo", help="redo own undone operation")
    p.add_argument("--root", required=True)

    p = sub.add_parser(
        "withdraw-delivered",
        help="coop branch appends a public withdrawal of a delivered change",
    )
    p.add_argument("--root", required=True)
    p.add_argument("--op", required=True)

    p = sub.add_parser("push", help="write a sanitized public packet")
    p.add_argument("--root", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--peer")

    p = sub.add_parser("merge", help="three-way merge a public packet")
    p.add_argument("--root", required=True)
    p.add_argument("--packet", required=True)
    p.add_argument("--answer-out")

    p = sub.add_parser("ack", help="acknowledge a merge answer and confirm common base")
    p.add_argument("--root", required=True)
    p.add_argument("--answer", required=True)

    p = sub.add_parser("resolve", help="resolve a public conflict sheet")
    p.add_argument("--root", required=True)
    p.add_argument("--conflict", required=True)
    p.add_argument("--choice", choices=["ours", "theirs", "text"], required=True)
    p.add_argument("--text")

    p = sub.add_parser("audit", help="scan counterpart-readable files for private markers")
    p.add_argument("--external", action="append", required=True)
    p.add_argument("--canary", action="append", default=[])

    p = sub.add_parser("demo", help="run the built-in end-to-end scenario")
    p.add_argument("--root", default="demo-run")

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "init":
        ws = Workspace.init(
            args.root,
            role=args.role,
            author=args.author,
            anchors=_anchors_from_args(args.anchor),
            force=args.force,
        )
        _print_json({"status": "initialized", "root": str(ws.root), "role": ws.role})
        return 0

    if args.command == "demo":
        from .demo import run_demo

        report = run_demo(Path(args.root))
        _print_json(report)
        return 0 if report.get("ok") else 1

    if args.command == "audit":
        findings = scan_for_leaks(args.external, args.canary)
        _print_json({"clean": not findings, "findings": findings})
        return 0 if not findings else 4

    if args.command == "deliver":
        source = Workspace(args.source)
        result = source.deliver_to(args.to, exchange_packet=args.packet)
        _print_json(
            {
                "status": "delivered",
                "to": args.to,
                "packet": result["packet"]["packet_id"] if result["packet"] else None,
                "policy": result["policy"],
            }
        )
        return 0

    ws = Workspace(args.root)

    if args.command == "text":
        sys.stdout.write(ws.public_text())
        return 0

    if args.command == "edit":
        kwargs = dict(
            start=args.start,
            end=args.end,
            old=args.old,
            occurrence=args.occurrence,
        )
        if args.mixed_note:
            result = ws.mixed_edit(
                args.anchor,
                args.text,
                args.mixed_note,
                placement=args.note_placement,
                note_id=args.note_id,
                **kwargs,
            )
            _print_json(result)
        else:
            op = ws.edit(args.anchor, args.text, **kwargs)
            _print_json(op)
        return 0

    if args.command == "note":
        note = ws.add_private_note(args.anchor, args.text, args.placement)
        _print_json(note)
        return 0

    if args.command == "undo":
        if args.mixed:
            result = ws.undo_mixed(args.mixed)
            _print_json(result)
            return 0
        op = ws.undo(args.op)
        _print_json(op)
        return 0

    if args.command == "redo":
        _print_json(ws.redo())
        return 0

    if args.command == "withdraw-delivered":
        _print_json(ws.withdraw_delivered(args.op))
        return 0

    if args.command == "push":
        packet = ws.push(args.out, peer=args.peer)
        _print_json(
            {"status": "pushed", "packet_id": packet["packet_id"], "out": args.out}
        )
        return 0

    if args.command == "merge":
        report = ws.merge_packet(args.packet, answer_out=args.answer_out)
        _print_json(report)
        return 3 if report.get("status") == "conflict" else 0

    if args.command == "ack":
        _print_json(ws.acknowledge_answer(args.answer))
        return 0

    if args.command == "resolve":
        op = ws.resolve_conflict(args.conflict, args.choice, text=args.text)
        _print_json(op)
        return 0

    parser.error(f"unhandled command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
