"""Command-line entry points for the curator and a demo reader.

    python -m hintdesk.cli admit   examples/lantern.json
    python -m hintdesk.cli menu    examples/lantern.json
    python -m hintdesk.cli round   examples/lantern.json --route booklet
    python -m hintdesk.cli verify  examples/lantern.json --choices "QX,QY"

Everything is offline and deterministic.
"""

from __future__ import annotations

import argparse
import json
import sys

from .content import Library
from .curator import Curator
from .engine import Engine
from .receipts import Receipt
from .verify import Verifier


def _load(path):
    return Library.from_file(path)


def cmd_admit(args):
    cur = Curator(_load(args.library))
    print(json.dumps(cur.admit().summary(), ensure_ascii=False, indent=2))


def cmd_menu(args):
    lib = _load(args.library)
    eng = Engine(lib)
    print(json.dumps(eng.menu(()), ensure_ascii=False, indent=2))


def cmd_round(args):
    lib = _load(args.library)
    cur = Curator(lib)
    out = cur.run_round(args.route)
    compact = {
        "route": out.route, "ticket": out.ticket, "ok": out.ok,
        "reason": out.reason, "detail": out.detail,
        "pickups": [
            {"wording": p.wording, "status": p.status, "useful": p.useful,
             "safe": p.safe, "eliminated": len(p.eliminated),
             "survivors": len(p.survivors)}
            for p in out.pickups
        ],
    }
    print(json.dumps(compact, ensure_ascii=False, indent=2))
    if not out.ok:
        sys.exit(1)


def cmd_verify(args):
    lib = _load(args.library)
    eng = Engine(lib)
    r = Receipt.build(ticket="demo", library=lib.digest,
                      puzzle_id=lib.puzzle_id, transcript=())
    for w in args.choices.split(","):
        w = w.strip()
        if not w:
            continue
        step = eng.decide(lib.truth, r.transcript, w)
        r = r.extend(step)
        print(f"asked {w!r} -> {step.status} {step.reason}")
    rep = Verifier(lib).replay(r)
    print(json.dumps(rep.summary(), ensure_ascii=False, indent=2))


def main(argv=None):
    p = argparse.ArgumentParser(prog="hintdesk")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("admit"); a.add_argument("library"); a.set_defaults(fn=cmd_admit)
    m = sub.add_parser("menu"); m.add_argument("library"); m.set_defaults(fn=cmd_menu)
    r = sub.add_parser("round")
    r.add_argument("library")
    r.add_argument("--route", choices=["booklet", "office"], default="booklet")
    r.set_defaults(fn=cmd_round)
    v = sub.add_parser("verify")
    v.add_argument("library")
    v.add_argument("--choices", required=True)
    v.set_defaults(fn=cmd_verify)

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
