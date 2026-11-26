"""Core data types for the privacy desk.

A household answers a binary weekly question:

    1 = 有噪声 / 投诉   (has noise / complained)
    0 = 无噪声 / 未投诉 (no noise / did not complain)

A public *fact* is always a statement about the sum of answers over a known
set of households ("covers").  Two kinds exist:

* ``exact``  -- ``sum(covers) == value``
* ``range``  -- ``lo <= sum(covers) <= hi``   (truthfully labelled range)

Both are public the moment they are issued/mailed; readers may pool every
receipt they hold within a week, so safety is always evaluated over the union.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence, Tuple


class PrivacyError(Exception):
    """Raised when a request would violate a structural rule of the desk."""


class BookingError(Exception):
    """Raised when a booking cannot be looked up or performed at all."""


def _canonical(payload: Mapping) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _sign(payload: Mapping) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class Constraint:
    """A single public statement about the sum of answers over ``covers``.

    ``Constraint`` carries no identity or provenance -- it is the pure logical
    content that the privacy engine reasons about.  Public artefacts are
    :class:`Receipt` objects that wrap a constraint.
    """

    covers: Tuple[str, ...]
    kind: str  # "exact" | "range"
    value: Optional[int] = None
    lo: Optional[int] = None
    hi: Optional[int] = None
    label: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "covers", tuple(self.covers))
        if not self.covers:
            raise PrivacyError("a constraint must cover at least one household")
        if len(set(self.covers)) != len(self.covers):
            raise PrivacyError("duplicate households in covers")
        if self.kind == "exact":
            if self.value is None or self.lo is not None or self.hi is not None:
                raise PrivacyError("exact constraint must carry value only")
            if not (0 <= self.value <= len(self.covers)):
                raise PrivacyError("exact value outside [0, |covers|]")
        elif self.kind == "range":
            if self.lo is None or self.hi is None or self.value is not None:
                raise PrivacyError("range constraint must carry lo/hi only")
            if not (0 <= self.lo <= self.hi <= len(self.covers)):
                raise PrivacyError("range bounds must satisfy 0 <= lo <= hi <= |covers|")
        else:
            raise PrivacyError(f"unknown constraint kind: {self.kind!r}")

    # -- constructors -----------------------------------------------------
    @classmethod
    def exact(cls, covers: Sequence[str], value: int, label: str = "") -> "Constraint":
        return cls(tuple(covers), "exact", value=value, label=label)

    @classmethod
    def range(cls, covers: Sequence[str], lo: int, hi: int, label: str = "") -> "Constraint":
        return cls(tuple(covers), "range", lo=lo, hi=hi, label=label)

    # -- semantics --------------------------------------------------------
    def holds(self, assignment: Mapping[str, int]) -> bool:
        s = sum(assignment[h] for h in self.covers)
        if self.kind == "exact":
            return s == self.value
        return self.lo <= s <= self.hi

    def sum_bounds(self) -> Tuple[int, int]:
        if self.kind == "exact":
            assert self.value is not None
            return (self.value, self.value)
        assert self.lo is not None and self.hi is not None
        return (self.lo, self.hi)

    def is_exact(self) -> bool:
        return self.kind == "exact"

    def describe(self) -> str:
        joined = ",".join(self.covers)
        if self.kind == "exact":
            return f"{{{joined}}}: exact count = {self.value}"
        return f"{{{joined}}}: count in [{self.lo}, {self.hi}]"

    def to_dict(self) -> dict:
        return {
            "covers": list(self.covers),
            "kind": self.kind,
            "value": self.value,
            "lo": self.lo,
            "hi": self.hi,
            "label": self.label,
        }


@dataclass(frozen=True)
class Receipt:
    """A public artefact: a constraint that has been issued or mailed.

    Receipts are immutable.  The ledger only ever appends, so a same-week
    correction can never make an already-mailed number disappear from what
    readers can see.
    """

    receipt_id: str
    week: str
    group: str
    constraint: Constraint
    origin: str  # "published" | "mailed" | "correction"
    issued_at: str
    requested_exact: bool = True
    requester: str = ""
    signature: str = ""

    # convenience delegates -------------------------------------------------
    @property
    def covers(self) -> Tuple[str, ...]:
        return self.constraint.covers

    @property
    def kind(self) -> str:
        return self.constraint.kind

    @property
    def value(self) -> Optional[int]:
        return self.constraint.value

    @property
    def lo(self) -> Optional[int]:
        return self.constraint.lo

    @property
    def hi(self) -> Optional[int]:
        return self.constraint.hi

    @property
    def bookable(self) -> bool:
        """Only an exact, signed-out count can be used to book a work slot."""
        return self.constraint.kind == "exact"

    def describe(self) -> str:
        return f"[{self.receipt_id}] {self.constraint.describe()}  (origin={self.origin})"

    def to_dict(self) -> dict:
        return {
            "receipt_id": self.receipt_id,
            "week": self.week,
            "group": self.group,
            "constraint": self.constraint.to_dict(),
            "origin": self.origin,
            "issued_at": self.issued_at,
            "requested_exact": self.requested_exact,
            "requester": self.requester,
            "signature": self.signature,
        }


@dataclass
class Booking:
    """A repair-slot booking made from an actually signed-out receipt."""

    booking_id: str
    requester: str
    group: str
    receipt_id: str
    slot: Optional[str]
    status: str  # "booked" | "refused" | "needs_reschedule"
    reason: str
    created_at: str

    @property
    def booked(self) -> bool:
        return self.status == "booked"

    def to_dict(self) -> dict:
        return {
            "booking_id": self.booking_id,
            "requester": self.requester,
            "group": self.group,
            "receipt_id": self.receipt_id,
            "slot": self.slot,
            "status": self.status,
            "reason": self.reason,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class WeekConfig:
    """Static public facts for one week: households and the groups over them."""

    week_id: str
    households: Tuple[str, ...]
    groups: Mapping[str, Tuple[str, ...]]
    protected: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        hh = tuple(self.households)
        if not hh:
            raise PrivacyError("at least one household is required")
        if len(set(hh)) != len(hh):
            raise PrivacyError("duplicate household ids")
        object.__setattr__(self, "households", hh)

        groups = {name: tuple(cov) for name, cov in self.groups.items()}
        for name, cov in groups.items():
            if not cov:
                raise PrivacyError(f"group {name!r} covers no households")
            if len(set(cov)) != len(cov):
                raise PrivacyError(f"group {name!r} has duplicate households")
            unknown = [h for h in cov if h not in hh]
            if unknown:
                raise PrivacyError(f"group {name!r} references unknown households {unknown}")
        object.__setattr__(self, "groups", groups)

        prot = tuple(self.protected) if self.protected else hh
        unknown = [h for h in prot if h not in hh]
        if unknown:
            raise PrivacyError(f"protected list references unknown households {unknown}")
        object.__setattr__(self, "protected", prot)

    def group_covers(self, group: str) -> Tuple[str, ...]:
        if group not in self.groups:
            raise PrivacyError(f"unknown group {group!r}; known: {sorted(self.groups)}")
        return self.groups[group]


def make_receipt(
    *,
    receipt_id: str,
    week: str,
    group: str,
    constraint: Constraint,
    origin: str,
    issued_at: str,
    requested_exact: bool = True,
    requester: str = "",
) -> Receipt:
    signature = _sign(
        {
            "id": receipt_id,
            "week": week,
            "group": group,
            "constraint": constraint.to_dict(),
            "origin": origin,
            "issued_at": issued_at,
            "requested_exact": requested_exact,
            "requester": requester,
        }
    )
    return Receipt(
        receipt_id=receipt_id,
        week=week,
        group=group,
        constraint=constraint,
        origin=origin,
        issued_at=issued_at,
        requested_exact=requested_exact,
        requester=requester,
        signature=signature,
    )
