"""Reusable scenario builders for demos and tests.

Encodes the two incidents from the brief and the single-witness-shift
trade-off, kept separate from the CLI so tests can call them directly.

Because one witness works one shift, a round's originals must all come from a
single custody route:

* ``escrow`` shift  -> all sources ``escrow`` (甲's route); the witness handled
  early custody so cannot host opening-day sealing.
* ``opening`` shift -> all sources ``onsite`` (乙's route); the witness hosts
  the opening, so there was no prior two-cabinet custody.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .service import DrawService


@dataclass
class Participant:
    ticket_id: str
    household_id: str
    salt: str
    value: str
    source: str = ""

    def as_ticket(self) -> dict:
        d = {"ticket_id": self.ticket_id, "household_id": self.household_id}
        if self.source:
            d["source"] = self.source
        return d


def make_participants(
    specs: list[tuple[str, str]], source: str = ""
) -> list[Participant]:
    """Build participants with deterministic-but-salted secrets."""
    parts = []
    for tid, hh in specs:
        parts.append(
            Participant(
                ticket_id=tid,
                household_id=hh,
                salt=f"salt-{tid}-{hh}",
                value=f"value-{tid}-{hh}",
                source=source,
            )
        )
    return parts


def commit_all(svc: DrawService, round_id: str, parts: list[Participant]) -> None:
    for p in parts:
        svc.commit(round_id, p.ticket_id, salt=p.salt, value=p.value)


def reveal_all(svc: DrawService, round_id: str, parts: list[Participant]) -> None:
    for p in parts:
        svc.submit_reveal(round_id, p.ticket_id, p.salt, p.value)


def escrow_all(svc: DrawService, round_id: str, parts: list[Participant]) -> None:
    for p in parts:
        svc.deposit_escrow(round_id, p.ticket_id, p.salt, p.value)


# ---------------------------------------------------------------------------
# Incident 1: escrow depositor (甲) is absent, but the cabinets are reachable.
# ---------------------------------------------------------------------------


def incident_escrow_absent_depositor(
    svc: DrawService,
    round_id: str = "R-escrow",
    participants: list[Participant] | None = None,
) -> dict[str, Any]:
    """甲 escrows early, then is absent; the cabinets hand over -> round opens.

    The round is sealed with the ``escrow`` shift, so every source is
    ``escrow``.  The absent depositor never hands in an original on opening day,
    yet the 2-of-2 cabinets release the *same* original against matching
    receipts, so the draw opens.  The witness worked the early shift and is not
    needed at the opening - precisely the trade-off escrow mode buys.
    """
    parts = participants or make_participants(
        [("A1", "H1"), ("A2", "H2"), ("A3", "H3")], source="escrow"
    )
    svc.create_round(
        round_id,
        [p.as_ticket() for p in parts],
        quota=2,
        mixing_rule="sha256-order-v1",
        witness_shift="escrow",
    )
    commit_all(svc, round_id, parts)
    escrow_all(svc, round_id, parts)
    absent = parts[0].ticket_id
    outcome = svc.open_round(round_id, witness="witness-early-shift")
    return {
        "round_id": round_id,
        "absent_depositor": absent,
        "outcome": outcome,
        "participants": parts,
    }


# ---------------------------------------------------------------------------
# Incident 2: a cabinet is offline, but every original submitter is present.
# ---------------------------------------------------------------------------


def incident_cabinet_offline_submitters_present(
    svc: DrawService,
    round_id: str = "R-onsite",
    participants: list[Participant] | None = None,
) -> dict[str, Any]:
    """One cabinet is down, but all submitters hand in originals on site.

    This round uses the ``opening`` shift (all sources ``onsite``), so it never
    relied on prior custody: the offline cabinet is irrelevant.  Every original
    is present, the round locks and opens.
    """
    parts = participants or make_participants(
        [("B1", "H1"), ("B2", "H2"), ("B3", "H3")], source="onsite"
    )
    svc.create_round(
        round_id,
        [p.as_ticket() for p in parts],
        quota=2,
        mixing_rule="sha256-order-v1",
        witness_shift="opening",
    )
    commit_all(svc, round_id, parts)
    offline = svc.cabinets.cabinets[0]
    offline.set_online(False)
    reveal_all(svc, round_id, parts)
    outcome = svc.open_round(round_id, witness="witness-opening-shift")
    return {
        "round_id": round_id,
        "offline_cabinet": offline.cabinet_id,
        "outcome": outcome,
        "participants": parts,
    }


# ---------------------------------------------------------------------------
# Witness-shift trade-off.
# ---------------------------------------------------------------------------


def witness_shift_choice(shift: str) -> dict[str, Any]:
    """Describe what a single witness shift can and cannot cover."""
    if shift == "escrow":
        return {
            "shift": "escrow",
            "can_escrow": True,
            "can_host_opening_seal": False,
            "opens_without_present_depositors": True,
            "note": "early custody available; opening-day synchronous sealing is not",
        }
    if shift == "opening":
        return {
            "shift": "opening",
            "can_escrow": False,
            "can_host_opening_seal": True,
            "opens_without_present_depositors": False,
            "note": "opening-day sealing available; no prior two-cabinet custody",
        }
    raise ValueError(f"unknown shift {shift!r}")
