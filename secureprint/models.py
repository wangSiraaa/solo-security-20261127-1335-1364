"""Data model for the secret print-room release service.

The model deliberately keeps three facts *separate*:

* a :class:`Job` is the order the device accepted;
* :class:`FaceMark` records say what was printed on each physical face of a
  sheet (possibly reported late, possibly by a stale work order);
* :class:`SensorReceipt` records say where the physical sheet actually ended up
  (locked cell, jam, or an open waste tray).

Ownership is never read off the job.  It is reconstructed per sheet from the
face marks, and a release additionally requires a verified *locked-cell* receipt.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional


class Side(str, enum.Enum):
    FRONT = "front"
    BACK = "back"


class LocKind(str, enum.Enum):
    PRINTER = "printer"
    CELL = "cell"
    TRAY = "tray"  # open waste tray / quarantine bag
    JAM = "jam"


class SheetStatus(str, enum.Enum):
    IN_PRINTER = "in_printer"
    IN_CELL = "in_cell"
    QUARANTINED = "quarantined"
    DISPOSED = "disposed"
    DELIVERED = "delivered"


class AttributionState(str, enum.Enum):
    UNKNOWN = "unknown"
    AMBIGUOUS = "ambiguous"
    CONFIRMED = "confirmed"


def _enum_value(x):
    return x.value if isinstance(x, enum.Enum) else x


# ---------------------------------------------------------------------------
# Face marks
# ---------------------------------------------------------------------------
@dataclass
class FaceMark:
    """A report about the content printed on one face of one physical sheet.

    ``candidates`` is the set of owners that could still own this face:

    * ``None`` or empty  -> nothing is known yet (could be anybody);
    * one member         -> confirmed owner;
    * two or more        -> ambiguous; the face may carry any of them.
    """

    serial: str
    side: Side
    seq: int
    candidates: Optional[FrozenSet[str]] = None
    job_id: Optional[str] = None
    station: Optional[str] = None
    reported_by: Optional[str] = None
    note: str = ""

    @property
    def state(self) -> AttributionState:
        if not self.candidates:
            return AttributionState.UNKNOWN
        if len(self.candidates) == 1:
            return AttributionState.CONFIRMED
        return AttributionState.AMBIGUOUS

    @property
    def owner(self) -> Optional[str]:
        if self.candidates and len(self.candidates) == 1:
            return next(iter(self.candidates))
        return None

    def to_dict(self) -> dict:
        return {
            "serial": self.serial,
            "side": _enum_value(self.side),
            "seq": self.seq,
            "candidates": sorted(self.candidates) if self.candidates else None,
            "job_id": self.job_id,
            "station": self.station,
            "reported_by": self.reported_by,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FaceMark":
        cand = d.get("candidates")
        return cls(
            serial=d["serial"],
            side=Side(d["side"]),
            seq=d["seq"],
            candidates=frozenset(cand) if cand is not None else None,
            job_id=d.get("job_id"),
            station=d.get("station"),
            reported_by=d.get("reported_by"),
            note=d.get("note", ""),
        )


@dataclass
class SensorReceipt:
    """A receipt from the device/sensors about where a physical sheet went."""

    serial: str
    kind: LocKind
    seq: int
    cell_id: Optional[str] = None
    station: Optional[str] = None
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "serial": self.serial,
            "kind": _enum_value(self.kind),
            "seq": self.seq,
            "cell_id": self.cell_id,
            "station": self.station,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SensorReceipt":
        return cls(
            serial=d["serial"],
            kind=LocKind(d["kind"]),
            seq=d["seq"],
            cell_id=d.get("cell_id"),
            station=d.get("station"),
            detail=d.get("detail", ""),
        )


# ---------------------------------------------------------------------------
# Physical sheet
# ---------------------------------------------------------------------------
@dataclass
class Sheet:
    serial: str
    job_id: str
    page_no: int
    face_marks: List[FaceMark] = field(default_factory=list)
    receipts: List[SensorReceipt] = field(default_factory=list)
    status: SheetStatus = SheetStatus.IN_PRINTER
    cell_id: Optional[str] = None
    reprint_of: Optional[str] = None
    version: int = 0

    # -- marks / receipts ---------------------------------------------------
    def latest_mark(self, side: Side) -> Optional[FaceMark]:
        marks = [m for m in self.face_marks if m.side == side]
        return max(marks, key=lambda m: m.seq) if marks else None

    def latest_receipt(self) -> Optional[SensorReceipt]:
        return max(self.receipts, key=lambda r: r.seq) if self.receipts else None

    def location_kind(self) -> LocKind:
        r = self.latest_receipt()
        return r.kind if r else LocKind.PRINTER

    def touch(self) -> None:
        self.version += 1

    # -- reconstruction -----------------------------------------------------
    def face_reconstruction(self) -> Dict[Side, dict]:
        out: Dict[Side, dict] = {}
        for side in (Side.FRONT, Side.BACK):
            m = self.latest_mark(side)
            if m is None:
                out[side] = {
                    "state": AttributionState.UNKNOWN.value,
                    "candidates": None,
                    "job_id": None,
                    "seq": None,
                }
            else:
                out[side] = {
                    "state": m.state.value,
                    "candidates": sorted(m.candidates) if m.candidates else None,
                    "job_id": m.job_id,
                    "seq": m.seq,
                }
        return out

    def possible_owners(self) -> Optional[FrozenSet[str]]:
        """Everything this sheet could still be carrying.

        ``None`` means at least one face is unknown, i.e. the sheet could be
        carrying anybody's classified content.
        """
        result: set = set()
        for side in (Side.FRONT, Side.BACK):
            m = self.latest_mark(side)
            if m is None or not m.candidates:
                return None
            result |= set(m.candidates)
        return frozenset(result)

    def confirmed_owner(self) -> Optional[str]:
        """The single owner of both faces, or ``None`` if not fully certain."""
        owners = set()
        for side in (Side.FRONT, Side.BACK):
            m = self.latest_mark(side)
            if m is None or m.state is not AttributionState.CONFIRMED:
                return None
            owners.add(m.owner)
        return owners.pop() if len(owners) == 1 else None

    def may_carry_foreign_content(self, recipient: Optional[str] = None) -> bool:
        """True if *any* face could belong to someone other than ``recipient``."""
        for side in (Side.FRONT, Side.BACK):
            m = self.latest_mark(side)
            if m is None or not m.candidates:
                return True
            if recipient is None:
                if len(m.candidates) != 1:
                    return True
            elif m.candidates != frozenset({recipient}):
                return True
        return False

    def to_dict(self) -> dict:
        return {
            "serial": self.serial,
            "job_id": self.job_id,
            "page_no": self.page_no,
            "face_marks": [m.to_dict() for m in self.face_marks],
            "receipts": [r.to_dict() for r in self.receipts],
            "status": self.status.value,
            "cell_id": self.cell_id,
            "reprint_of": self.reprint_of,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Sheet":
        return cls(
            serial=d["serial"],
            job_id=d["job_id"],
            page_no=d["page_no"],
            face_marks=[FaceMark.from_dict(m) for m in d.get("face_marks", [])],
            receipts=[SensorReceipt.from_dict(r) for r in d.get("receipts", [])],
            status=SheetStatus(d.get("status", "in_printer")),
            cell_id=d.get("cell_id"),
            reprint_of=d.get("reprint_of"),
            version=d.get("version", 0),
        )


# ---------------------------------------------------------------------------
# Locked pickup cell
# ---------------------------------------------------------------------------
@dataclass
class BoxCell:
    cell_id: str
    owner: Optional[str] = None
    sheet_serial: Optional[str] = None
    locked: bool = True
    signed_to: Optional[str] = None

    def is_free(self) -> bool:
        return self.sheet_serial is None

    def to_dict(self) -> dict:
        return {
            "cell_id": self.cell_id,
            "owner": self.owner,
            "sheet_serial": self.sheet_serial,
            "locked": self.locked,
            "signed_to": self.signed_to,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BoxCell":
        return cls(
            cell_id=d["cell_id"],
            owner=d.get("owner"),
            sheet_serial=d.get("sheet_serial"),
            locked=d.get("locked", True),
            signed_to=d.get("signed_to"),
        )


# ---------------------------------------------------------------------------
# Job / order
# ---------------------------------------------------------------------------
@dataclass
class Job:
    job_id: str
    recipient: str
    pages: int
    in_person: bool
    created_seq: int
    page_serials: Dict[int, List[str]] = field(default_factory=dict)

    def active_serial(self, page_no: int, sheets: Dict[str, "Sheet"]) -> Optional[str]:
        """The newest live (not disposed, not delivered) sheet for a page."""
        for serial in reversed(self.page_serials.get(page_no, [])):
            s = sheets.get(serial)
            if s is not None and s.status not in (
                SheetStatus.DISPOSED,
                SheetStatus.DELIVERED,
            ):
                return serial
        return None

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "recipient": self.recipient,
            "pages": self.pages,
            "in_person": self.in_person,
            "created_seq": self.created_seq,
            "page_serials": {str(k): v for k, v in self.page_serials.items()},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Job":
        return cls(
            job_id=d["job_id"],
            recipient=d["recipient"],
            pages=d["pages"],
            in_person=d["in_person"],
            created_seq=d["created_seq"],
            page_serials={int(k): list(v) for k, v in d.get("page_serials", {}).items()},
        )


# ---------------------------------------------------------------------------
# Claim slip / ledger / audit
# ---------------------------------------------------------------------------
@dataclass
class ClaimSlip:
    ticket_id: str
    recipient: str
    sheets: List[str]
    cell_ids: List[str]
    signature: Dict[str, int]
    created_at: str
    valid: bool = True
    delivered: bool = False
    delivered_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "ticket_id": self.ticket_id,
            "recipient": self.recipient,
            "sheets": list(self.sheets),
            "cell_ids": list(self.cell_ids),
            "signature": dict(self.signature),
            "created_at": self.created_at,
            "valid": self.valid,
            "delivered": self.delivered,
            "delivered_at": self.delivered_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ClaimSlip":
        return cls(
            ticket_id=d["ticket_id"],
            recipient=d["recipient"],
            sheets=list(d["sheets"]),
            cell_ids=list(d["cell_ids"]),
            signature={k: int(v) for k, v in d["signature"].items()},
            created_at=d["created_at"],
            valid=d.get("valid", True),
            delivered=d.get("delivered", False),
            delivered_at=d.get("delivered_at"),
        )


@dataclass
class LedgerEntry:
    ticket_id: str
    recipient: str
    sheets: List[str]
    cell_ids: List[str]
    delivered_at: str
    signature: Dict[str, int]

    def to_dict(self) -> dict:
        return {
            "ticket_id": self.ticket_id,
            "recipient": self.recipient,
            "sheets": list(self.sheets),
            "cell_ids": list(self.cell_ids),
            "delivered_at": self.delivered_at,
            "signature": dict(self.signature),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LedgerEntry":
        return cls(
            ticket_id=d["ticket_id"],
            recipient=d["recipient"],
            sheets=list(d["sheets"]),
            cell_ids=list(d["cell_ids"]),
            delivered_at=d["delivered_at"],
            signature={k: int(v) for k, v in d["signature"].items()},
        )


@dataclass
class AuditEvent:
    seq: int
    at: str
    kind: str
    detail: str
    refs: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "seq": self.seq,
            "at": self.at,
            "kind": self.kind,
            "detail": self.detail,
            "refs": dict(self.refs),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AuditEvent":
        return cls(
            seq=d["seq"],
            at=d["at"],
            kind=d["kind"],
            detail=d["detail"],
            refs=dict(d.get("refs", {})),
        )


@dataclass
class CustodyRecord:
    """A recipient's signed custody commitment for a job."""

    recipient: str
    job_id: str
    signed: bool
    must_collect_in_person: bool
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "recipient": self.recipient,
            "job_id": self.job_id,
            "signed": self.signed,
            "must_collect_in_person": self.must_collect_in_person,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CustodyRecord":
        return cls(
            recipient=d["recipient"],
            job_id=d["job_id"],
            signed=d["signed"],
            must_collect_in_person=d["must_collect_in_person"],
            note=d.get("note", ""),
        )
