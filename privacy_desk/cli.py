"""Command-line peer of the privacy desk.

Typical reviewer / worker hand-off on one machine::

    # reviewer: start a week from raw return slips, import old mailed paper
    python -m privacy_desk.cli new    --week W1 --spec spec.json --out sess.json
    python -m privacy_desk.cli mail   --session sess.json --group 整层 --exact 1
    python -m privacy_desk.cli publish --session sess.json --group 北翼 --who 租户

    # any reader: hold a receipt and book a work slot from it
    python -m privacy_desk.cli book   --session sess.json --receipt W1-published-001 \
                                      --who 租户 --slot 入户时段

    # inspect: what is public, what is derivable, what got booked
    python -m privacy_desk.cli report --session sess.json

    # late-discovered omission: record a breach faithfully
    python -m privacy_desk.cli audit  --session sess.json

    # replay every worked scenario from the brief
    python -m privacy_desk.cli demo

The ``--spec`` file for ``new`` is JSON::

    {
      "households": ["甲", "乙", "丙"],
      "groups": {"北翼": ["甲", "乙"], "整层": ["甲", "乙", "丙"]},
      "answers": {"甲": 1, "乙": 0, "丙": 0},
      "protected": ["甲", "乙", "丙"],
      "policy": "arrival",
      "reserved_groups": []
    }
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from .model import BookingError, PrivacyError, WeekConfig
from .scenarios import (
    replay_week1,
    replay_week2,
    run_composition_demo,
    run_correction_demo,
    run_late_discovery,
    run_preagreed,
)
from .session import Session


def _load(path: str) -> Session:
    with open(path, "r", encoding="utf-8") as fh:
        return Session.from_json(fh.read())


def _save(session: Session, path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(session.to_json())


def _spec_to_session(spec_path: str) -> Session:
    with open(spec_path, "r", encoding="utf-8") as fh:
        spec = json.load(fh)
    config = WeekConfig(
        week_id=spec.get("week_id", "W"),
        households=tuple(spec["households"]),
        groups={k: tuple(v) for k, v in spec["groups"].items()},
        protected=tuple(spec.get("protected") or spec["households"]),
    )
    return Session.from_raw(
        config,
        spec["answers"],
        policy=spec.get("policy", "arrival"),
        reserved_groups=tuple(spec.get("reserved_groups", [])),
    )


# -- command handlers ------------------------------------------------------
def cmd_new(args: argparse.Namespace) -> int:
    session = _spec_to_session(args.spec)
    _save(session, args.out)
    print(f"created session for week {session.config.week_id} -> {args.out}")
    print(session.report())
    return 0


def cmd_mail(args: argparse.Namespace) -> int:
    session = _load(args.session)
    if args.exact is not None:
        receipt = session.import_mailed_exact(group=args.group, value=args.exact)
    elif args.range is not None:
        lo, hi = args.range
        receipt = session.import_mailed_range(group=args.group, lo=lo, hi=hi)
    else:
        print("error: provide --exact N or --range LO HI", file=sys.stderr)
        return 2
    _save(session, args.session)
    print("imported mailed paper report:")
    print("  " + receipt.describe())
    return 0


def cmd_publish(args: argparse.Namespace) -> int:
    session = _load(args.session)
    out = session.publish(
        group=args.group, requester=args.who, want_exact=not args.no_exact
    )
    _save(session, args.session)
    print(out.describe())
    return 0


def cmd_book(args: argparse.Namespace) -> int:
    session = _load(args.session)
    all_slots = args.slots.split(",") if args.slots else None
    booking = session.book(
        receipt_id=args.receipt,
        requester=args.who,
        slot=args.slot,
        all_slots=all_slots,
    )
    if args.reschedule and booking.status == "refused":
        booking = session.desk.reschedule(booking.booking_id, args.reschedule)
    _save(session, args.session)
    slot = booking.slot if booking.slot is not None else "-"
    print(f"{booking.booking_id}: {booking.status} slot={slot}")
    print(f"  {booking.reason}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    session = _load(args.session)
    print(session.report())
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    session = _load(args.session)
    derived = session.derived()
    if not derived:
        print("audit: no protected household is derivable from public info.")
        return 0
    shown = ", ".join(f"{h}={v}" for h, v in sorted(derived.items()))
    note = {
        "week": session.config.week_id,
        "finding": "leak",
        "detail": f"public info derives {shown}",
        "derived": dict(derived),
    }
    session.ledger.note_audit(note)
    _save(session, args.session)
    print(f"audit: BREACH recorded -- {shown} is derivable from public info.")
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    blocks: List[str] = []
    blocks.append(run_composition_demo().render())
    for r in replay_week1():
        blocks.append(r.render())
    for r in replay_week2():
        blocks.append(r.render())
    blocks.append(run_late_discovery().render())
    blocks.append(run_correction_demo().render())
    blocks.append(run_preagreed("北翼").render())
    blocks.append(run_preagreed("整层").render())
    print("\n\n".join(blocks))
    return 0


# -- parser ----------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="privacy_desk.cli",
        description="Local privacy-preserving weekly publisher + repair booking desk.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    new = sub.add_parser("new", help="start a session from a raw spec file")
    new.add_argument("--spec", required=True)
    new.add_argument("--out", required=True)
    new.set_defaults(func=cmd_new)

    mail = sub.add_parser("mail", help="import an already-mailed paper report")
    mail.add_argument("--session", required=True)
    mail.add_argument("--group", required=True)
    g = mail.add_mutually_exclusive_group(required=True)
    g.add_argument("--exact", type=int)
    g.add_argument("--range", type=int, nargs=2, metavar=("LO", "HI"))
    mail.set_defaults(func=cmd_mail)

    pub = sub.add_parser("publish", help="submit a new publication request")
    pub.add_argument("--session", required=True)
    pub.add_argument("--group", required=True)
    pub.add_argument("--who", required=True)
    pub.add_argument("--no-exact", action="store_true", help="ask for a reference range only")
    pub.set_defaults(func=cmd_publish)

    bk = sub.add_parser("book", help="book a work slot from a signed-out receipt")
    bk.add_argument("--session", required=True)
    bk.add_argument("--receipt", required=True)
    bk.add_argument("--who", required=True)
    bk.add_argument("--slot", required=True)
    bk.add_argument("--slots", help="comma-separated slot list to draw from")
    bk.add_argument("--reschedule", help="fallback slot if the requested one is taken")
    bk.set_defaults(func=cmd_book)

    rep = sub.add_parser("report", help="show public state, derivations, bookings")
    rep.add_argument("--session", required=True)
    rep.set_defaults(func=cmd_report)

    aud = sub.add_parser("audit", help="record a late-discovered breach faithfully")
    aud.add_argument("--session", required=True)
    aud.set_defaults(func=cmd_audit)

    dem = sub.add_parser("demo", help="run every worked scenario from the brief")
    dem.set_defaults(func=cmd_demo)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (PrivacyError, BookingError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
