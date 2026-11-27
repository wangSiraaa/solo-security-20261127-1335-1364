"""Small command-line entry points for the restartable local vault.

Everything is file-backed under ``--root``:

    python -m fair_exchange.cli init    --root W --parties jia,yi
    python -m fair_exchange.cli order    --root W --order o --a jia --b yi --retention 30 --tick 0
    python -m fair_exchange.cli deposit  --root W --order o --party jia --text-file f --tick 1
    python -m fair_exchange.cli delegate --root W --order o --party jia --tick 2
    python -m fair_exchange.cli confirm  --root W --order o --party jia --tick 3
    python -m fair_exchange.cli policy   --root W --order o --policy HOLD_FOR_CONFIRMATION --hold 10 --tick 2
    python -m fair_exchange.cli withdraw --root W --order o --party yi --tick 4
    python -m fair_exchange.cli advance  --root W --order o --tick N
    python -m fair_exchange.cli open     --root W --order o --party jia --tick 5
    python -m fair_exchange.cli status   --root W --order o
    python -m fair_exchange.cli ledger   --root W --order o
    python -m fair_exchange.cli scenario

Each invocation opens the same SQLite cabinet, does one step, and exits -- so
stopping and resuming between calls is ordinary.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .scenarios import replay_output
from .session import Session


def _print(obj: Any) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="fair_exchange.cli")
    p.add_argument("--root", default=None,
                   help="working directory for the vault (before the subcommand)")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, **kw):
        sp = sub.add_parser(name)
        # also accept --root after the subcommand for convenience
        sp.add_argument("--root", default=argparse.SUPPRESS)
        for k, v in kw.items():
            opt = "--" + k.replace("_", "-")
            sp.add_argument(opt, dest=k, **v)
        sp.set_defaults(func=fn)

    add("init", cmd_init, parties={"required": True},
        bits={"type": int, "default": 1024})
    add("order", cmd_order, order={"required": True}, a={"required": True},
        b={"required": True}, retention={"type": int, "required": True},
        tick={"type": int, "required": True})
    add("deposit", cmd_deposit, order={"required": True}, party={"required": True},
        text_file={"required": True}, tick={"type": int, "required": True})
    add("withdraw", cmd_withdraw, order={"required": True}, party={"required": True},
        tick={"type": int, "required": True}, ver={"type": int, "default": None})
    add("delegate", cmd_delegate, order={"required": True}, party={"required": True},
        tick={"type": int, "required": True})
    add("confirm", cmd_confirm, order={"required": True}, party={"required": True},
        tick={"type": int, "required": True})
    add("revoke", cmd_revoke, order={"required": True}, party={"required": True},
        tick={"type": int, "required": True})
    add("policy", cmd_policy, order={"required": True}, policy={"required": True},
        hold={"type": int, "default": 0}, expiry={"default": "ABORT"},
        tick={"type": int, "required": True})
    add("advance", cmd_advance, order={"required": True}, tick={"type": int, "required": True})
    add("open", cmd_open, order={"required": True}, party={"required": True},
        tick={"type": int, "required": True})
    add("status", cmd_status, order={"required": True})
    add("ledger", cmd_ledger, order={"required": True})
    add("opens", cmd_opens, order={"required": True})
    add("scenario", cmd_scenario)

    args = p.parse_args(argv)
    if args.cmd == "scenario":
        return args.func(args)
    if not getattr(args, "root", None):
        p.error("--root is required")
    root = args.root
    from .vault import VaultError
    try:
        with Session(root) as s:
            return args.func(s, args)
    except VaultError as e:
        _print({"error": e.code, "message": str(e)})
        return 2


def cmd_init(s: Session, a):
    for name in a.parties.split(","):
        name = name.strip()
        if name:
            s.add_party(name, a.bits)
    _print({"parties": s.parties()})
    return 0


def cmd_order(s: Session, a):
    s.open_order(a.order, a.a, a.b, a.retention, a.tick)
    _print(s.status(a.order))
    return 0


def cmd_deposit(s: Session, a):
    with open(a.text_file, "rb") as f:
        data = f.read()
    _print(s.deposit(a.order, a.party, data, a.tick))
    return 0


def cmd_withdraw(s: Session, a):
    s.withdraw(a.order, a.party, a.tick, a.ver)
    _print({"withdrawn": True})
    return 0


def cmd_delegate(s: Session, a):
    s.delegate(a.order, a.party, a.tick)
    _print({"delegated": True, "party": a.party})
    return 0


def cmd_confirm(s: Session, a):
    s.confirm(a.order, a.party, a.tick)
    _print({"confirmed": True, "party": a.party})
    return 0


def cmd_revoke(s: Session, a):
    _st, va, vb = s._current_pair(a.order)
    revoked = s.vault.revoke_auth(a.order, a.party, "DELEGATION",
                                  va["id"], vb["id"], a.tick)
    _print({"revoked": revoked})
    return 0


def cmd_policy(s: Session, a):
    s.set_policy(a.order, a.policy, a.tick, hold_ticks=a.hold, expiry_action=a.expiry)
    _print(s.status(a.order))
    return 0


def cmd_advance(s: Session, a):
    _print(s.advance(a.order, a.tick))
    return 0


def cmd_open(s: Session, a):
    rec = s.claim_and_open(a.order, a.party, a.tick)
    rec["plaintext"] = rec["plaintext"].decode(errors="replace")
    _print(rec)
    return 0


def cmd_status(s: Session, a):
    _print(s.status(a.order))
    return 0


def cmd_ledger(s: Session, a):
    _print(s.ledger(a.order))
    return 0


def cmd_opens(s: Session, a):
    _print(s.vault.opens(a.order))
    return 0


def cmd_scenario(a):
    _print(replay_output())
    return 0


if __name__ == "__main__":
    sys.exit(main())
