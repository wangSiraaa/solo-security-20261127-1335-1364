#!/usr/bin/env python3
"""Runnable narrative demo for the signed segmented duty tape.

Run with:  python3 demo.py
The demo uses only the standard library and the public test sealer key.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from duty_tape.protocol import (
    artifact_records,
    build_artifact,
    dependency_pin,
    seal_artifact,
    write_tape,
)
from duty_tape.query import QueryEndpoint
from duty_tape.receiver import RecoveryTerminal
from duty_tape.sealer import TestSealer, new_revocation

ROOT = Path(__file__).resolve().parent
EXAMPLES = ROOT / "examples"
SEALER = TestSealer()
NOW = "2026-11-27T08:00:00+00:00"


def make_terminal(path: Path) -> RecoveryTerminal:
    return RecoveryTerminal(path, sealer=SEALER, clock=lambda: NOW)


def read_example(generation_dir: str, name: str) -> str:
    return (EXAMPLES / generation_dir / name).read_text(encoding="utf-8")


def batch(generation: str, batch_id: str):
    gen = {"old": (1, 1, 1), "new": (3, 2, 7)}[generation]
    subdir = generation
    contacts = build_artifact(
        batch=batch_id,
        kind="contacts",
        generation=gen[0],
        body=read_example(subdir, "contacts.csv"),
        issued_at=NOW,
    )
    lockers = build_artifact(
        batch=batch_id,
        kind="lockers",
        generation=gen[1],
        body=read_example(subdir, "lockers.csv"),
        issued_at=NOW,
    )
    duty = build_artifact(
        batch=batch_id,
        kind="duty",
        generation=gen[2],
        body=read_example(subdir, "duty.csv"),
        issued_at=NOW,
        dependencies=[dependency_pin(lockers)],
    )
    for art in (contacts, lockers, duty):
        seal_artifact(art, SEALER)
    return contacts, lockers, duty


def feed_artifacts(root: Path, artifacts) -> None:
    for art in artifacts:
        blob = "".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
            for record in artifact_records(art)
        ).encode("utf-8")
        result = make_terminal(root).feed_bytes(blob)
        if result["rejected"]:
            raise RuntimeError(result["rejected"])


def print_header(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def main() -> None:
    demo_root = Path(tempfile.mkdtemp(prefix="duty-tape-demo-"))
    state = demo_root / "terminal"
    print(f"demo state: {demo_root}")

    old = batch("old", "B-2026-11-20")
    new = batch("new", "B-2026-11-27")

    print_header("1. An older correct volume is already serving")
    feed_artifacts(state, old)
    q = QueryEndpoint(state)
    print("Lin Zhao old assignment:", json.dumps(q.person("Lin Zhao", "2026-11-27"), ensure_ascii=False))

    print_header("2. New tape tail fails after the independently sealed contacts table")
    contacts, lockers, duty = new
    contact_blob = "".join(
        json.dumps(r) + "\n" for r in artifact_records(contacts)
    ).encode()
    make_terminal(state).feed_bytes(contact_blob)

    # A little unverified locker prefix arrives before the read error.
    locker_prefix = artifact_records(lockers)[:2]
    prefix_blob = ("\n".join(json.dumps(r) for r in locker_prefix) + "\n").encode()
    make_terminal(state).feed_bytes(prefix_blob)
    make_terminal(state).tail_failed("simulated CRC failure at tape tail")
    make_terminal(state).clear_temporary_area()

    q = QueryEndpoint(state)
    print("Safe early rescue — abbreviation only:")
    print("  Zhou Ning =>", q.abbreviation("Zhou Ning")["abbreviation"])
    print("Old consistent duty pair still serves:")
    lin = q.person("Lin Zhao", "2026-11-27")
    print("  Lin Zhao =>", lin["abbreviation"], lin["locker_number"])
    status = make_terminal(state).status()
    print("Active generations:", {k: v["generation"] for k, v in status["active"].items()})

    print_header("3. Correct full tape is recovered and terminal can switch")
    tape_path = demo_root / "correct-volume.jsonl"
    write_tape(tape_path, [contacts, lockers, duty])
    result = make_terminal(state).feed_file(tape_path)
    assert not result["rejected"]
    chen = QueryEndpoint(state).person("Chen An", "2026-11-27")
    print("Chen An =>", chen["abbreviation"], chen["locker_number"])

    print_header("4. Another desk copies contacts; later revocation cannot erase that fact")
    copied = make_terminal(state).export_active("contacts", demo_root / "desk-2-copy")
    print("Copied to:", copied["destination"])
    rev = new_revocation(
        SEALER,
        artifact_digest=contacts["digest"],
        reason="demonstration revocation",
        issued_at=NOW,
    )
    make_terminal(state).revoke(rev)
    copy_still_exists = Path(copied["destination"]).exists()
    history = make_terminal(state).history({"table_copied", "revocation_received"})
    chain = make_terminal(state).verify_history()
    print("Copied file still exists outside local catalog:", copy_still_exists)
    print("Copy/revocation audit events:", len(history), "hash chain:", chain["valid"])

    print_header("5. Old-generation locker pin blocks the same verified new duty table")
    locked_state = demo_root / "locked-terminal"
    feed_artifacts(locked_state, old)
    old_locker_digest = old[1]["digest"]
    make_terminal(locked_state).pin_kind("lockers", old_locker_digest)
    feed_artifacts(locked_state, new)
    term = make_terminal(locked_state)
    new_duty_readiness = term.readiness_for(duty)
    print("New duty verified:", duty["artifact_id"])
    print("Ready to claim new locker numbers?", new_duty_readiness["ready"])
    print("Reason:", "; ".join(new_duty_readiness["reasons"]))
    print(
        "Query still on old generation:",
        QueryEndpoint(locked_state).person("Lin Zhao", "2026-11-27")["locker_number"],
    )

    print_header("6. Cross-batch same-name locker segment cannot satisfy this duty pin")
    mixed_state = demo_root / "mixed-batch"
    b_batch = batch("new", "B-OTHER-BATCH")
    b_lockers = b_batch[1]
    a_contacts, a_lockers, a_duty = new
    feed_artifacts(mixed_state, [b_lockers, a_contacts, a_duty])
    readiness = make_terminal(mixed_state).readiness_for(a_duty)
    print("A-duty ready with B locker segment?", readiness["ready"])
    feed_artifacts(mixed_state, [a_lockers])
    p2 = QueryEndpoint(mixed_state).person("p2", "2026-11-27")
    print("After exact A locker arrives, p2 =>", p2["locker_number"])

    print("\nAll demo assertions/queries completed. Run `python3 -m unittest discover -s tests -v`.")


if __name__ == "__main__":
    main()
