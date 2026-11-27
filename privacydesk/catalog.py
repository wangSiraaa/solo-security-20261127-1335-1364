"""In-memory check-in catalog — the "ordinary entrance" for new records.

New batches of synthetic check-ins are registered here and then queried
through the same desk as anything else; nothing about the desk changes when
a new household or slot arrives.
"""

from __future__ import annotations

import random
from typing import Dict, Iterable, List, Tuple

from .core import HouseholdId

SlotData = Tuple[int, str, Tuple[str, ...]]  # (period, slot_id, households)


class CheckinCatalog:
    def __init__(self) -> None:
        self._slots: Dict[Tuple[int, str], Tuple[str, ...]] = {}

    def register_slot(self, period: int, slot_id: str,
                      households: Iterable[HouseholdId]) -> None:
        if (period, slot_id) in self._slots:
            raise KeyError(f"slot {slot_id!r} for period {period} already exists")
        self._slots[(period, slot_id)] = tuple(sorted(households))

    def register_batch(self, slots: Iterable[SlotData]) -> int:
        count = 0
        for period, slot_id, households in slots:
            self.register_slot(period, slot_id, households)
            count += 1
        return count

    def slot(self, period: int, slot_id: str) -> SlotData:
        key = (period, slot_id)
        if key not in self._slots:
            raise KeyError(f"unknown slot {slot_id!r} for period {period}")
        return (period, slot_id, self._slots[key])

    def slots(self, slots: Iterable[Tuple[int, str]]) -> List[SlotData]:
        return [self.slot(p, s) for p, s in slots]

    def period_slots(self, period: int) -> List[str]:
        return [s for (p, s) in self._slots if p == period]

    def households(self) -> List[HouseholdId]:
        found = set()
        for hids in self._slots.values():
            found.update(hids)
        return sorted(found)

    def synthetic_batch(self, period: int, slot_ids: Iterable[str],
                        households: Iterable[HouseholdId],
                        per_slot: int = 2, seed: int = 0) -> int:
        """Create plausible random check-in batches through the normal entry."""
        rng = random.Random(seed)
        pool = list(households)
        created = 0
        for slot_id in slot_ids:
            size = min(per_slot, len(pool))
            chosen = rng.sample(pool, size) if size else []
            self.register_slot(period, slot_id, chosen)
            created += 1
        return created
