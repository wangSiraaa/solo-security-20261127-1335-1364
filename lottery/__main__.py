"""Command-line interface for the public-draw service.

Subcommands
-----------
  baseline           Run the three-ticket baseline, recompute, and issue a claim.
  tamper             Show that one bad reveal fails verification; restore fixes it.
  duplicate-claim    Show settlement is once-only via the claims ledger.
  escrow-absent      Incident: escrow depositor absent, cabinets hand over.
  cabinet-offline    Incident: a cabinet is offline, all submitters present.
  postpone-reopen    Deadline round with a missing original postpones, then reopens.
  reissue            Open a round twice (同轮重发) and show identical packets.
  shift              Print the single-witness-shift trade-off.
  verify FILE        Verify a published package JSON from an outside reader.
  audit FILE         Classify a round from a published event-log JSON.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile

from .audit import classify_round, rounds_overview
from .public_package import load_package, verify_package
from .scenarios import (
    commit_all,
    escrow_all,
    incident_cabinet_offline_submitters_present,
    incident_escrow_absent_depositor,
    make_participants,
    witness_shift_choice,
)
from .service import DrawService, DuplicateSettlement


def _svc(dirpath: str | None = None) -> DrawService:
    return DrawService(package_dir=dirpath if dirpath is not None else tempfile.mkdtemp())


def _print(title: str) -> None:
    print(f"\n=== {title} ===")


def cmd_baseline(args: argparse.Namespace) -> int:
    d = tempfile.mkdtemp()
    svc = DrawService(package_dir=d)
    parts = make_participants([("T1", "H1"), ("T2", "H2"), ("T3", "H3")], source="escrow")
    svc.create_round(
        "R1", [p.as_ticket() for p in parts], quota=2, mixing_rule="sha256-order-v1", witness_shift="escrow"
    )
    commit_all(svc, "R1", parts)
    escrow_all(svc, "R1", parts)
    out = svc.open_round("R1", witness="reviewer")
    _print("baseline open")
    print("draw winners:", out.winners)
    pkg = svc.package("R1")
    res = verify_package(pkg)
    print("outside verification:", "OK" if res.ok else "FAIL", res.errors)
    print("recomputed winners:", res.recomputed_winners)
    receipt = svc.claim("R1", out.winners[0])
    _print("claim receipt issued")
    print(json.dumps(receipt.to_dict(), indent=2))
    return 0 if res.ok else 1


def cmd_tamper(args: argparse.Namespace) -> int:
    d = tempfile.mkdtemp()
    svc = DrawService(package_dir=d)
    parts = make_participants([("T1", "H1"), ("T2", "H2"), ("T3", "H3")], source="escrow")
    svc.create_round(
        "R1", [p.as_ticket() for p in parts], quota=2, mixing_rule="sha256-order-v1", witness_shift="escrow"
    )
    commit_all(svc, "R1", parts)
    escrow_all(svc, "R1", parts)
    out = svc.open_round("R1", witness="reviewer")
    pkg = svc.package("R1")

    # Tamper: swap one reveal for a value that does not match its commitment.
    bad = json.loads(json.dumps(pkg))
    victim = pkg["draw_winners"][0]
    bad["reveals"][victim]["value"] = "forged-value"
    bad["packet_hash"] = _rehash(bad)
    res_bad = verify_package(bad)
    _print("tampered reveal")
    print("winner:", victim, "-> verification:", "OK" if res_bad.ok else "FAIL")
    print("reason:", res_bad.errors)

    restored = json.loads(json.dumps(pkg))
    res_ok = verify_package(restored)
    _print("restored original reveal")
    print("verification:", "OK" if res_ok.ok else "FAIL", res_ok.errors)
    return 0 if (not res_bad.ok and res_ok.ok) else 1


def _rehash(pkg: dict) -> str:
    from .crypto import digest

    body = {k: v for k, v in pkg.items() if k != "packet_hash"}
    return digest(body)


def cmd_duplicate_claim(args: argparse.Namespace) -> int:
    d = tempfile.mkdtemp()
    svc = DrawService(package_dir=d)
    parts = make_participants([("T1", "H1"), ("T2", "H2"), ("T3", "H3")], source="escrow")
    svc.create_round(
        "R1", [p.as_ticket() for p in parts], quota=2, mixing_rule="sha256-order-v1", witness_shift="escrow"
    )
    commit_all(svc, "R1", parts)
    escrow_all(svc, "R1", parts)
    out = svc.open_round("R1", witness="reviewer")
    winner = out.winners[0]
    _print("ledger already shows the seat as handed out")
    svc.mark_settled("R1", winner)
    print("settled:", svc.is_settled("R1", winner))
    _print("attempt to claim again")
    try:
        svc.claim("R1", winner)
        print("UNEXPECTED: second claim succeeded")
        return 1
    except DuplicateSettlement as exc:
        print("refused:", exc)
        return 0


def cmd_escrow_absent(args: argparse.Namespace) -> int:
    svc = _svc()
    _print("incident: escrow depositor 甲 absent, cabinets reachable")
    info = incident_escrow_absent_depositor(svc)
    out = info["outcome"]
    print("absent depositor:", info["absent_depositor"])
    print("opened:", out.opened, "winners:", out.winners)
    print("witness (early shift):", "witness-early-shift")
    _print("shift trade-off")
    print(json.dumps(witness_shift_choice("escrow"), indent=2))
    res = verify_package(svc.package(info["round_id"]))
    print("verification:", "OK" if res.ok else "FAIL", res.errors)
    return 0 if out.opened and res.ok else 1


def cmd_cabinet_offline(args: argparse.Namespace) -> int:
    svc = _svc()
    _print("incident: one cabinet offline, all submitters present")
    info = incident_cabinet_offline_submitters_present(svc)
    out = info["outcome"]
    print("offline cabinet:", info["offline_cabinet"])
    print("opened:", out.opened, "winners:", out.winners)
    res = verify_package(svc.package(info["round_id"]))
    print("verification:", "OK" if res.ok else "FAIL", res.errors)
    return 0 if out.opened and res.ok else 1


def cmd_postpone_reopen(args: argparse.Namespace) -> int:
    svc = _svc()
    parts = make_participants([("T1", "H1"), ("T2", "H2"), ("T3", "H3")], source="onsite")
    svc.create_round(
        "R1", [p.as_ticket() for p in parts], quota=2, mixing_rule="sha256-order-v1", witness_shift="opening"
    )
    commit_all(svc, "R1", parts)
    # two reveal, one does not -> cannot lock -> postpone, no winners
    svc.submit_reveal("R1", parts[0].ticket_id, parts[0].salt, parts[0].value)
    svc.submit_reveal("R1", parts[1].ticket_id, parts[1].salt, parts[1].value)
    out = svc.open_round("R1", witness="witness-opening-shift")
    _print("opening with incomplete originals")
    print("opened:", out.opened, "postponed:", out.postponed)
    print("missing:", out.missing_tickets)
    print("winners published:", svc.winners("R1"))
    info = classify_round(svc.log(), "R1")
    print("labels:", info["labels"])
    _print("reopen (补开) under a fresh public witness")
    new = svc.reopen("R1", "R1b", witness="witness-fresh")
    print("reopened round:", new.round_id, "from:", new.reopened_from)
    info2 = classify_round(svc.log(), "R1b")
    print("R1b labels:", info2["labels"])
    return 0 if (out.postponed and not svc.winners("R1")) else 1


def cmd_reissue(args: argparse.Namespace) -> int:
    d = tempfile.mkdtemp()
    svc = DrawService(package_dir=d)
    parts = make_participants([("T1", "H1"), ("T2", "H2"), ("T3", "H3")], source="escrow")
    svc.create_round(
        "R1", [p.as_ticket() for p in parts], quota=2, mixing_rule="sha256-order-v1", witness_shift="escrow"
    )
    commit_all(svc, "R1", parts)
    escrow_all(svc, "R1", parts)
    out = svc.open_round("R1", witness="reviewer")
    first = svc.package("R1")
    again = svc.reissue("R1")
    _print("same-round re-issue (同轮重发)")
    print("winners unchanged:", first["winners"] == again["winners"])
    print("packet hash unchanged:", first["packet_hash"] == again["packet_hash"])
    info = classify_round(svc.log(), "R1")
    print("labels:", info["labels"])
    return 0 if first["packet_hash"] == again["packet_hash"] else 1


def cmd_shift(args: argparse.Namespace) -> int:
    _print("single witness shift: choose escrow XOR opening-day seal")
    for s in ("escrow", "opening"):
        print(json.dumps(witness_shift_choice(s), indent=2))
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    pkg = load_package(args.file)
    res = verify_package(pkg)
    print(json.dumps(res.to_dict(), indent=2))
    return 0 if res.ok else 1


def cmd_audit(args: argparse.Namespace) -> int:
    with open(args.file, "r", encoding="utf-8") as fh:
        log = json.load(fh)
    overview = rounds_overview(log)
    print(json.dumps(overview, indent=2, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m lottery", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    for name, fn in [
        ("baseline", cmd_baseline),
        ("tamper", cmd_tamper),
        ("duplicate-claim", cmd_duplicate_claim),
        ("escrow-absent", cmd_escrow_absent),
        ("cabinet-offline", cmd_cabinet_offline),
        ("postpone-reopen", cmd_postpone_reopen),
        ("reissue", cmd_reissue),
        ("shift", cmd_shift),
    ]:
        sp = sub.add_parser(name)
        sp.set_defaults(func=fn)
    v = sub.add_parser("verify")
    v.add_argument("file")
    v.set_defaults(func=cmd_verify)
    a = sub.add_parser("audit")
    a.add_argument("file")
    a.set_defaults(func=cmd_audit)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
