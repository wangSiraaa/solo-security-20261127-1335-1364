"""Core data model for the read-aloud licensing exercise.

POLICY CHOSEN (one of the two offered):
  Offline limited-count *capability* with a NON-ROLLBACK local counter.

  The desk pre-grants a player a lease: a chapter, a number of normal listen
  credits, and a borrower scope. The player consumes credits against a local,
  strictly-increasing counter (it can never be rolled back). Content is still
  provisioned segment-by-segment, so a mid-chapter disconnect leaves later
  segments honestly "missing". To return, the player SEALS itself (refuses any
  further open) and emits a device-signed ReturnProof carrying its final
  counter; the desk verifies binding + signature and only then frees the
  remaining credits for the waiting borrower.

Consumption boundary (one "listen"):
  * A NORMAL segment consumes one credit when it is first opened.
  * Pausing then RESUMING the same segment on the same device within
    RESUME_WINDOW sim-seconds is free (no extra credit).
  * Resuming after the window, or opening any different segment, consumes a
    fresh credit.
  * PREVIEW_ONLY segments never consume a normal credit, are playable only
    while connected (live), and are never cached.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


RESUME_WINDOW = 300  # sim-seconds a paused segment may be resumed for free


class LeaseScope(str, Enum):
    FULL = "full"                     # all normal segments (cacheable) + previews live
    FRONT_PREVIEW = "front-preview"   # only PREVIEW_ONLY segments, live only


class LeaseState(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"


class ReturnStatus(str, Enum):
    PENDING = "pending"    # offline / not yet verifiable -> "to be checked"
    LIMITED = "limited"    # provisional, only part of the balance is releasable
    SETTLED = "settled"    # fully verified and reconciled


class EventKind(str, Enum):
    OPEN = "open"
    RESUME = "resume"
    PAUSE = "pause"
    PLAY_LIVE = "play-live"
    MISSING = "missing"
    REFUSED = "refused"
    SEAL = "seal"


@dataclass(frozen=True)
class PlayEvent:
    at: int
    kind: EventKind
    seg_id: Optional[str]
    detail: str = ""


@dataclass(frozen=True)
class Lease:
    lease_id: str
    chapter_id: str
    device_id: str
    borrower_id: str
    plays_total: int
    scope: LeaseScope
    issued_at: int
    expires_at: int
    signature: str = ""

    def payload(self) -> Dict[str, Any]:
        return {
            "lease_id": self.lease_id,
            "chapter_id": self.chapter_id,
            "device_id": self.device_id,
            "borrower_id": self.borrower_id,
            "plays_total": self.plays_total,
            "scope": self.scope.value,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
        }


@dataclass(frozen=True)
class ReturnProof:
    """Device-signed proof of final consumption. Emitted at seal time."""
    lease_id: str
    chapter_id: str
    device_id: str
    plays_consumed: int
    sealed: bool
    at: int
    play_log: List[Dict[str, Any]] = field(default_factory=list)
    signature: str = ""

    def payload(self) -> Dict[str, Any]:
        return {
            "lease_id": self.lease_id,
            "chapter_id": self.chapter_id,
            "device_id": self.device_id,
            "plays_consumed": self.plays_consumed,
            "sealed": self.sealed,
            "at": self.at,
            "play_log": self.play_log,
        }


@dataclass(frozen=True)
class ReturnReceipt:
    """Desk-issued receipt handed to a returning borrower."""
    receipt_id: str
    lease_id: str
    chapter_id: str
    device_id: str
    plays_total: int
    plays_consumed: int
    credits_returned: int
    status: ReturnStatus
    note: str
    at: int


@dataclass
class DeviceLeaseState:
    """Mutable per-lease state held inside a toy player."""
    lease: Lease
    plays_consumed: int = 0
    open_seg: Optional[str] = None
    opened_at: Optional[int] = None
    paused: bool = False
    paused_at: Optional[int] = None
    sealed: bool = False
    state: LeaseState = LeaseState.ACTIVE
    cache: Dict[str, str] = field(default_factory=dict)   # seg_id -> text (normal only)
    log: List[PlayEvent] = field(default_factory=list)

    @property
    def credits_remaining(self) -> int:
        return self.lease.plays_total - self.plays_consumed
