"""Test calendar.

All dates in the exercise are relative to a fixed "today" so that the phrase
"a birthday falls between the two screenings" is stable no matter when the code
runs.  Real systems would use the actual clock; here determinism matters more.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache

TODAY = date(2026, 11, 27)


def days(delta: int) -> date:
    return TODAY + timedelta(days=delta)


def day_number(d: date) -> int:
    return d.toordinal()


@lru_cache(maxsize=None)
def age_on(dob: date, ref: date) -> int:
    """Whole years completed by ``ref``."""
    years = ref.year - dob.year
    if (ref.month, ref.day) < (dob.month, dob.day):
        years -= 1
    return years


@dataclass(frozen=True)
class Screening:
    title: str
    date: date
    age_tier: int

    def admits(self, dob: date) -> bool:
        return age_on(dob, self.date) >= self.age_tier


class Program:
    """A weekend programme: ordered screenings with their tiers."""

    def __init__(self, program_id: str, screenings: list[Screening]):
        self.program_id = program_id
        self.screenings = list(screenings)
        self.dates = [s.date for s in self.screenings]
        self.tiers = [s.age_tier for s in self.screenings]

    def __len__(self) -> int:
        return len(self.screenings)

    @property
    def newest_date(self) -> date:
        return max(self.dates)

    def screening(self, idx: int) -> Screening:
        return self.screenings[idx]

    def fingerprint(self) -> str:
        import hashlib
        import json

        blob = json.dumps(
            {
                "program_id": self.program_id,
                "screenings": [
                    {"title": s.title, "date": s.date.isoformat(), "age_tier": s.age_tier}
                    for s in self.screenings
                ],
            },
            sort_keys=True,
        ).encode()
        return hashlib.sha256(blob).hexdigest()


def default_program() -> Program:
    """A weekend programme with two age tiers and a gap day between shows.

    Friday opening gala is 18+; Sunday closing show is 12+.  The calendar has a
    real gap (Saturday) so a holder's birthday can fall strictly between the two
    screenings -- the case the exercise relies on.
    """
    return Program(
        "weekend-2026-w48",
        [
            Screening("Opening gala (18+)", days(0), 18),   # Fri 2026-11-27
            Screening("Closing show (12+)", days(2), 12),   # Sun 2026-11-29
        ],
    )
