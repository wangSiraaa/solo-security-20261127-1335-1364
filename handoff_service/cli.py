#!/usr/bin/env python3
"""Command line interface for the peripheral hand-off service.

Examples:
  handoff seed
  handoff call '{"op":"input","interface":"J2","capability":"read_sample",...}'
  handoff call --op input --interface J2 --value S-42 --order WO-V
  handoff serve --host 127.0.0.1 --port 8765
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from .core import HandOffError, handle
from .store import DEFAULT_STATE_PATH, locked_state
from .server import serve as run_server


def _parse_scalar(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _parse_set_values(pairs: list[str] | None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise SystemExit(f"--set expects key=value, got: {pair}")
        key, raw = pair.split("=", 1)
        result[key.replace("-", "_")] = _parse_scalar(raw)
    return result


def _parse_unknown_flags(tokens: list[str]) -> dict[str, Any]:
    """Accept friendly flags such as --interface J2 in addition to JSON/--set."""
    result: dict[str, Any] = {}
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if not token.startswith("--"):
            i += 1
            continue
        body = token[2:]
        if "=" in body:
            key, raw = body.split("=", 1)
            result[key.replace("-", "_")] = _parse_scalar(raw)
            i += 1
            continue
        key = body.replace("-", "_")
        if i + 1 < len(tokens) and not tokens[i + 1].startswith("--"):
            result[key] = _parse_scalar(tokens[i + 1])
            i += 2
        else:
            result[key] = True
            i += 1
    return result


def _print_json(value: Any, compact: bool = False) -> None:
    if compact:
        print(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    else:
        print(json.dumps(value, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--state", default=argparse.SUPPRESS, help="JSON state file")
    common.add_argument("--compact", action="store_true", default=argparse.SUPPRESS, help="compact JSON output")

    parser = argparse.ArgumentParser(prog="handoff", description="Repair-bench peripheral hand-off service")
    parser.add_argument("--state", default=DEFAULT_STATE_PATH, help="JSON state file")
    parser.add_argument("--compact", action="store_true", default=False, help="compact JSON output")
    sub = parser.add_subparsers(dest="command", required=True)

    call = sub.add_parser("call", parents=[common], help="dispatch one JSON command")
    call.add_argument("payload", nargs="?", help="JSON command payload")
    call.add_argument("--op", help="operation name when not using a JSON payload")
    call.add_argument("--set", dest="sets", action="append", help="key=value; value is parsed as JSON when possible")
    call.add_argument("--stdin", action="store_true", help="read JSON payload from stdin")
    for flag, dest in [
        ("--id", "id"), ("--interface", "interface"), ("--capability", "capability"),
        ("--value", "value"), ("--order", "order"), ("--fixture", "fixture"),
        ("--component", "component"), ("--circuit", "circuit"), ("--path", "path"),
        ("--at", "at"), ("--reported-serial", "reported_serial"),
        ("--quarantine", "quarantine"), ("--attestation", "attestation"),
        ("--worker", "worker"), ("--reason", "reason"), ("--scope", "scope"),
        ("--pattern", "pattern"), ("--board", "board"), ("--buffer", "buffer"),
        ("--handoff", "handoff"), ("--from-path", "from_path"), ("--note", "note"),
        ("--effective-seq", "effective_seq"),
    ]:
        call.add_argument(flag, dest=dest)
    for flag, dest in [
        ("--entrusted", "entrusted"), ("--locked", "locked"),
        ("--rereadable", "rereadable"), ("--accept-loss", "accept_loss"),
        ("--force", "force"), ("--replace", "replace"), ("--fail", "fail"),
    ]:
        call.add_argument(flag, dest=dest, action="store_true", default=None)

    sub.add_parser("seed", parents=[common], help="reset and seed the three-block/two-pattern bench")
    sub.add_parser("show", parents=[common], help="show full persisted state")

    order = sub.add_parser("order", parents=[common], help="show one work order and active formal entries")
    order.add_argument("order_id")

    q = sub.add_parser("quarantine", parents=[common], help="list unclaimed quarantine items")
    q.add_argument("--all", action="store_true", help="include resolved items")

    serve = sub.add_parser("serve", parents=[common], help="run local HTTP JSON service")
    serve.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    serve.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8765")))

    sub.add_parser("demo", parents=[common], help="run the built-in acceptance scenario and assertions")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args, unknown = parser.parse_known_args(argv)
    extra = _parse_unknown_flags(unknown)
    if "state" in extra:
        args.state = extra.pop("state")
    if "compact" in extra:
        args.compact = extra.pop("compact")
    args.extra = extra

    if args.command == "serve":
        run_server(args.host, args.port, args.state)
        return 0

    if args.command == "demo":
        from .scenario import run_demo

        result = run_demo()
        _print_json(result, args.compact)
        return 0 if result.get("all_assertions_passed") else 1

    if args.command == "seed":
        command = {"op": "reset", "seed": True}
    elif args.command == "show":
        command = {"op": "show"}
    elif args.command == "order":
        command = {"op": "show"}
    elif args.command == "quarantine":
        command = {"op": "show"}
    else:
        command = {}
        if args.stdin:
            command = json.load(sys.stdin)
        elif args.payload:
            command = json.loads(args.payload)
        if args.op:
            command["op"] = args.op
        internal = {
            "state", "compact", "command", "payload", "sets", "stdin", "op", "extra"
        }
        for key, value in vars(args).items():
            if key not in internal and value is not None:
                command[key] = value
        command.update(_parse_set_values(args.sets))
        command.update(getattr(args, "extra", {}))
        if command.pop("compact", None):
            args.compact = True
        if "op" not in command:
            parser.error("call requires a JSON payload or --op")

    try:
        with locked_state(args.state) as state:
            result = handle(state, command)

            if args.command == "order":
                order = result["state"]["orders"].get(args.order_id)
                if not order:
                    _print_json({"ok": False, "error": "unknown_order", "order_id": args.order_id}, args.compact)
                    return 2
                active = [e for e in order.get("entries", []) if e.get("status") != "reversed"]
                result = {"order": order, "active_entry_refs": active, "active_count": len(active)}
            elif args.command == "quarantine":
                items = result["state"]["quarantine"]
                if not args.all:
                    items = [q for q in items if q.get("status") == "unclaimed"]
                result = {"count": len(items), "items": items}
    except HandOffError as exc:
        body = {"ok": False, "error": exc.code, "message": exc.message, "details": exc.details}
        _print_json(body, args.compact)
        return 4 if exc.code == "confirmation_required" else 2
    except (json.JSONDecodeError, KeyError) as exc:
        _print_json({"ok": False, "error": "bad_request", "message": str(exc)}, args.compact)
        return 2

    _print_json(result, args.compact)
    if isinstance(result, dict) and result.get("ok") is False:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
