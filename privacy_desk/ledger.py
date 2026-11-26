"""Append-only public ledger.

The ledger is the single source of truth for *what readers can see this week*.
It only ever appends receipts.  There is deliberately **no** delete, replace or
retract operation: a number that has been mailed out stays public forever, so a
same-week correction can only add information -- it can never make an earlier
figure vanish.  That is exactly the "同一周的更正不能让已经寄出的数字消失" rule.

The public state for a week is the union of every receipt recorded for that
week, regardless of whether it was ``published`` by the desk or ``mailed`` as a
paper report.  Readers are assumed to pool everything they hold.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from .model import Constraint, PrivacyError, Receipt, make_receipt


class Ledger:
    """An in-memory, append-only store of public receipts."""

    def __init__(self) -> None:
        self._receipts: List[Receipt] = []
        self._by_id: Dict[str, Receipt] = {}
        self._audit: List[dict] = []
        self._clock = 0

    # -- recording --------------------------------------------------------
    def _tick(self) -> str:
        # A monotonic pseudo-timestamp so ordering is deterministic in tests.
        self._clock += 1
        return f"t{self._clock:04d}"

    def record(
        self,
        *,
        week: str,
        group: str,
        constraint: Constraint,
        origin: str,
        requested_exact: bool = True,
        requester: str = "",
        receipt_id: Optional[str] = None,
    ) -> Receipt:
        """Append a receipt.  Never removes or rewrites anything."""
        if origin not in ("published", "mailed", "correction"):
            raise PrivacyError(f"unknown origin {origin!r}")
        rid = receipt_id or f"{week}-{origin}-{len(self._receipts) + 1:03d}"
        if rid in self._by_id:
            raise PrivacyError(f"duplicate receipt id {rid!r}")
        receipt = make_receipt(
            receipt_id=rid,
            week=week,
            group=group,
            constraint=constraint,
            origin=origin,
            issued_at=self._tick(),
            requested_exact=requested_exact,
            requester=requester,
        )
        self._receipts.append(receipt)
        self._by_id[rid] = receipt
        return receipt

    # -- queries ----------------------------------------------------------
    def all(self) -> List[Receipt]:
        return list(self._receipts)

    def for_week(self, week: str) -> List[Receipt]:
        return [r for r in self._receipts if r.week == week]

    def constraints_for_week(self, week: str) -> List[Constraint]:
        return [r.constraint for r in self.for_week(week)]

    def get(self, receipt_id: str) -> Receipt:
        if receipt_id not in self._by_id:
            raise PrivacyError(f"unknown receipt id {receipt_id!r}")
        return self._by_id[receipt_id]

    def groups_with_receipts(self, week: str) -> set:
        return {r.group for r in self.for_week(week)}

    def receipts_for_group(self, week: str, group: str) -> List[Receipt]:
        return [r for r in self.for_week(week) if r.group == group]

    def exact_receipt_for_group(self, week: str, group: str) -> Optional[Receipt]:
        """The latest exact receipt (published or mailed) for a group, if any."""
        candidates = [
            r for r in self.receipts_for_group(week, group) if r.constraint.is_exact()
        ]
        return candidates[-1] if candidates else None

    def __len__(self) -> int:
        return len(self._receipts)

    def to_list(self) -> List[dict]:
        return [r.to_dict() for r in self._receipts]

    # -- audit trail (append-only, distinct from receipts) ----------------
    def note_audit(self, record: dict) -> None:
        """Append a human-readable audit note (used to record late breaches).

        Audit notes are *not* receipts and carry no numbers into the privacy
        calculus; they exist so a leak discovered after the fact is recorded
        faithfully rather than silently dropped.
        """
        self._audit.append(dict(record))

    def audit_log(self) -> List[dict]:
        return [dict(a) for a in self._audit]
