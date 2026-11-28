"""The outsourced display console (外席 / 外席显示台).

This module deliberately has **no access** to the inner world: it sees only

* ``PublicEvent`` objects decoded from delivered outer frames, and
* a :class:`StepTable` that the duty officer (值班员) registered by hand — the
  step names of the batch and their relative order.

It must therefore never infer ordering from arrival order.  A later stage can
legitimately be delivered before an earlier one (two components interleave, and
one shipment's failure receipt can overtake its predecessor).  When a failure is
seen but an earlier stage is still missing, the console cannot yet prove that
this failure is the *first* definite one, so it displays the shipment as
``依据尚缺`` (``AWAITING_BASIS``) naming the predecessor it is waiting on.  Once
that predecessor arrives the failure location becomes confirmable and can be
fetched again by the original receipt id.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .model import Outcome, PublicEvent, Report, Status


class StepTableError(ValueError):
    """Raised when the operator registers ambiguous step ordering."""


@dataclass
class StepTable:
    """Operator-registered step names and their relative order for a batch.

    Registration can be incremental, which is what lets the duty officer add an
    *unfamiliar* step name later as long as its place in the sequence is stated.
    """

    _rank: Dict[str, int] = field(default_factory=dict)

    def register(self, step_name: str, rank: Optional[int] = None) -> None:
        """Register a step name.

        With ``rank=None`` the step is appended after all currently known steps.
        With an explicit ``rank`` the step is placed at that position; any
        conflicting existing assignment raises :class:`StepTableError`.
        """

        if step_name in self._rank:
            if rank is not None and rank != self._rank[step_name]:
                raise StepTableError(
                    f"step {step_name!r} already registered at rank "
                    f"{self._rank[step_name]}, cannot move to {rank}"
                )
            return
        if rank is None:
            rank = (max(self._rank.values()) + 1) if self._rank else 0
        if rank in self._rank.values():
            raise StepTableError(f"rank {rank} already taken")
        self._rank[step_name] = rank

    def register_in_order(self, steps: List[str]) -> None:
        for name in steps:
            self.register(name)

    def rank_of(self, step_name: str) -> Optional[int]:
        return self._rank.get(step_name)

    def ordered(self) -> List[str]:
        return [name for name, _ in sorted(self._rank.items(), key=lambda kv: kv[1])]

    def __contains__(self, step_name: object) -> bool:
        return step_name in self._rank


class Console:
    """Merges delivered public events per receipt and reports a shipment state."""

    def __init__(self, table: StepTable) -> None:
        self.table = table
        self._events: Dict[str, Dict[str, PublicEvent]] = {}
        # Keep the raw delivered frames too, so "actually delivered bytes" can be
        # audited without re-deriving them.
        self._raw: Dict[str, List[bytes]] = {}

    # -- ingestion ---------------------------------------------------------

    def deliver(self, event: PublicEvent, raw_frame: Optional[bytes] = None) -> None:
        """Deliver one public event (and optionally its exact wire bytes)."""

        per_receipt = self._events.setdefault(event.receipt_id, {})
        # First observation wins; duplicates are idempotent.
        per_receipt.setdefault(event.step_name, event)
        if raw_frame is not None:
            self._raw.setdefault(event.receipt_id, []).append(raw_frame)

    def deliver_frame(self, frame: bytes) -> PublicEvent:
        """Decode and deliver an outer frame."""

        from .wire import decode_frame

        event = decode_frame(frame)
        self.deliver(event, raw_frame=frame)
        return event

    # -- introspection -----------------------------------------------------

    def receipts(self) -> List[str]:
        return list(self._events)

    def delivered_bytes(self, receipt_id: str) -> bytes:
        return b"".join(self._raw.get(receipt_id, []))

    def delivered_frame_lengths(self, receipt_id: str) -> List[int]:
        return [len(f) for f in self._raw.get(receipt_id, [])]

    def observed(self, receipt_id: str) -> Dict[str, PublicEvent]:
        return dict(self._events.get(receipt_id, {}))

    def merge_relations(self, receipt_id: str) -> List[Tuple[str, str]]:
        """How the console merged stages for one receipt.

        Returns ``(step, outcome)`` pairs in the console's *table order*, which
        is independent of arrival order.  This is the observable "merge
        relationship" used by the non-interference checks.
        """

        per = self._events.get(receipt_id, {})
        pairs: List[Tuple[str, str]] = []
        for name in self.table.ordered():
            if name in per:
                pairs.append((name, per[name].outcome.value))
        return pairs

    # -- interpretation ----------------------------------------------------

    def report(self, receipt_id: str) -> Report:
        """Compute the shipment's state from delivered events + the step table."""

        per = self._events.get(receipt_id)
        if not per:
            return Report(receipt_id, Status.EMPTY, reason="no public bytes delivered")

        ordered = self.table.ordered()
        # Steps we know how to order. Anything observed but unregistered is kept
        # aside; the operator must register it to place it in the sequence.
        unregistered = [name for name in per if name not in self.table]

        for name in ordered:
            event = per.get(name)
            if event is None:
                # We cannot confirm *anything* past this gap, including a later
                # observed failure.  If a failure was seen downstream, we are
                # awaiting the basis (predecessor); otherwise still waiting.
                downstream_failure = self._first_downstream_failure(per, name)
                if downstream_failure is not None:
                    return Report(
                        receipt_id,
                        Status.AWAITING_BASIS,
                        step=name,
                        reason=(
                            f"observed failure at {downstream_failure} but "
                            f"precursor {name} not yet delivered"
                        ),
                        observed_failure=downstream_failure,
                    )
                return Report(
                    receipt_id,
                    Status.WAITING,
                    step=name,
                    reason="stage not yet delivered",
                )

            if event.outcome is Outcome.FAILURE:
                return Report(
                    receipt_id,
                    Status.FAILED,
                    step=name,
                    reason="first definite failure",
                )
            # success -> keep walking

        if unregistered:
            # Everything registered succeeded, but there are steps we cannot
            # order yet.  Be conservative and ask the operator to register them
            # rather than declaring the shipment done.
            stranger = sorted(unregistered)[0]
            return Report(
                receipt_id,
                Status.AWAITING_BASIS,
                step=stranger,
                reason=f"unregistered step {stranger!r}; register its order",
            )

        return Report(receipt_id, Status.DONE, reason="all stages confirmed successful")

    def _first_downstream_failure(
        self, per: Dict[str, PublicEvent], after_step: str
    ) -> Optional[str]:
        rank = self.table.rank_of(after_step)
        if rank is None:
            return None
        best: Optional[Tuple[int, str]] = None
        for name, event in per.items():
            if event.outcome is not Outcome.FAILURE:
                continue
            r = self.table.rank_of(name)
            if r is None or r <= rank:
                continue
            if best is None or r < best[0]:
                best = (r, name)
        return None if best is None else best[1]

    def reports(self) -> Dict[str, Report]:
        return {rid: self.report(rid) for rid in self._events}
