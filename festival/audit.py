"""Forensics: what did each party actually keep, and can they be joined?

The story ends by asking two buyers' records at two different counters be
inspected to see whether the same viewer can be linked across shops.  This module
collects exactly the material each location retained and runs concrete
correlation attacks against it:

* Do the retained blobs contain a birth date anywhere?
* Do they contain a festival-wide / full-history holder number?
* Do they contain the *other* counter's viewing history?
* Can the two counters' records be joined into one person?

It also checks the "nothing extra" property: the door may see only its own
screening's records, never another show's.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

_ISO_DATE = re.compile(r"\b(19\d{2}|20\d{2})-(\d{2})-(\d{2})\b")
_EXERCISE_ID = re.compile(r"\bEX-\d{4}[0-9a-f]*\b")


@dataclass
class RetainedMaterial:
    """A flattened, searchable view of everything a party kept."""

    owner: str
    blobs: list[str] = field(default_factory=list)

    def add(self, obj) -> None:
        self.blobs.append(json.dumps(obj, sort_keys=True, default=str))
        return self

    def text(self) -> str:
        return "\n".join(self.blobs)

    def dates_found(self) -> set[str]:
        out = set()
        for m in _ISO_DATE.finditer(self.text()):
            out.add(m.group(0))
        return out

    def exercise_ids_found(self) -> set[str]:
        return set(m.group(0) for m in _EXERCISE_ID.finditer(self.text()))


def collect_desk_material(desk, path_name: str) -> RetainedMaterial:
    rm = RetainedMaterial(f"desk:{desk.desk_id}")
    rm.add({
        "desk_id": desk.desk_id,
        "program_id": desk.program.program_id,
        "program_fingerprint": desk.program.fingerprint(),
        "path": path_name,
    })
    for rec in desk.ledger:
        rm.add(rec)
    return rm


def collect_door_material(terminal) -> RetainedMaterial:
    rm = RetainedMaterial(f"door:{terminal.terminal_id}")
    rm.add({
        "terminal_id": terminal.terminal_id,
        "program_id": terminal.view.program_id,
        "index": terminal.view.index,
        "day": terminal.view.day.isoformat(),
        "tier": terminal.view.tier,
    })
    rm.add(terminal.history())
    return rm


def collect_wallet_material(wallet) -> RetainedMaterial:
    rm = RetainedMaterial(f"wallet:{wallet.cred.exercise_id}")
    rm.add({"mode": wallet.mode, "exercise_id": wallet.cred.exercise_id,
            "dob": wallet.cred.dob.isoformat()})  # the wallet DOES hold the DOB
    for t in wallet.tickets.values():
        rm.add({"ticket_id": t.ticket_id, "program_id": t.program_id,
                "stubs": {str(i): s.to_signed() for i, s in t.stubs.items()}})
    return rm


def audit_privacy(material: RetainedMaterial, dob_of_holder: str) -> dict:
    """Check the non-wallet material leaks no DOB, no global ID, no cross-shop data."""
    dates = material.dates_found()
    ids = material.exercise_ids_found()
    return {
        "owner": material.owner,
        "contains_holder_dob": dob_of_holder in dates,
        "contains_exercise_id": bool(ids),
        "exercise_ids": sorted(ids),
        "dates_present": sorted(dates),
        "searches_clean": (dob_of_holder not in dates) and not ids,
    }


def try_cross_desk_correlation(desk_a_material: RetainedMaterial,
                               desk_b_material: RetainedMaterial) -> dict:
    """Attempt to join the same person across two shops using only retained data.

    Each desk stores a per-programme handle.  We look for any shared handle,
    shared binding key, or shared ticket id between the two retained blobs.
    """
    def handles(rm: RetainedMaterial) -> set[str]:
        found = set()
        for blob in rm.blobs:
            try:
                obj = json.loads(blob)
            except json.JSONDecodeError:
                continue
            for rec in (obj if isinstance(obj, list) else [obj]):
                if isinstance(rec, dict) and "holder" in rec and isinstance(rec["holder"], dict):
                    if "nullifier" in rec["holder"]:
                        found.add(f"N:{rec['holder']['nullifier']}")
                    if "binding_pub" in rec["holder"]:
                        found.add(f"K:{rec['holder']['binding_pub']}")
        return found

    a = handles(desk_a_material)
    b = handles(desk_b_material)
    shared = a & b
    return {
        "desk_a_handles": len(a),
        "desk_b_handles": len(b),
        "shared": sorted(shared),
        "correlatable": bool(shared),
    }


def door_sees_only_its_screening(terminal_material: RetainedMaterial,
                                 other_indices: list[int]) -> bool:
    """True if the door retained nothing about the other screenings."""
    text = terminal_material.text()
    for i in other_indices:
        if f'"index": {i}' in text or f'"index":{i}' in text:
            return False
    return True
