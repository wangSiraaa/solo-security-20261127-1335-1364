"""Privacy-preserving weekly noise complaint publisher + repair booking desk.

The public standard this package enforces, in one sentence:

    Using only what has *already been made public this week* (published exact
    counts, published truthfully-labelled ranges, the household IDs each item
    covers, and public facts such as which household belongs to which group),
    every protected household must still have **at least two** possible
    answers.

Public information is append-only: once a number is mailed out it can never be
made to disappear by a later correction.  Readers may exchange every public
receipt they hold, so the safety analysis is always run against the *union* of
all receipts for the week.
"""

from .model import (
    Constraint,
    Receipt,
    Booking,
    WeekConfig,
    PrivacyError,
    BookingError,
)
from .privacy import (
    admissible_assignments,
    possible_values,
    is_safe,
    forced_answers,
    group_count_values,
    tightest_safe_range,
)
from .ledger import Ledger
from .publisher import Publisher, PublishOutcome
from .booking import BookingDesk
from .scenarios import (
    sample_week1,
    sample_week2,
    replay_week1,
    replay_week2,
    run_composition_demo,
    run_late_discovery,
    run_generic_week,
    run_correction_demo,
    run_preagreed,
)
from .session import Session

__all__ = [
    "Constraint",
    "Receipt",
    "Booking",
    "WeekConfig",
    "PrivacyError",
    "BookingError",
    "admissible_assignments",
    "possible_values",
    "is_safe",
    "forced_answers",
    "group_count_values",
    "tightest_safe_range",
    "Ledger",
    "Publisher",
    "PublishOutcome",
    "BookingDesk",
    "sample_week1",
    "sample_week2",
    "replay_week1",
    "replay_week2",
    "run_composition_demo",
    "run_late_discovery",
    "run_correction_demo",
    "run_generic_week",
]

__version__ = "1.0.0"
