"""Core value types for the confidential diagnostic channel.

The design draws a hard line between two worlds:

* the *inner* world (business components + trusted relay + local audit), which
  may see everything, including the confidential **drill marker** (演练标记);
* the *outer* world (the outsourced display console), which may only ever see
  **public** step names, public success/failure outcomes, and the shipment
  (receipt) identifier used to merge stages of one shipment.

All externally observable bytes are produced from :class:`PublicEvent` only.
The marker never has a representation in that encoding, which is what makes the
non-interference property testable at the byte level.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Outcome(str, Enum):
    """Public success/failure of a single stage."""

    SUCCESS = "success"
    FAILURE = "failure"


def coerce_outcome(value: "str | Outcome") -> Outcome:
    """Accept ``"success"`` / ``"failure"`` / :class:`Outcome`."""

    if isinstance(value, Outcome):
        return value
    return Outcome(value)


@dataclass(frozen=True)
class TypedEvent:
    """An event written by a *modern* business component using typed fields.

    ``marker`` is confidential and local-only.  The trusted relay projects this
    down to a :class:`PublicEvent` before anything leaves the host.
    """

    receipt_id: str
    stream_id: str
    step_name: str
    outcome: Outcome
    marker: str = ""


@dataclass(frozen=True)
class PublicEvent:
    """Everything the outer console is ever allowed to learn about a stage."""

    receipt_id: str
    step_name: str
    outcome: Outcome


class Status(str, Enum):
    """Outer-console interpretation of a shipment (receipt)."""

    DONE = "done"                    # every pipeline stage confirmed successful
    FAILED = "failed"                # first definite failure, locally located
    WAITING = "waiting"              # first stage still awaiting delivery
    AWAITING_BASIS = "awaiting_basis"  # 依据尚缺: cannot yet localise the first failure
    EMPTY = "empty"                  # nothing delivered for this receipt yet


@dataclass(frozen=True)
class Report:
    """The console's answer for one shipment.

    ``step`` always names a *specific* stage when one can be named: the failed
    stage, the stage still awaited, or (for ``AWAITING_BASIS``) the preceding
    receipt that must arrive before the observed failure can be confirmed.
    A normal failure report therefore never collapses into an empty receipt.
    """

    receipt_id: str
    status: Status
    step: Optional[str] = None
    reason: str = ""
    observed_failure: Optional[str] = None

    def describe(self) -> str:
        if self.status is Status.DONE:
            return f"{self.receipt_id}: done"
        if self.status is Status.FAILED:
            return f"{self.receipt_id}: failed at {self.step}"
        if self.status is Status.WAITING:
            return f"{self.receipt_id}: waiting at {self.step}"
        if self.status is Status.AWAITING_BASIS:
            detail = self.reason or "basis missing"
            return f"{self.receipt_id}: awaiting basis, need {self.step}: {detail}"
        return f"{self.receipt_id}: empty"
