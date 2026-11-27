"""Core primitives: the mock mechanism, the ledger, and release records.

Vocabulary used throughout the exercise
---------------------------------------
household : the privacy unit.  A budget (spending cap) is attached to a
            household, *not* to a time slot ("场次") or a query.  Records from
            the same household in different slots all charge that household.
slot      : a self-contained log of check-ins (one "场次").  A query names
            one or more slots.
public    : the published value.  It is ``true + perturbation``.  It is the
            only number exposed for a release; the true aggregate never is.
born      : draws the perturbation for a release exactly once and fixes its
            fingerprint.  A retry must reuse ``born``'s output; drawing again
            is the failure the query desk exists to prevent.

The perturbation is deterministic and mock-only; it is deliberately *not* a
sound privacy mechanism.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Dict, Iterable, Tuple

# A "epoch"/period mainly orders releases and qualifies a trend statement
# ("this period's trend").  Numbers are period labels only.
HouseholdId = str
Period = int

BASELINE_DAILY_BUDGET = 3.0


class BudgetExceeded(Exception):
    """Raised when a release would push one household past its cap."""

    def __init__(self, household: HouseholdId, needed: float, remaining: float):
        self.household = household
        self.needed = needed
        self.remaining = remaining
        super().__init__(
            f"household {household!r} needs {needed:.3f} but only "
            f"{remaining:.3f} remains"
        )


class Budget:
    """Per-household spending caps.  Shared by every slot for that household."""

    def __init__(self, caps: Dict[HouseholdId, float] | None = None,
                 default: float = BASELINE_DAILY_BUDGET):
        self._caps: Dict[HouseholdId, float] = dict(caps or {})
        self._default = default
        self._spent: Dict[HouseholdId, float] = {}

    def cap_for(self, household: HouseholdId) -> float:
        return self._caps.get(household, self._default)

    def spent(self, household: HouseholdId) -> float:
        return self._spent.get(household, 0.0)

    def remaining(self, household: HouseholdId) -> float:
        return self.cap_for(household) - self.spent(household)

    def check(self, charges: Dict[HouseholdId, float]) -> None:
        """Raise :class:`BudgetExceeded` if *charges* do not fit."""
        for household, amount in charges.items():
            if amount <= 0:
                continue
            left = self.remaining(household)
            if left + 1e-12 < amount:
                raise BudgetExceeded(household, amount, left)

    def apply(self, charges: Dict[HouseholdId, float]) -> None:
        """Commit *charges*.  Assumes :meth:`check` already passed."""
        for household, amount in charges.items():
            if amount <= 0:
                continue
            self._spent[household] = self.spent(household) + amount

    def snapshot(self) -> Dict[HouseholdId, float]:
        return dict(self._spent)

    def restore(self, spent: Dict[HouseholdId, float]) -> None:
        self._spent = dict(spent)


class Mechanism:
    """A reproducible, mock perturbation mechanism.

    ``sample(...)`` is the moment a *new* perturbation is produced.  It is
    called exactly once per genuinely new release.  ``reuse(record)`` takes
    the already-fixed perturbation of a prior release.  Nothing here inspects
    a random sample to estimate privacy strength.
    """

    def sample(self, salt: str, key: str) -> int:
        digest = hashlib.sha256(f"{salt}\x1f{key}".encode()).digest()
        # A deliberately tiny mock perturbation so public numbers stay legible
        # and small-count series keep their direction.  Not a real mechanism.
        return int.from_bytes(digest[:4], "big") % 3 - 1  # in [-1, 1]

    @staticmethod
    def reuse(record: "ReleaseRecord") -> int:
        return record.perturbation


@dataclass(frozen=True)
class ReleaseRecord:
    """Everything the system remembers about one *published* release."""

    release_id: str
    query_key: str
    slots: Tuple[Tuple[int, str], ...]  # (period, slot_id), sorted
    households: Tuple[HouseholdId, ...]  # affected, sorted
    per_household_cost: float
    perturbation: int
    salt: str
    public_value: int
    fingerprint: str
    display_only: bool = False
    period: Period = 0
    # Present for a published table release: per-slot public cells.  Each cell
    # is (perturbation, public).  The true per-cell value stays at the stats
    # end and never reaches the board.
    cells: Dict[Tuple[int, str], Tuple[int, int]] | None = None

    @property
    def households_set(self) -> frozenset:
        return frozenset(self.households)

    def total_cost(self) -> float:
        return self.per_household_cost * len(self.households)


@dataclass
class Exhibit:
    """What the display board physically received.

    A board is fed exhibits, never releases and never the household ledger.
    An exhibit references a base release (``lineage``) and carries only public
    numbers.  Composed exhibits (built from a published table) keep the base
    release's ``fingerprint`` as their shared perturbation evidence.
    """

    exhibit_id: str
    lineage: str           # base release this exhibit can be traced to
    period: Period
    slots: Tuple[Tuple[int, str], ...]
    public_value: int
    fingerprint: str
    display_only: bool = False
    composed: bool = False

    @staticmethod
    def from_record(record: ReleaseRecord) -> "Exhibit":
        return Exhibit(
            exhibit_id=record.release_id,
            lineage=record.release_id,
            period=record.period,
            slots=record.slots,
            public_value=record.public_value,
            fingerprint=record.fingerprint,
            display_only=record.display_only,
        )


_DEFAULT_MECHANISM = Mechanism()


def _aggregate(slots: Iterable[Tuple[int, str, Tuple[str, ...]]]) -> Tuple[int, Tuple[str, ...]]:
    """Sum check-ins across slots, returning the value and affected households.

    Arguments are ``(period, slot_id, households_of_slot)``.  The same
    household appearing in several slots counts once for the sum but is
    charged for this release as one affected household.
    """
    total = 0
    households = set()
    for _period, _slot_id, hids in slots:
        total += len(hids)
        households.update(hids)
    return total, tuple(sorted(households))


def _fingerprint(salt: str, key: str, evidence, public_value: int) -> str:
    raw = f"{salt}\x1f{key}\x1f{evidence}\x1f{public_value}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def make_release(
    key: str,
    slot_data: Iterable[Tuple[int, str, Tuple[str, ...]]],
    cost: float,
    salt: str = "room-salt-1",
    mechanism: Mechanism = _DEFAULT_MECHANISM,
    display_only: bool = False,
    per_cell: bool = False,
) -> ReleaseRecord:
    """Born one release: draw its perturbation once and fix its fingerprint.

    *slot_data* is ``(period, slot_id, households)`` per contributing slot.
    With ``per_cell`` (a published table) each slot gets its own public cell
    and the release total is the sum of those cells.
    """
    materialised = sorted((int(p), str(s), tuple(h)) for p, s, h in slot_data)
    if not materialised:
        raise ValueError("a release needs at least one slot")
    true_total, households = _aggregate(materialised)
    period = materialised[0][0]

    if per_cell:
        cells: Dict[Tuple[int, str], Tuple[int, int]] = {}
        public = 0
        for p, s, hids in materialised:
            cell_pert = mechanism.sample(salt, f"{key}#{p}:{s}")
            cell_public = len(hids) + cell_pert
            cells[(p, s)] = (cell_pert, cell_public)
            public += cell_public
        perturbation = 0
        evidence = ":".join(
            f"{p}:{s}={cells[(p, s)][0]}" for p, s, _ in materialised
        )
    else:
        cells = None
        perturbation = mechanism.sample(salt, key)
        public = true_total + perturbation
        evidence = str(perturbation)

    release_id = _fingerprint(salt, key, evidence, public)
    return ReleaseRecord(
        release_id=release_id,
        query_key=key,
        slots=tuple((p, s) for p, s, _ in materialised),
        households=households,
        per_household_cost=float(cost),
        perturbation=perturbation,
        salt=salt,
        public_value=public,
        fingerprint=release_id,
        display_only=display_only,
        period=period,
        cells=cells,
    )
