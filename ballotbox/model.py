"""Core data types, constants and errors for the two-hall anonymous ballot exercise.

Domain vocabulary (kept close to the Chinese requirements):

* 会员台 (MembershipDesk)  -- knows *who* may take part and *which card* was issued,
  handles 挂失 (loss reports).  It never learns a choice.
* 展厅 (Hall)              -- two of them, each backed by its own local inbox
  directory.  The clerk may only read its own post's receipts.  A hall records
  ballots (choice, no member) and intake/receipts (card, no choice).
* 凭条 (Token)             -- an authenticated slip, valid for exactly one event
  and carrying no member number, so the 意见箱 cannot see membership.
* 最终接收权 (final-receipt authority) -- granted by the desk to one hall, which
  can then issue a 收妥回执 (FINAL receipt) even while offline.
* 跨厅流转 (transfer)      -- no single final authority; a hall issues a 暂存回条
  (TEMPORARY receipt); it only counts after a hand-off ("交接") before deadline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple

# ---- Event / authority states -------------------------------------------------

EVENT_OPEN = "OPEN"
EVENT_CLOSED = "CLOSED"

AUTH_EXCLUSIVE = "EXCLUSIVE"  # final-receipt authority sits with one hall
AUTH_TRANSFER = "TRANSFER"    # cross-hall transfer eligibility; temporary only

LOSS_NONE = "NONE"
LOSS_PENDING = "PENDING"        # reported, but authority hall is offline -> hand-off
LOSS_EFFECTIVE = "EFFECTIVE"

RECEIPT_FINAL = "FINAL"         # 收妥回执 -- counts
RECEIPT_TEMPORARY = "TEMPORARY"  # 暂存回条 -- provisional, not yet counted
RECEIPT_VOID = "VOID"

BALLOT_PENDING = "PENDING"   # accepted provisionally (暂存), not yet counted
BALLOT_COUNTED = "COUNTED"
BALLOT_VOID = "VOID"


# ---- Errors -------------------------------------------------------------------

class BallotError(Exception):
    """Base class for all protocol violations in this exercise."""


class InvalidSignature(BallotError):
    """The slip's authentication tag does not verify (tampered or forged)."""


class EventMismatch(BallotError):
    """The slip belongs to a different event than the hall is collecting."""


class Rejected(BallotError):
    """A submission was refused.  ``reason`` is a stable machine code."""

    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}" if detail else reason)


class AlreadyIssued(BallotError):
    """A member already holds the one card allowed for this event."""


class NotAllowed(BallotError):
    """An operation is not permitted in the current state."""


class ForeignPost(BallotError):
    """A clerk tried to read a post other than its own."""


# ---- Records ------------------------------------------------------------------

@dataclass
class Event:
    event_id: str
    choices: Tuple[str, ...]
    key: bytes
    deadline: Optional[int] = None
    state: str = EVENT_OPEN


@dataclass
class Token:
    """What a member physically carries.  No member id, only event + random id."""
    token_id: str
    event_id: str
    slip: str  # the wire form: "v1:<event>:<token_id>:<nonce>:<mac>"

    def __str__(self) -> str:  # convenience for CLI / tests
        return self.slip


@dataclass
class TokenRecord:
    """Desk-side view of an issued card.  Deliberately has *no* choice field."""
    token_id: str
    member_id: str
    event_id: str
    authority: object = None  # ("EXCLUSIVE", hall_id) | AUTH_TRANSFER | None
    granted_at: Optional[int] = None
    loss_state: str = LOSS_NONE
    loss_requested_at: Optional[int] = None
    loss_effective_at: Optional[int] = None
    loss_note: str = ""
    finalized: bool = False
    finalized_hall: Optional[str] = None
    finalized_at: Optional[int] = None
    consumed_ballot: Optional[str] = None
    # hall_id -> {ballot_id, receipt_id, at}; provisional (暂存) acceptances
    provisionals: dict = field(default_factory=dict)


@dataclass
class LossResult:
    token_id: str
    effective: bool
    pending: bool
    state: str
    effective_at: Optional[int]
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "token_id": self.token_id,
            "effective": self.effective,
            "pending": self.pending,
            "state": self.state,
            "effective_at": self.effective_at,
            "reason": self.reason,
        }


@dataclass
class Receipt:
    """A 收妥回执 (FINAL) or a 暂存回条 (TEMPORARY) or a VOID notice."""
    receipt_id: str
    token_id: str
    ballot_id: Optional[str]
    hall: str
    kind: str
    at: int
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "receipt_id": self.receipt_id,
            "token_id": self.token_id,
            "ballot_id": self.ballot_id,
            "hall": self.hall,
            "kind": self.kind,
            "at": self.at,
            "note": self.note,
        }


@dataclass
class Ballot:
    """An anonymous choice.  No token, no member -- only a random ballot id."""
    ballot_id: str
    event_id: str
    hall: str
    choice: str
    at: int
    status: str = BALLOT_COUNTED

    def as_dict(self) -> dict:
        return {
            "ballot_id": self.ballot_id,
            "event_id": self.event_id,
            "hall": self.hall,
            "choice": self.choice,
            "at": self.at,
            "status": self.status,
        }


@dataclass
class SubmitResult:
    ok: bool
    receipt: Optional[Receipt] = None
    reason: str = ""
    detail: str = ""


__all__ = [
    "EVENT_OPEN", "EVENT_CLOSED",
    "AUTH_EXCLUSIVE", "AUTH_TRANSFER",
    "LOSS_NONE", "LOSS_PENDING", "LOSS_EFFECTIVE",
    "RECEIPT_FINAL", "RECEIPT_TEMPORARY", "RECEIPT_VOID",
    "BALLOT_PENDING", "BALLOT_COUNTED", "BALLOT_VOID",
    "BallotError", "InvalidSignature", "EventMismatch", "Rejected",
    "AlreadyIssued", "NotAllowed", "ForeignPost",
    "Event", "Token", "TokenRecord", "LossResult", "Receipt", "Ballot",
    "SubmitResult",
]
