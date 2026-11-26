"""Domain errors for the secret print-room release service."""


class SecurityError(Exception):
    """Base class for every refusal the service can raise.

    A raise here always means *the paper was not released*.  Callers may treat
    the message as an operator-facing explanation.
    """


class UnknownSheet(SecurityError):
    """No physical sheet with that serial number is known to the service."""


class UnknownCell(SecurityError):
    """No pickup cell with that id exists."""


class NoCommitment(SecurityError):
    """The recipient has signed no custody commitment, so nothing is owed."""


class AmbiguousOwnership(SecurityError):
    """At least one face of the sheet cannot be attributed with certainty.

    The report set still allows more than one owner (or leaves a face unknown),
    so the sheet must not be certified to anybody until the content is resolved.
    """


class ForeignContent(SecurityError):
    """The sheet provably carries content belonging to someone else."""


class UnsafeLocation(SecurityError):
    """The sheet is not inside a locked pickup cell (jam or open waste tray)."""


class NotComplete(SecurityError):
    """Not every sheet required for the recipient's set is safely available."""


class NotAuthorizedRecipient(SecurityError):
    """The recipient is not the verified owner of the sheet(s)."""


class ForgedOwnership(SecurityError):
    """A work order / report tries to assert ownership the faces do not support."""


class RecipientAbsent(SecurityError):
    """In-person collection was requested but the recipient is not present."""


class CellOccupied(SecurityError):
    """The target cell already holds a different sheet."""


class SameCellConflict(SecurityError):
    """A sheet or cell has already been signed for by somebody else."""


class ReceiptInvalid(SecurityError):
    """The claim slip no longer describes a releasable physical state."""


class InvalidStateTransition(SecurityError):
    """The requested operation is not allowed from the current state."""


class AlreadyDelivered(SecurityError):
    """The sheet has already been physically handed over."""


class NoFreeCell(SecurityError):
    """No empty locked cell is available to receive a new sheet."""
