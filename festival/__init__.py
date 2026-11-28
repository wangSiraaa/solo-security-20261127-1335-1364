"""Offline eligibility wallet for a fictional film festival.

A complete, dependency-free reference for the exercise:

* :mod:`festival.crypto` -- Schnorr signatures over a safe-prime group.
* :mod:`festival.calendar` -- deterministic test calendar.
* :mod:`festival.credential` -- drill identity + per-programme public faces.
* :mod:`festival.preissue` -- path A (finite pre-issued token grid).
* :mod:`festival.assertion` -- path B (trusted local proof service).
* :mod:`festival.wallet` -- holder-side custody of DOB + eligibility.
* :mod:`festival.tickets` -- desk sales, single-holder bundles, reconciliation.
* :mod:`festival.door` -- offline, replay-safe door terminal.
* :mod:`festival.paths` -- path choice and per-show feasibility.
* :mod:`festival.audit` -- what each party retained, and linkage analysis.
"""

from .calendar import TODAY, Program, Screening, age_on, days
from .credential import ExerciseCredential, LocalCredentialIssuer
from .assertion import LocalProofService
from .preissue import PreIssueAuthority
from .wallet import Wallet, NotEligible, EligibilityUnavailable
from .tickets import TicketDesk, MixedHolderError, DeskRejection, IssuedTicket
from .door import DoorTerminal, TicketView
from .paths import PATH_A, PATH_B, plan_program

__all__ = [
    "TODAY", "Program", "Screening", "age_on", "days",
    "ExerciseCredential", "LocalCredentialIssuer",
    "LocalProofService", "PreIssueAuthority",
    "Wallet", "NotEligible", "EligibilityUnavailable",
    "TicketDesk", "MixedHolderError", "DeskRejection", "IssuedTicket",
    "DoorTerminal", "TicketView", "PATH_A", "PATH_B", "plan_program",
]
