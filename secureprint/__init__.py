"""Secret print-room release service (simulation).

Models an independent-lock pigeonhole pickup box for classified print jobs.
The governing rule: *a device accepting an order, a sheet being printed on
both faces, and a sheet dropping into a locked box are three different
events.*  Ownership is therefore never inferred from a stale work order; it is
reconstructed per physical sheet from its serial number, its per-face marks,
and sensor receipts, and a release is only signed against the *current*
verified state of the box.
"""

from .models import (
    Side, FaceMark, Sheet, Job, CustodyRecord, BoxCell, SensorReceipt,
    AuditEvent, ClaimSlip, LedgerEntry, LocKind, SheetStatus, AttributionState,
)
from .errors import (
    SecurityError, UnknownSheet, UnknownCell, AmbiguousOwnership,
    NotAuthorizedRecipient, CellOccupied, InvalidStateTransition,
    ForgedOwnership, SameCellConflict, ForeignContent, UnsafeLocation,
    NotComplete, RecipientAbsent, ReceiptInvalid, AlreadyDelivered,
    NoCommitment, NoFreeCell,
)
from .store import CustodyStore
from .service import ReleaseService, ReleaseOutcome

__all__ = [
    "Side", "FaceMark", "Sheet", "Job", "CustodyRecord", "BoxCell",
    "SensorReceipt", "AuditEvent", "ClaimSlip", "LedgerEntry", "LocKind",
    "SheetStatus", "AttributionState", "SecurityError", "UnknownSheet",
    "UnknownCell", "AmbiguousOwnership", "NotAuthorizedRecipient",
    "CellOccupied", "InvalidStateTransition", "ForgedOwnership",
    "SameCellConflict", "ForeignContent", "UnsafeLocation", "NotComplete",
    "RecipientAbsent", "ReceiptInvalid", "AlreadyDelivered", "NoCommitment",
    "NoFreeCell", "CustodyStore", "ReleaseService", "ReleaseOutcome",
]
