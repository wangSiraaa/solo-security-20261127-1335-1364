"""The display board and its trend statement.

The board is deliberately dumb and read-only.  It holds *exhibits* (public
numbers that were physically handed to it) and can be rebuilt from those
numbers alone; it never sees the true household ledger.  Consequences that
the exercises check:

* an exhibit for a different period than the board is showing is "not this
  period" and cannot be hung as the current period, even if the release is
  genuine;
* a trend statement over two overlapping exhibits drawn from the *same*
  perturbation cannot be called "two independent observations", while two
  exhibits from distinct releases can;
* a composed exhibit (made from a published table) can show a slice of the
  table but never a dimension the table did not support.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

from .core import Exhibit

Slot = Tuple[int, str]  # (period, slot_id)


class BoardError(Exception):
    pass


class PeriodMismatch(BoardError):
    pass


class PublicationIndex:
    """Metadata-only map: release id -> period it truly belongs to.

    This carries no household data and no true counts; it is exactly the
    "which period was this publication for" stamp the stats end can hand the
    board so the board can refuse a relabeled exhibit.
    """

    def __init__(self, periods: Optional[dict] = None):
        self._periods = dict(periods or {})

    def register(self, release_id: str, period: int) -> None:
        self._periods[release_id] = period

    def period_of(self, release_id: str) -> Optional[int]:
        return self._periods.get(release_id)

    def snapshot(self) -> dict:
        return dict(self._periods)


@dataclass
class TrendStatement:
    text: str
    independent_observations: int
    supporting_exhibit_ids: List[str]
    signed: bool
    direction: str = "unknown"  # "rising" | "falling" | "flat" | "unknown"
    signature: str = ""

    def __str__(self) -> str:  # pragma: no cover - presentation only
        return self.text


def _same_evidence(a: Exhibit, b: Exhibit) -> bool:
    """True when two exhibits trace to the *same* perturbation.

    Two slices of one publication, or any pair that literally reuses a
    perturbation fingerprint, are not two independent observations.  Slots
    that merely overlap while the perturbation was drawn separately do not
    merge: separate paid releases carry independent evidence.
    """
    if a.lineage and a.lineage == b.lineage:
        return True
    return bool(a.fingerprint) and a.fingerprint == b.fingerprint


class DisplayBoard:
    def __init__(self, current_period: int, index: Optional[PublicationIndex] = None):
        self.current_period = current_period
        self.index = index
        self._exhibits: List[Exhibit] = []

    # -- mounting ------------------------------------------------------------
    def mount(self, exhibit: Exhibit) -> None:
        """Hang an exhibit, but only if it belongs to the current period."""
        if exhibit.period != self.current_period:
            raise PeriodMismatch(
                f"exhibit {exhibit.exhibit_id!r} is for period "
                f"{exhibit.period}, board shows period {self.current_period}"
            )
        if self.index is not None:
            true_period = self.index.period_of(exhibit.lineage)
            if true_period is not None and true_period != self.current_period:
                raise PeriodMismatch(
                    f"exhibit {exhibit.exhibit_id!r} claims period "
                    f"{exhibit.period} but its publication belongs to period "
                    f"{true_period}"
                )
        if any(e.exhibit_id == exhibit.exhibit_id for e in self._exhibits):
            return  # idempotent re-hang of the exact same exhibit
        self._exhibits.append(exhibit)

    def try_mount(self, exhibit: Exhibit) -> bool:
        try:
            self.mount(exhibit)
            return True
        except PeriodMismatch:
            return False

    # -- reads ---------------------------------------------------------------
    def exhibits(self, period: Optional[int] = None) -> List[Exhibit]:
        if period is None:
            return list(self._exhibits)
        return [e for e in self._exhibits if e.period == period]

    def find(self, exhibit_id: str) -> Optional[Exhibit]:
        for e in self._exhibits:
            if e.exhibit_id == exhibit_id:
                return e
        return None

    def describe(self, period: Optional[int] = None) -> str:
        """A public trend blurb from public values only, with independence."""
        shown = self.exhibits(period)
        if not shown:
            return "no exhibits for this period"
        ordered = sorted(shown, key=lambda e: min(e.slots))
        curve = " -> ".join(f"{e.public_value}" for e in ordered)
        if len(ordered) >= 2:
            first, last = ordered[0].public_value, ordered[-1].public_value
            direction = (
                "rising" if last > first
                else "falling" if last < first
                else "flat"
            )
        else:
            direction = "single point"
        groups = self.independence_groups(shown)
        target = period if period is not None else self.current_period
        return (
            f"period {target}: "
            f"{len(shown)} exhibit(s), series [{curve}], direction={direction}, "
            f"independent evidence groups={len(groups)}"
        )

    def independence_groups(self, exhibits: Sequence[Exhibit]) -> List[List[Exhibit]]:
        """Group exhibits that share perturbation evidence.

        Two exhibits belong to one group when they trace to the same base
        publication (same ``lineage``) or explicitly reuse the same
        perturbation fingerprint.  A pair of *separate* paid releases stays in
        two groups even if their time slots happen to overlap: each drew its
        own perturbation, so each is its own observation.
        """
        parent = list(range(len(exhibits)))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def union(i: int, j: int) -> None:
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[rj] = ri

        for i in range(len(exhibits)):
            for j in range(i + 1, len(exhibits)):
                if _same_evidence(exhibits[i], exhibits[j]):
                    union(i, j)

        groups: dict[int, List[Exhibit]] = {}
        for i, exhibit in enumerate(exhibits):
            groups.setdefault(find(i), []).append(exhibit)
        return list(groups.values())

    # -- statement -----------------------------------------------------------
    def trend_statement(self, exhibit_ids: Iterable[str]) -> TrendStatement:
        """Sign a trend note for a pair of exhibits (this period only).

        Claims "two independent observations" only when the two exhibits
        trace to distinct perturbation evidence.  Two overlapping slices of
        the same release get one observation, not two.
        """
        chosen = []
        for eid in exhibit_ids:
            e = self.find(eid)
            if e is None:
                raise BoardError(f"exhibit {eid!r} is not on the board")
            if e.period != self.current_period:
                raise PeriodMismatch(
                    f"exhibit {eid!r} is period {e.period}, not current period "
                    f"{self.current_period}"
                )
            chosen.append(e)

        if len(chosen) < 2:
            raise BoardError("a trend statement needs at least two exhibits")
        if len({e.exhibit_id for e in chosen}) < len(chosen):
            raise BoardError("the same exhibit cannot be cited twice")

        groups = self.independence_groups(chosen)
        independent = len(groups)
        same_evidence = independent < len(chosen)
        direction = self._direction(chosen)

        if same_evidence:
            text = (
                "trend is supported by a single observation "
                f"(exhibits {[e.exhibit_id for e in chosen]} reuse the same "
                "perturbation evidence)"
            )
            return TrendStatement(text=text, independent_observations=1,
                                  supporting_exhibit_ids=[e.exhibit_id for e in chosen],
                                  signed=False, direction=direction)

        if direction == "rising":
            text = (
                "two independent observations support a rising trend "
                f"({[e.exhibit_id for e in chosen]})"
            )
        else:
            text = (
                f"two independent observations, direction={direction} "
                f"({[e.exhibit_id for e in chosen]})"
            )
        return TrendStatement(text=text, independent_observations=independent,
                              supporting_exhibit_ids=[e.exhibit_id for e in chosen],
                              signed=True, direction=direction)

    @staticmethod
    def _direction(exhibits: Sequence[Exhibit]) -> str:
        """Order exhibits by earliest slot and compare their public values."""
        ordered = sorted(exhibits, key=lambda e: min(e.slots))
        if len(ordered) < 2:
            return "unknown"
        first, last = ordered[0].public_value, ordered[-1].public_value
        if last > first:
            return "rising"
        if last < first:
            return "falling"
        return "flat"
