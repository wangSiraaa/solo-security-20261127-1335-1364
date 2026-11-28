"""Command-line interface for the offline calibration-board tool.

Subcommands
-----------
``plan``      synthesize a fixed-tick Mode A control sequence and prove it
``prove``     test a specific fixed tick against every reset interleaving
``handshake`` report Mode B cross-domain handshake feasibility
``run``       execute one concrete run and dump the per-window read records
``examples``  list bundled example boards / programs

All commands read JSON and write JSON unless ``--format text`` is given.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .events import Event, ReadRequest
from .handshake import handshake_windows
from .model import Board, SpecError
from .planner import (
    ResetSpec,
    build_run,
    handshake_plan,
    prove_fixed_tick,
    synthesize_plan,
)
from .sim import Simulator


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _load_json(path: str) -> Any:
    if path == "-":
        return json.load(sys.stdin)
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _emit(doc: Any, fmt: str) -> None:
    if fmt == "json":
        json.dump(doc, sys.stdout, indent=2, sort_keys=False, default=str)
        sys.stdout.write("\n")
    else:
        _emit_text(doc)


def _emit_text(doc: Any) -> None:
    def walk(obj: Any, indent: int = 0) -> None:
        pad = "  " * indent
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, (dict, list)):
                    sys.stdout.write(f"{pad}{k}:\n")
                    walk(v, indent + 1)
                else:
                    sys.stdout.write(f"{pad}{k}: {v}\n")
        elif isinstance(obj, list):
            for item in obj:
                if isinstance(item, (dict, list)):
                    sys.stdout.write(f"{pad}-\n")
                    walk(item, indent + 1)
                else:
                    sys.stdout.write(f"{pad}- {item}\n")
        else:
            sys.stdout.write(f"{pad}{obj}\n")

    walk(doc)


def _board_from_args(args: argparse.Namespace) -> Board:
    doc = _load_json(args.board)
    if "board" in doc and isinstance(doc["board"], dict):
        doc = doc["board"]
    return Board.from_dict(doc)


def _requests_from_args(args: argparse.Namespace, doc: dict | None) -> list[ReadRequest]:
    reqs: list[ReadRequest] = []
    if doc and "windows" in doc:
        for w in doc["windows"]:
            reqs.append(ReadRequest.from_dict(w))
    for w in getattr(args, "window", []) or []:
        reqs.append(ReadRequest.from_dict(json.loads(w)))
    return reqs


# ---------------------------------------------------------------------------
# subcommands
# ---------------------------------------------------------------------------
def _apply_mutations(board: Board, mutations: list[dict]) -> Board:
    """Apply cell clear-scope / retention mutations to a cloned board."""
    b = board.clone()
    for m in mutations:
        cell = m.get("cell")
        if not cell:
            raise SpecError("mutation missing 'cell'")
        if "clear_with" in m:
            b = b.with_cell_clear_scope(cell, m["clear_with"] or [])
        elif "clear_scope" in m:
            b = b.with_cell_clear_scope(cell, m["clear_scope"] or [])
        elif "retained" in m:
            scope = [] if m["retained"] else [b.cells[cell].domain]
            b = b.with_cell_clear_scope(cell, scope)
        else:
            raise SpecError(f"mutation for {cell!r} changes nothing")
    return b


def cmd_plan(args: argparse.Namespace) -> int:
    doc = _load_json(args.program) if args.program else {}
    board = _board_from_args(args)
    board = _apply_mutations(board, doc.get("mutations") or [])
    reset_kind = args.reset or (doc.get("reset_kind") if doc else None)
    if not reset_kind:
        raise SpecError("plan needs --reset or a reset_kind in the program file")
    round_gen = args.round or doc.get("round_gen") or doc.get("round")
    if not round_gen:
        raise SpecError("plan needs --round or a round_gen in the program file")
    want = args.want or doc.get("want") or []
    writes = doc.get("writes") or {}
    plan = synthesize_plan(board, reset_kind, round_gen, writes, list(want))
    _emit(plan.to_dict(), args.format)
    return 0 if plan.ok else 2


def cmd_prove(args: argparse.Namespace) -> int:
    doc = _load_json(args.program) if args.program else {}
    board = _board_from_args(args)
    reset_kind = args.reset or doc.get("reset_kind")
    if not reset_kind:
        raise SpecError("prove needs --reset or a reset_kind in the program file")
    round_gen = args.round or doc.get("round_gen") or doc.get("round")
    if not round_gen:
        raise SpecError("prove needs --round or a round_gen in the program file")
    want = args.want or doc.get("want") or []
    writes = doc.get("writes") or {}
    if args.tick is None:
        raise SpecError("prove needs --tick")
    out = prove_fixed_tick(board, reset_kind, round_gen, writes, list(want), args.tick)
    _emit(out, args.format)
    return 0 if out["safe_in_all"] else 2


def cmd_handshake(args: argparse.Namespace) -> int:
    board = _board_from_args(args)
    want = args.want or []
    if not want:
        raise SpecError("handshake needs --want")
    out = handshake_plan(board, list(want))
    _emit(out, args.format)
    return 0 if out["ok"] else 2


def cmd_run(args: argparse.Namespace) -> int:
    doc = _load_json(args.run)
    board = _board_from_args(args)
    events = [Event.from_dict(e) for e in doc.get("events") or []]
    reqs = _requests_from_args(args, doc)
    if not reqs:
        raise SpecError("run file lists no windows")
    sim = Simulator(board, events, reqs)
    result = sim.run()
    out: dict[str, Any] = result.to_dict()
    hs = handshake_windows(board, events, reqs)
    if hs:
        out["handshakes"] = [h.to_dict() for h in hs]
    _emit(out, args.format)
    # exit 2 if any window leaked raw, else 0
    return 2 if result.leaks else 0


def cmd_scenario(args: argparse.Namespace) -> int:
    """Run a chosen normal reset order with fresh measurements.

    This is the reviewer's flow: pick a permitted reset order, drive the board
    with new values, and record what each maintenance port genuinely read.
    """
    doc = _load_json(args.run)
    board = _board_from_args(args)
    board = _apply_mutations(board, doc.get("mutations") or [])
    reset_kind = doc.get("reset_kind") or args.reset
    if not reset_kind:
        raise SpecError("scenario needs reset_kind in the run file or --reset")
    starts = doc.get("starts") or {}
    round_gen = doc.get("round_gen") or args.round
    if not round_gen:
        raise SpecError("scenario needs round_gen")
    writes = doc.get("writes") or {}
    write_tick = int(doc.get("write_tick", 0))
    reqs = _requests_from_args(args, doc)
    if not reqs:
        raise SpecError("scenario run file lists no windows")
    reset = ResetSpec.from_board(board, reset_kind) if isinstance(reset_kind, str) else reset_kind
    events, reqs = build_run(board, reset, starts or None, round_gen, writes, write_tick, reqs)
    result = Simulator(board, events, reqs).run()
    out: dict[str, Any] = {
        "board": board.name,
        "reset_kind": reset_kind,
        "round_gen": round_gen,
        "starts": dict(starts),
        "events": [
            {"at": e.at, "kind": e.kind, "domains": e.domains, "cell": e.cell,
             "value": e.value, "gen": e.gen}
            for e in events
        ],
        **result.to_dict(),
    }
    hs = handshake_windows(board, events, reqs)
    if hs:
        out["handshakes"] = [h.to_dict() for h in hs]
    _emit(out, args.format)
    return 2 if result.leaks else 0


def cmd_examples(args: argparse.Namespace) -> int:
    from .examples import EXAMPLES

    doc = {
        "examples": [
            {"name": name, "description": desc} for name, (_b, desc) in EXAMPLES.items()
        ]
    }
    _emit(doc, args.format)
    return 0


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="calboard",
        description="Offline calibration-board startup inspection & deployment tool",
    )
    p.add_argument("--format", choices=("json", "text"), default="json")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--board", required=True, help="board description JSON")
        # SUPPRESS so an unset subcommand flag does not clobber the global
        # --format that appeared before the subcommand.
        sp.add_argument("--format", choices=("json", "text"), default=argparse.SUPPRESS)

    sp = sub.add_parser("plan", help="synthesize a fixed-tick control plan")
    add_common(sp)
    sp.add_argument("--program", help="program JSON (reset_kind, round_gen, want, writes)")
    sp.add_argument("--reset", help="reset kind override")
    sp.add_argument("--round", help="round generation override")
    sp.add_argument("--want", nargs="+", help="outputs to publish")
    sp.set_defaults(func=cmd_plan)

    sp = sub.add_parser("prove", help="prove a fixed window tick")
    add_common(sp)
    sp.add_argument("--program", help="program JSON")
    sp.add_argument("--reset", help="reset kind override")
    sp.add_argument("--round", help="round generation override")
    sp.add_argument("--want", nargs="+", help="outputs to publish")
    sp.add_argument("--tick", type=int, required=True, help="fixed window tick")
    sp.set_defaults(func=cmd_prove)

    sp = sub.add_parser("handshake", help="Mode B feasibility")
    add_common(sp)
    sp.add_argument("--want", nargs="+", required=True)
    sp.set_defaults(func=cmd_handshake)

    sp = sub.add_parser("run", help="run one concrete scenario")
    add_common(sp)
    sp.add_argument("--run", required=True, help="run JSON (events, windows)")
    sp.add_argument("--window", action="append", help="extra window JSON (repeatable)")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("scenario", help="run a chosen normal reset order with fresh values")
    add_common(sp)
    sp.add_argument("--run", required=True, help="scenario JSON (reset_kind, starts, writes, windows)")
    sp.add_argument("--reset", help="reset kind override")
    sp.add_argument("--round", help="round generation override")
    sp.add_argument("--window", action="append", help="extra window JSON (repeatable)")
    sp.set_defaults(func=cmd_scenario)

    sp = sub.add_parser("examples", help="list bundled examples")
    sp.add_argument("--format", choices=("json", "text"), default="text")
    sp.set_defaults(func=cmd_examples)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # global --format default is 'json'; subcommand --format (when given)
    # overrides it because it is parsed later into the same attribute.
    if not hasattr(args, "format") or args.format is None:
        args.format = "json"
    try:
        return args.func(args)
    except SpecError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
