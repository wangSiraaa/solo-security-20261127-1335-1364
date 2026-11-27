"""Command-line driver for synth_ime.

State lives in a single JSON file; use ``--key-file`` to keep the device master
key outside that file for the stronger sealing guarantee.

Examples
--------
  python -m synth_ime.cli --db dev.json init
  python -m synth_ime.cli --db dev.json learn alice --token TOKEN "bra ka lo"
  python -m synth_ime.cli --db dev.json suggest "bra "
  python -m synth_ime.cli --db dev.json exit alice --token TOKEN
  python -m synth_ime.cli --db dev.json members
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from .engine import Device, ImeError, JsonStore, ReplayError, AuthError


def _device(args, allow_missing_key: bool = False) -> Device:
    key = None
    if getattr(args, "key_file", None):
        if os.path.exists(args.key_file):
            with open(args.key_file, "rb") as fh:
                key = fh.read()
        elif not allow_missing_key:
            raise SystemExit("key file %s does not exist" % args.key_file)
    return Device(JsonStore(args.db), master_key=key)


def _need_token(args) -> str:
    if not args.token:
        raise SystemExit("this command requires --token")
    return args.token


def cmd_init(args) -> None:
    from . import seal

    key = None
    if getattr(args, "key_file", None):
        if os.path.exists(args.key_file):
            with open(args.key_file, "rb") as fh:
                key = fh.read()
        else:
            key = seal.new_key()
            os.makedirs(os.path.dirname(os.path.abspath(args.key_file)) or ".", exist_ok=True)
            with open(args.key_file, "wb") as fh:
                fh.write(key)
    dev = Device(JsonStore(args.db), master_key=key)
    print(json.dumps({"db": args.db, "members": dev.members()}))


def cmd_join(args) -> None:
    dev = _device(args)
    print(json.dumps({"member": dev.join(_need_token(args))}))


def cmd_members(args) -> None:
    dev = _device(args)
    print(json.dumps({"members": dev.members()}))


def cmd_learn(args) -> None:
    dev = _device(args)
    print(json.dumps({"added": dev.learn(_need_token(args), args.sentences)}))


def cmd_unlearn(args) -> None:
    dev = _device(args)
    print(json.dumps({"removed": dev.unlearn(_need_token(args), args.sentence)}))


def cmd_exit(args) -> None:
    dev = _device(args)
    if args.member_id:
        print(json.dumps({"result": dev.revoke(args.member_id)}))
    else:
        print(json.dumps({"result": dev.exit(_need_token(args))}))


def cmd_suggest(args) -> None:
    dev = _device(args)
    if args.as_user is not None:
        dev.set_current_user(args.as_user)
    print(json.dumps(dev.suggest(args.prefix, forbid=args.forbid or ())))


def cmd_chain(args) -> None:
    dev = _device(args)
    if args.as_user is not None:
        dev.set_current_user(args.as_user)
    print(json.dumps(dev.chain(args.prefix, args.steps, forbid=args.forbid or ())))


def cmd_counts(args) -> None:
    dev = _device(args)
    print(json.dumps(dev.counts_for(args.prefix)))


def cmd_member_sentences(args) -> None:
    dev = _device(args)
    print(json.dumps(dev.member_sentences(args.member_id)))


def cmd_backup(args) -> None:
    dev = _device(args)
    print(json.dumps(dev.member_backup(_need_token(args)), indent=2, sort_keys=True))


def cmd_restore(args) -> None:
    dev = _device(args)
    with open(args.file, "r", encoding="utf-8") as fh:
        bundle = json.load(fh)
    n = dev.member_restore(_need_token(args), bundle, replace=args.replace)
    print(json.dumps({"restored": n}))


def cmd_set_base(args) -> None:
    dev = _device(args)
    with open(args.file, "r", encoding="utf-8") as fh:
        dev.set_base(json.load(fh))
    print(json.dumps({"base": len(dev.state["base"])}))


def cmd_disable(args) -> None:
    dev = _device(args)
    dev.set_current_user(args.as_user)
    dev.local_disable(args.word)
    print(json.dumps({"disabled": sorted(dev.local_disabled())}))


def cmd_enable(args) -> None:
    dev = _device(args)
    dev.set_current_user(args.as_user)
    dev.local_enable(args.word)
    print(json.dumps({"disabled": sorted(dev.local_disabled())}))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="synth_ime", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--db", default="synthime.json", help="state file (default: synthime.json)")
    p.add_argument("--key-file", help="store the device master key in this file, not the state file")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create/initialise the state file").set_defaults(func=cmd_init)
    sub.add_parser("members", help="list member ids").set_defaults(func=cmd_members)

    s = sub.add_parser("join", help="enrol a member from their token")
    s.add_argument("--token", required=True)
    s.set_defaults(func=cmd_join)

    s = sub.add_parser("learn", help="teach sentences")
    s.add_argument("member", help="member name (informational)")
    s.add_argument("--token", required=True)
    s.add_argument("sentences", nargs="+")
    s.set_defaults(func=cmd_learn)

    s = sub.add_parser("unlearn", help="remove a sentence this member taught")
    s.add_argument("--token", required=True)
    s.add_argument("sentence")
    s.set_defaults(func=cmd_unlearn)

    s = sub.add_parser("exit", help="withdraw a member's contribution")
    s.add_argument("member", nargs="?", help="member name (informational)")
    s.add_argument("--token")
    s.add_argument("--member-id", help="revoke by member id without their token")
    s.set_defaults(func=cmd_exit)

    s = sub.add_parser("suggest", help="next-word suggestions for a prefix")
    s.add_argument("prefix")
    s.add_argument("--forbid", action="append", help="locally disabled word")
    s.add_argument("--as-user", help="apply this user's local disabled-word list")
    s.set_defaults(func=cmd_suggest)

    s = sub.add_parser("chain", help="multi-step association")
    s.add_argument("prefix")
    s.add_argument("--steps", type=int, default=3)
    s.add_argument("--forbid", action="append")
    s.add_argument("--as-user")
    s.set_defaults(func=cmd_chain)

    s = sub.add_parser("counts", help="show merged counts for a prefix")
    s.add_argument("prefix")
    s.set_defaults(func=cmd_counts)

    s = sub.add_parser("member-sentences", help="show a member's sentences (device-internal)")
    s.add_argument("member_id")
    s.set_defaults(func=cmd_member_sentences)

    s = sub.add_parser("backup", help="export a member's learning material")
    s.add_argument("--token", required=True)
    s.set_defaults(func=cmd_backup)

    s = sub.add_parser("restore", help="restore a member's learning material from a file")
    s.add_argument("--token", required=True)
    s.add_argument("--file", required=True)
    s.add_argument("--replace", action="store_true", help="swap current material for the bundle")
    s.set_defaults(func=cmd_restore)

    s = sub.add_parser("set-base", help="replace the public base corpus from a JSON file")
    s.add_argument("--file", required=True)
    s.set_defaults(func=cmd_set_base)

    s = sub.add_parser("disable", help="disable a word for the current local user")
    s.add_argument("word")
    s.add_argument("--as-user", default="default")
    s.set_defaults(func=cmd_disable)

    s = sub.add_parser("enable", help="re-enable a word for the current local user")
    s.add_argument("word")
    s.add_argument("--as-user", default="default")
    s.set_defaults(func=cmd_enable)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except (ImeError, AuthError, ReplayError, ValueError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
