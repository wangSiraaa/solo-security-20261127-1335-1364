"""The query desk: sign a release, hand it out once, refund on lost ack.

Two ways to ask a question, both legitimate (the exercise lets the desk pick
either):

``ask_detailed`` (route *direct*)
    Each new question is priced against its *affected households* and, on
    success, keeps a real fresh release.  A follow-up about a sub-range of
    slots is simply a new question: it charges again and can exhaust a busy
    household's cap first.

``ask_table`` (route *published table*)
    One up-front release of an agreed range ("约定范围的扰动分项表").  Later
    views are composed only from numbers already in the table, so changing
    the slice never adds budget, but the board can never show a dimension the
    table did not support.

Both go through :meth:`sign` / :meth:`ack`, so the lost-acknowledgement retry
is handled the same way regardless of route.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .core import Budget, Exhibit, HouseholdId, Mechanism, ReleaseRecord, make_release
from .core import _DEFAULT_MECHANISM
from .ledger import Ledger

SlotData = Tuple[int, str, Tuple[str, ...]]  # (period, slot_id, households)


class TableDoesNotSupport(Exception):
    """A composed view asked for a dimension the published table lacks."""


@dataclass
class Signed:
    """A release the desk has signed and charged, not yet acknowledged."""

    record: ReleaseRecord
    charges: Dict[HouseholdId, float]


class PublishedTable:
    """A read-only view over a published table release."""

    def __init__(self, record: ReleaseRecord):
        if record.cells is None:
            raise ValueError("record is not a published table")
        self.record = record

    @property
    def slots(self) -> List[Tuple[int, str]]:
        return list(self.record.slots)

    def has(self, period: int, slot_id: str) -> bool:
        return (period, slot_id) in self.record.cells

    def compose(self, slots: List[Tuple[int, str]]) -> Exhibit:
        """Sum public cells for *slots*; never charges and never redraws."""
        missing = [s for s in slots if s not in self.record.cells]
        if missing:
            raise TableDoesNotSupport(
                f"table {self.record.release_id!r} has no cell for {missing!r}"
            )
        total = sum(self.record.cells[tuple(s)][1] for s in slots)
        ordered = tuple(sorted(tuple(s) for s in slots))
        return Exhibit(
            exhibit_id=f"{self.record.release_id}:{'|'.join(f'{p}:{s}' for p, s in ordered)}",
            lineage=self.record.release_id,
            period=self.record.period,
            slots=ordered,
            public_value=total,
            fingerprint=self.record.fingerprint,  # shared perturbation evidence
            display_only=True,
            composed=True,
        )


class QueryDesk:
    def __init__(self, budget: Budget, mechanism: Mechanism = _DEFAULT_MECHANISM,
                 cost: float = 1.0):
        self.ledger = Ledger(budget)
        self.mechanism = mechanism
        self.cost = cost
        self._awaiting_ack: Dict[str, ReleaseRecord] = {}
        # Drafts survive rollback: a re-sent query reuses the *same* draw even
        # if the mechanism were random, so a retry never samples a new number.
        self._drafts: Dict[str, ReleaseRecord] = {}

    # --------------------------------------------------------------- signing
    def _sign(self, key: str, slots: List[SlotData], per_cell: bool,
              display_only: bool) -> Signed:
        prior = self.ledger.existing(key)
        if prior is not None:
            return Signed(record=prior, charges={})

        # A re-send before the ack landed: hand back the same signed release
        # without reserving its charge a second time.
        for held in self._awaiting_ack.values():
            if held.query_key == key:
                return Signed(record=held, charges={})

        record = self._drafts.get(key)
        if record is None:
            record = make_release(
                key, slots, cost=self.cost, mechanism=self.mechanism,
                display_only=display_only, per_cell=per_cell,
            )
            self._drafts[key] = record
        elif (record.cells is not None) != per_cell:
            raise RuntimeError(
                f"query key {key!r} was already drawn in a different mode"
            )

        charges = {h: self.cost for h in record.households}
        self.ledger.budget.check(charges)  # may raise BudgetExceeded
        self.ledger.begin(record, charges)
        self._awaiting_ack[record.release_id] = record
        return Signed(record=record, charges=charges)

    # ------------------------------------------------------------------ direct
    def ask_detailed(self, key: str, slots: List[SlotData],
                     display_only: bool = False) -> Signed:
        """Direct route: a new question keeps a real, freshly perturbed release.

        An identical re-send returns the recorded release with no charge.
        """
        return self._sign(key, slots, per_cell=False, display_only=display_only)

    # -------------------------------------------------------------- published
    def ask_table(self, key: str, slots: List[SlotData]) -> PublishedTable:
        """Publish one agreed range up front; future views reuse its cells."""
        signed = self._sign(key, slots, per_cell=True, display_only=True)
        if signed.charges:
            self.ack(signed.record.release_id)
        record = self.ledger.existing(key) or signed.record
        return PublishedTable(record)

    def existing_table(self, key: str) -> Optional[PublishedTable]:
        record = self.ledger.existing(key)
        if record is None or record.cells is None:
            return None
        return PublishedTable(record)

    # ------------------------------------------------------------ sign/ack API
    def ack(self, release_id: str) -> ReleaseRecord:
        """Acknowledge receipt; commits the open transaction for that release."""
        record = self._awaiting_ack.pop(release_id, None)
        if record is None:
            existing = self.ledger.record_for_id(release_id)
            if existing is not None:
                return existing
            raise KeyError(f"no signed release {release_id!r} awaiting ack")
        self.ledger.commit(record, reason="ack")
        return record

    def retry(self, key: str) -> Optional[ReleaseRecord]:
        """The ack was lost: refund the pending charge, then look the key up.

        The honest retry re-sends the *same* key, finds the same release and
        reuses its perturbation — no fresh draw, no extra spend.
        """
        self._awaiting_ack.clear()
        self.ledger.rollback()
        return self.ledger.existing(key)

    def charge_total(self) -> float:
        return sum(self.ledger.budget.snapshot().values())

    # ------------------------------------------------------------ board feed
    def deliver_exhibit(self, key: str, index=None) -> Exhibit:
        """Produce the public exhibit for a published key (and stamp its period).

        This is the only thing the board receives: public numbers plus a
        lineage id.  If *index* (a :class:`PublicationIndex`) is supplied its
        period is registered so the board can reject relabeling.
        """
        record = self.ledger.existing(key)
        if record is None:
            raise KeyError(f"query {key!r} has not been published")
        if index is not None:
            index.register(record.release_id, record.period)
        return Exhibit.from_record(record)
