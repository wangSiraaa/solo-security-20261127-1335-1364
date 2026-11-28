"""Hint-issuance desk: two candidate-honest hints per ticket, replayable.

Public surface
--------------
Library            immutable puzzle content (fields, statements, pages, questions)
Engine             the shared issuance rules (used by both routes and the auditor)
Booklet            route A -- precompiled branches for an external hint-giver
InnerOffice        route B -- holds usable material, answers from a receipt live
Station            serves a reader, builds/continues receipts
Verifier           independent replay of a receipt under each hypothesised truth
Curator            intake that vets a new library and reports fulfilment
"""

from .content import ContentError, Field, Library, Page, Question, Statement
from .engine import (
    CHANGED, FINAL, LIVE, PICKUP, REFUSED, SPENT, WITHDRAWN,
    Decision, Delivered, Engine, Step, content_of,
)
from .receipts import Receipt, ReceiptError
from .booklet import Booklet, CannotFulfil
from .desk import InnerOffice
from .station import BookletRouteAdapter, OfficeRouteAdapter, Station
from .verify import ReplayReport, Verifier
from .curator import (
    Curator, FulfilmentReport, PickupCheck, RoundOutcome,
)

__all__ = [
    "ContentError", "Field", "Library", "Page", "Question", "Statement",
    "CHANGED", "FINAL", "LIVE", "PICKUP", "REFUSED", "SPENT", "WITHDRAWN",
    "Decision", "Delivered", "Engine", "Step", "content_of",
    "Receipt", "ReceiptError", "Booklet", "CannotFulfil", "InnerOffice",
    "BookletRouteAdapter", "OfficeRouteAdapter", "Station",
    "ReplayReport", "Verifier",
    "Curator", "FulfilmentReport", "PickupCheck", "RoundOutcome",
]
