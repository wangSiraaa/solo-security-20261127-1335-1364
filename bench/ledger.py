"""Consumption marks over physical reserves.

The ledger answers one question idempotently: "has this exact slice of this
reserve already been spent for this order?"  A resent request reuses the same
mark, so a lost reply never double-spends material.

Independence itself is *not* computed here -- that is the engine's job, because
it depends on spent/paused state that lives with the engine.  The ledger only
knows pools and marks, never bit equality.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

from .reserve import BatchKey, ReserveBook

MarkKey = Tuple[str, int, int, int]  # (source_id, epoch, offset, nbytes)


@dataclass(frozen=True)
class Draw:
    """One contiguous slice taken from one reserve for one order."""

    key: BatchKey
    offset: int
    nbytes: int
    role: str = "independent"  # "independent" | "head" | "dependent"

    @property
    def source_id(self) -> str:
        return self.key[0]

    @property
    def epoch(self) -> int:
        return self.key[1]

    @property
    def mark_key(self) -> MarkKey:
        return (self.key[0], self.key[1], self.offset, self.nbytes)


class IndependenceLedger:
    def __init__(self, reserves: ReserveBook):
        self._reserves = reserves
        self._marks: Dict[MarkKey, str] = {}  # mark_key -> order_id

    # -- marks -------------------------------------------------------------

    def is_marked(self, draw: Draw) -> bool:
        return draw.mark_key in self._marks

    def mark(self, draw: Draw, order_id: str) -> bool:
        """Idempotently record ``draw`` for ``order_id``.

        Returns ``True`` if this call recorded it, ``False`` if it was already
        marked (a resent request must not double-count).
        """
        if draw.mark_key in self._marks:
            return False
        self._marks[draw.mark_key] = order_id
        return True

    def marked_by(self, order_id: str) -> List[Draw]:
        return [
            Draw(key=(k[0], k[1]), offset=k[2], nbytes=k[3])
            for k, oid in self._marks.items()
            if oid == order_id
        ]

    def marks(self) -> Dict[MarkKey, str]:
        return dict(self._marks)

    # -- raw pool survey (no independence judgement) ----------------------

    def live_by_pool(self) -> Dict[BatchKey, int]:
        out: Dict[BatchKey, int] = {}
        for key in self._reserves.keys():
            r = self._reserves.get(key)
            if r is not None and r.live() > 0:
                out[key] = r.live()
        return out

    def total_live_bytes(self) -> int:
        return sum(self.live_by_pool().values())
