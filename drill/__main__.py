"""CLI entry points.

    python -m drill run        --root DIR [--mode online|graded] [--scenario std]
                              [--policy commit|cache] [--delay-index CLASS/ASSIGN]
    python -m drill recover    --root DIR --held HELDDIR [--out FILE]
    python -m drill topology   [--json]

``run`` is the server process.  It ends the simulated service at the kill
tick.  ``recover`` is a *separate* process that only reads the store tree,
held receipts and power.json -- never the config.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict

from .engine import Engine
from .recovery import Recovery, reopen
from .receipts import verify_receipt
from .scenario import build_shared_feed_topology, build_standard
from .storage import Store


def _split_pair(s: str) -> Dict[str, str]:
    cls, asg = s.split("/", 1)
    return {"classroom": cls, "assignment": asg}


def cmd_run(args: argparse.Namespace) -> int:
    cfg = build_standard()
    if args.policy:
        cfg.receipt_policy = args.policy
    if args.mode:
        cfg.mode = args.mode  # type: ignore[attr-defined]
    if args.delay_index:
        cfg.delay_index = _split_pair(args.delay_index)
    if args.flush_ticks is not None:
        cfg.flush_ticks = args.flush_ticks
    root = os.path.abspath(args.root)
    store = Store(root)
    eng = Engine(cfg, store, mode=args.mode or "online")
    eng.run()
    # teachers hold receipts requested at runtime; redeem map keyed by txid
    redeem = {r["payload"]["txid"]: r["payload"]["receipt_number"]
              for r in eng.receipts}
    meta = {
        "root": root,
        "held": os.path.join(os.path.dirname(root.rstrip("/")), "held"),
        "mode": args.mode or "online",
        "policy": cfg.receipt_policy,
        "receipts": [
            {"txid": r["payload"]["txid"], "status": r.get("status"),
             "policy": r["payload"]["policy"],
             "number": r["payload"]["receipt_number"]}
            for r in eng.receipts
        ],
        "aborted": eng.aborted,
        "battery_final": eng.telemetry[-1]["battery_after"] if eng.telemetry else None,
        "redeem": redeem,
    }
    print(json.dumps(meta, indent=2, ensure_ascii=False))
    return 0


def cmd_recover(args: argparse.Namespace) -> int:
    # Classroom keys are public material in the drill; a real deployment loads
    # them from a credential store.  Crucially we do NOT import the scenario /
    # engine here -- recovery depends only on disk artifacts + held receipts.
    from .credentials import classroom_key
    keys = {"C101": classroom_key("C101"), "C102": classroom_key("C102")}
    rec = Recovery(os.path.abspath(args.root), os.path.abspath(args.held), keys)
    summary = rec.recover()
    redeem: Dict[str, str] = {}
    for r in summary["recovered"]:
        if r.get("receipt_number"):
            redeem[r["txid"]] = r["receipt_number"]
    reopened = reopen(summary, redeem)
    out = {"summary": summary, "reopen": reopened}
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=2, ensure_ascii=False)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


def cmd_topology(args: argparse.Namespace) -> int:
    from .power import feasible, startup_conflicts
    base = build_standard().topology
    fault = build_shared_feed_topology()
    feeds_after = ["B"]
    result = {
        "base": {
            "startup_R2_R3_together_normal": feasible(
                base, ["A", "B"], 90.0, ["R2", "R3"]),
            "startup_R2_R3_together_after_loss": feasible(
                base, feeds_after, 90.0, ["R2", "R3"]),
            "conflicts_after_loss": startup_conflicts(
                base, feeds_after, 90.0, ["R2", "R3"]),
        },
        "fault_shared_feed": {
            "startup_R2_R3_together_normal": feasible(
                fault, ["A", "B"], 90.0, ["R2", "R3"]),
            "startup_R2_R3_together_after_loss": feasible(
                fault, feeds_after, 90.0, ["R2", "R3"]),
            "conflicts_after_loss": startup_conflicts(
                fault, feeds_after, 90.0, ["R2", "R3"]),
        },
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="drill")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run")
    r.add_argument("--root", required=True)
    r.add_argument("--mode", choices=["online", "graded"], default="online")
    r.add_argument("--policy", choices=["commit", "cache"], default=None)
    r.add_argument("--delay-index", default=None, help="CLASSROOM/ASSIGNMENT")
    r.add_argument("--flush-ticks", type=int, default=None,
                   help="磁盘落盘时间: ticks a write occupies a lane")
    r.set_defaults(func=cmd_run)

    c = sub.add_parser("recover")
    c.add_argument("--root", required=True)
    c.add_argument("--held", required=True)
    c.add_argument("--out", default=None)
    c.set_defaults(func=cmd_recover)

    t = sub.add_parser("topology")
    t.set_defaults(func=cmd_topology)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
