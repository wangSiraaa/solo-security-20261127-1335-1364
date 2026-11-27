"""Persistent budget ledger keyed by *what was released*, not by request.

The ledger is the single source of truth for what has actually been spent.
It makes the two awkward cases correct by construction:

* re-sending an identical release charges nothing (the query key is already
  recorded, and the caller gets the recorded release back);
* a request whose acknowledgment was lost can be rolled back, so the honest
  retry reuses the same perturbation rather than drawing a new one.

Signing *reserves* the charge; acknowledging keeps it; rolling back refunds
it.  So a lost acknowledgement produces zero real spend, and the retry that
re-sends the same key spends exactly once.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .core import Budget, HouseholdId, ReleaseRecord


class Ledger:
    def __init__(self, budget: Budget):
        self.budget = budget
        self._by_key: Dict[str, ReleaseRecord] = {}
        self._by_id: Dict[str, ReleaseRecord] = {}
        self._journal: List[dict] = []
        self._pending: Optional[Tuple[str, Dict[HouseholdId, float], Dict[str, float]]] = None

    # -- reads ---------------------------------------------------------------
    def existing(self, key: str) -> Optional[ReleaseRecord]:
        return self._by_key.get(key)

    def record_for_id(self, release_id: str) -> Optional[ReleaseRecord]:
        return self._by_id.get(release_id)

    def all_records(self) -> List[ReleaseRecord]:
        return list(self._by_key.values())

    def journal(self) -> List[dict]:
        return list(self._journal)

    # -- transaction ---------------------------------------------------------
    def begin(self, record: ReleaseRecord, charges: Dict[HouseholdId, float]) -> None:
        """Reserve *charges* for a signed release; refundable until committed."""
        if self._pending is not None:
            raise RuntimeError("a ledger transaction is already open")
        before = self.budget.snapshot()
        self.budget.apply(charges)  # reserve
        self._pending = (record.query_key, dict(charges), before)

    def commit(self, record: ReleaseRecord, reason: str = "ack") -> None:
        """Keep the reservation and persist the release."""
        if self._pending is None:
            raise RuntimeError("no open ledger transaction")
        key, charges, _before = self._pending
        if key != record.query_key:
            raise RuntimeError("committing a different release than opened")
        self._pending = None
        self._persist(record, charges, reason)

    def rollback(self) -> None:
        """Abandon the open transaction, refunding the reserved charge."""
        if self._pending is None:
            return
        _key, _charges, before = self._pending
        self._pending = None
        self.budget.restore(before)
        self._journal.append({"event": "rollback"})

    # -- direct store (already-checked, already-applied) ---------------------
    def store(self, record: ReleaseRecord, charges: Dict[HouseholdId, float],
              reason: str = "release") -> None:
        self.budget.apply(charges)
        self._persist(record, charges, reason)

    def _persist(self, record: ReleaseRecord, charges: Dict[HouseholdId, float],
                 reason: str) -> None:
        self._by_key[record.query_key] = record
        self._by_id[record.release_id] = record
        self._journal.append({
            "event": "release",
            "reason": reason,
            "query_key": record.query_key,
            "release_id": record.release_id,
            "period": record.period,
            "cost": record.total_cost(),
            "charges": dict(charges),
            "public_value": record.public_value,
            "display_only": record.display_only,
        })
