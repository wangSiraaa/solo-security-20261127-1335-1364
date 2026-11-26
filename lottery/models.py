"""Typed records for the public-draw service.

The event log is an append-only, hash-chained history.  Each round's entire
lifecycle (create -> seal -> postpone/reopen -> lock -> open -> replace) is a
chain of events, so an outside reader can tell a *supplementary opening*
(补开) from a plain *postponement* (延期) from a *re-issue of the same round*
(同轮重发) without trusting the operator's summary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .crypto import digest


# ---------------------------------------------------------------------------
# Round lifecycle states.
# ---------------------------------------------------------------------------

COMMITTING = "COMMITTING"  # accepting salted commitments
SEALED = "SEALED"          # roster order + rules + commitments locked
POSTPONED = "POSTPONED"    # postponed (延期); no winners may exist
OPENED = "OPENED"          # draw completed; winners published
INVALID = "INVALID"        # a reveal contradicted its commitment

# Reveal phases: while POSTPONABLE a round may still legally be postponed;
# once LOCKED the draw can no longer be changed or postponed.
POSTPONABLE = "POSTPONABLE"
LOCKED = "LOCKED"

MODES = ("escrow", "deadline")
WITNESS_SHIFT_TO_MODE = {"escrow": "escrow", "opening": "deadline"}


@dataclass(frozen=True)
class Ticket:
    ticket_id: str
    household_id: str


@dataclass
class Event:
    seq: int
    type: str
    data: dict[str, Any]
    prev_hash: str
    hash: str = ""

    def body(self) -> dict[str, Any]:
        return {"seq": self.seq, "type": self.type, "data": self.data, "prev_hash": self.prev_hash}

    def finalize(self) -> "Event":
        self.hash = digest(self.body())
        return self

    def to_dict(self) -> dict[str, Any]:
        d = self.body()
        d["hash"] = self.hash
        return d


def hash_chain(events: list[dict[str, Any]]) -> str | None:
    """Return the chain head for a list of serialised events (or None)."""
    prev = ""
    for i, e in enumerate(events):
        if e.get("seq") != i or e.get("prev_hash") != prev:
            raise ValueError(f"event chain broken at seq {i}")
        expected = digest(
            {"seq": e["seq"], "type": e["type"], "data": e["data"], "prev_hash": e["prev_hash"]}
        )
        if e.get("hash") != expected:
            raise ValueError(f"event hash mismatch at seq {i}")
        prev = expected
    return prev or None


def verify_event_chain(events: list[dict[str, Any]]) -> tuple[bool, str | None]:
    try:
        return True, hash_chain(events)
    except ValueError as exc:
        return False, str(exc)


@dataclass
class VerificationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)
    recomputed_winners: list[str] = field(default_factory=list)
    recomputed_draw_order: list[str] = field(default_factory=list)

    def add(self, name: str, passed: bool, detail: str = "") -> None:
        self.checks[name] = passed
        if not passed:
            self.errors.append(detail or name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": list(self.errors),
            "checks": dict(self.checks),
            "recomputed_winners": list(self.recomputed_winners),
            "recomputed_draw_order": list(self.recomputed_draw_order),
        }


@dataclass
class Receipt:
    round_id: str
    ticket_id: str
    household_id: str
    claim_token: str
    receipt_id: str
    receipt_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "round_id": self.round_id,
            "ticket_id": self.ticket_id,
            "household_id": self.household_id,
            "claim_token": self.claim_token,
            "receipt_id": self.receipt_id,
            "receipt_hash": self.receipt_hash,
        }


@dataclass
class Outcome:
    """Result of attempting to open a round."""

    opened: bool
    postponed: bool
    winners: list[str] = field(default_factory=list)
    reason: str = ""
    missing_tickets: list[str] = field(default_factory=list)
    offline_cabinets: list[str] = field(default_factory=list)
    state: str = ""
