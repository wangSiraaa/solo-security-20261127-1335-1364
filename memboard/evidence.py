"""Evidence receipts produced by the firmware / board inspector.

Only fictional addresses and local observations are modelled.  Every piece of
evidence carries the firmware *generation* it was observed under, which is what
lets us later decide whether a correction still points at the same physical
row after a firmware revision.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .geometry import REGION_PRIMARY, RowAddr


@dataclass(frozen=True)
class CorrectionReceipt:
    """A corrected (or uncorrectable) read-back event."""

    receipt_id: str
    board: str
    logical_page: int
    channel: int
    generation: int
    syndrome: str = "CE"  # CE = corrected, UE = uncorrectable
    observed_at: float = 0.0
    note: str = ""

    def fingerprint(self) -> tuple:
        # A receipt identity for *dedup* purposes.  The receipt id and wall
        # clock are intentionally excluded: re-reporting the same observation
        # must not create a second independent anomaly.
        return (
            self.board,
            self.logical_page,
            self.channel,
            self.generation,
            self.syndrome,
        )

    def __str__(self) -> str:  # pragma: no cover - convenience
        return (
            f"{self.receipt_id}(pg{self.logical_page}/ch{self.channel}/g{self.generation}"
            f"/{self.syndrome}@{self.observed_at:g})"
        )


@dataclass(frozen=True)
class RetiredRow:
    """A physical primary row already taken out of service in the past."""

    board: str
    channel: int
    index: int
    generation: int
    retired_at: float = 0.0
    reason: str = ""

    def row(self) -> RowAddr:
        return RowAddr(self.board, self.channel, REGION_PRIMARY, self.index)


@dataclass(frozen=True)
class AccessReceipt:
    """A normal read or write back-receipt (positive or negative evidence)."""

    receipt_id: str
    board: str
    logical_page: int
    channel: int
    generation: int
    kind: str = "read"  # "read" | "write"
    ok: bool = True
    observed_at: float = 0.0

    def fingerprint(self) -> tuple:
        return (
            self.board,
            self.logical_page,
            self.channel,
            self.generation,
            self.kind,
            self.ok,
        )


@dataclass
class EvidenceBundle:
    """Everything the inspection console is handed."""

    corrections: list[CorrectionReceipt] = field(default_factory=list)
    retired: list[RetiredRow] = field(default_factory=list)
    accesses: list[AccessReceipt] = field(default_factory=list)

    def with_correction(self, receipt: CorrectionReceipt) -> "EvidenceBundle":
        return EvidenceBundle(
            corrections=[*self.corrections, receipt],
            retired=list(self.retired),
            accesses=list(self.accesses),
        )

    def with_access(self, receipt: AccessReceipt) -> "EvidenceBundle":
        return EvidenceBundle(
            corrections=list(self.corrections),
            retired=list(self.retired),
            accesses=[*self.accesses, receipt],
        )

    def replace_correction(self, index: int, receipt: CorrectionReceipt) -> "EvidenceBundle":
        corrections = list(self.corrections)
        corrections[index] = receipt
        return EvidenceBundle(corrections, list(self.retired), list(self.accesses))

    def all_receipt_ids(self) -> list[str]:
        return [
            *(r.receipt_id for r in self.corrections),
            *(r.receipt_id for r in self.accesses),
        ]
