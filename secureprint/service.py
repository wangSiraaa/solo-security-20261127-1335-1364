"""Release service: reconstruct in-flight sheets, then sign claim slips safely.

Design contract (see also ``models.py``):

* Accepting an order, printing both faces, and the paper dropping into a locked
  cell are three independent events.  A release is signed only against the
  *current* reconstructed state of each physical sheet -- never against the
  order's claims.
* Face reports and sensor receipts are append-only; the newest report wins.
  A late back-face report may arrive under an older work order (even after a
  shift change), so ownership is per-sheet, not per-order.
* A back face that might carry somebody else's classified content keeps the
  whole sheet out of the current recipient's hands, even if the front is theirs.
* A sheet is "safely available" only if its newest receipt is a locked cell.
  A jam or an open waste tray is an incident: the sheet is quarantined, never
  registered as safely delivered.
* On re-open the ledger answers "who actually received what, by ticket id".
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Sequence

from .errors import (
    AlreadyDelivered,
    AmbiguousOwnership,
    CellOccupied,
    ForeignContent,
    InvalidStateTransition,
    NoCommitment,
    NotAuthorizedRecipient,
    NotComplete,
    ReceiptInvalid,
    RecipientAbsent,
    SameCellConflict,
    SecurityError,
    UnknownCell,
    UnknownSheet,
    UnsafeLocation,
)
from .models import (
    AuditEvent,
    BoxCell,
    ClaimSlip,
    CustodyRecord,
    FaceMark,
    Job,
    LedgerEntry,
    LocKind,
    SensorReceipt,
    Sheet,
    SheetStatus,
    Side,
)
from .store import CustodyStore


@dataclass
class ReleaseOutcome:
    ticket_id: str
    recipient: str
    sheets: List[str]
    cell_ids: List[str]
    in_person: bool
    delivered: bool

    def to_dict(self) -> dict:
        return {
            "ticket_id": self.ticket_id,
            "recipient": self.recipient,
            "sheets": list(self.sheets),
            "cell_ids": list(self.cell_ids),
            "in_person": self.in_person,
            "delivered": self.delivered,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ReleaseService:
    def __init__(self, store: Optional[CustodyStore] = None, clock=None):
        self._store = store
        self._clock = clock or _now
        # in-memory state
        self._seq = 0
        self._jobs: Dict[str, Job] = {}
        self._sheets: Dict[str, Sheet] = {}
        self._cells: Dict[str, BoxCell] = {}
        self._records: Dict[str, CustodyRecord] = {}
        self._tickets: Dict[str, ClaimSlip] = {}
        self._ledger: List[LedgerEntry] = []
        self._audit: List[AuditEvent] = []
        self._ticket_counter = itertools.count(1)
        self._suspend_save = False
        if store is not None:
            self._restore()

    # ===================================================================
    # persistence
    # ===================================================================
    def save(self) -> None:
        if self._store is None or self._suspend_save:
            return
        self._store.save(self._snapshot())

    def suspend_persistence(self) -> None:
        """Stop writing snapshots (used while simulating candidates)."""
        self._suspend_save = True

    def resume_persistence(self) -> None:
        self._suspend_save = False

    def _snapshot(self) -> dict:
        return {
            "seq": self._seq,
            "jobs": {k: v.to_dict() for k, v in self._jobs.items()},
            "sheets": {k: v.to_dict() for k, v in self._sheets.items()},
            "cells": {k: v.to_dict() for k, v in self._cells.items()},
            "records": {k: v.to_dict() for k, v in self._records.items()},
            "tickets": {k: v.to_dict() for k, v in self._tickets.items()},
            "ledger": [e.to_dict() for e in self._ledger],
            "audit": [e.to_dict() for e in self._audit],
            "ticket_counter": self._ticket_counter_value,
        }

    def _restore(self) -> None:
        data = self._store.load()
        if not data:
            return
        self._restore_snapshot(data)

    def _restore_snapshot(self, data: dict) -> None:
        """Rebuild all in-memory state from a snapshot dict (used by restore and
        by the planner's side-effect-free simulations)."""
        self._seq = data.get("seq", 0)
        self._jobs = {k: Job.from_dict(v) for k, v in data.get("jobs", {}).items()}
        self._sheets = {k: Sheet.from_dict(v) for k, v in data.get("sheets", {}).items()}
        self._cells = {k: BoxCell.from_dict(v) for k, v in data.get("cells", {}).items()}
        self._records = {
            k: CustodyRecord.from_dict(v) for k, v in data.get("records", {}).items()
        }
        self._tickets = {
            k: ClaimSlip.from_dict(v) for k, v in data.get("tickets", {}).items()
        }
        self._ledger = [LedgerEntry.from_dict(e) for e in data.get("ledger", [])]
        self._audit = [AuditEvent.from_dict(e) for e in data.get("audit", [])]
        self._ticket_counter = itertools.count(data.get("ticket_counter", 1))

    @property
    def _ticket_counter_value(self) -> int:
        # itertools.count has no peek; derive from the tickets we have instead.
        n = 1
        for tid in self._tickets:
            if tid.startswith("T-"):
                try:
                    n = max(n, int(tid.split("-", 1)[1]) + 1)
                except ValueError:
                    pass
        return n

    # ===================================================================
    # low-level bookkeeping
    # ===================================================================
    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _audit_event(self, kind: str, detail: str, **refs: str) -> AuditEvent:
        ev = AuditEvent(
            seq=self._next_seq(), at=self._clock(), kind=kind, detail=detail, refs=refs
        )
        self._audit.append(ev)
        return ev

    def _next_ticket_id(self) -> str:
        n = next(self._ticket_counter)
        # After a restore the counter may lag behind tickets already on file;
        # never hand out an id that collides with an existing slip.
        while f"T-{n:04d}" in self._tickets:
            n = next(self._ticket_counter)
        return f"T-{n:04d}"

    def _require_sheet(self, serial: str) -> Sheet:
        s = self._sheets.get(serial)
        if s is None:
            raise UnknownSheet(f"no sheet with serial {serial!r}")
        return s

    def _require_cell(self, cell_id: str) -> BoxCell:
        c = self._cells.get(cell_id)
        if c is None:
            raise UnknownCell(f"no pickup cell {cell_id!r}")
        return c

    def _require_record(self, recipient: str) -> CustodyRecord:
        rec = self._records.get(recipient)
        if rec is None or not rec.signed:
            raise NoCommitment(
                f"recipient {recipient!r} has signed no custody commitment"
            )
        return rec

    def _cell_of(self, serial: str) -> Optional[BoxCell]:
        for cell in self._cells.values():
            if cell.sheet_serial == serial:
                return cell
        return None

    # ===================================================================
    # operator-facing setup
    # ===================================================================
    def add_cell(self, cell_id: str, owner: Optional[str] = None) -> BoxCell:
        if cell_id in self._cells:
            raise InvalidStateTransition(f"cell {cell_id!r} already exists")
        cell = BoxCell(cell_id=cell_id, owner=owner)
        self._cells[cell_id] = cell
        self._audit_event("cell.added", f"cell {cell_id}", cell_id=cell_id)
        self.save()
        return cell

    def sign_commitment(
        self, recipient: str, job_id: str, in_person: bool, note: str = ""
    ) -> CustodyRecord:
        rec = CustodyRecord(
            recipient=recipient,
            job_id=job_id,
            signed=True,
            must_collect_in_person=in_person,
            note=note,
        )
        self._records[recipient] = rec
        self._audit_event(
            "commitment.signed",
            f"{recipient} committed to job {job_id} (in_person={in_person})",
            recipient=recipient,
            job_id=job_id,
        )
        self.save()
        return rec

    def submit_job(
        self, job_id: str, recipient: str, pages: int, in_person: bool = True
    ) -> Job:
        """Accept an order.  This does *not* mean any paper exists yet."""
        if job_id in self._jobs:
            raise InvalidStateTransition(f"job {job_id!r} already exists")
        job = Job(
            job_id=job_id,
            recipient=recipient,
            pages=pages,
            in_person=in_person,
            created_seq=self._next_seq(),
        )
        self._jobs[job_id] = job
        self._audit_event(
            "job.submitted",
            f"device accepted {pages}-page job {job_id} for {recipient}",
            job_id=job_id,
            recipient=recipient,
        )
        self.save()
        return job

    # ===================================================================
    # physical events: print faces, sensor receipts, incidents
    # ===================================================================
    def register_sheet(
        self, serial: str, job_id: str, page_no: int, reprint_of: Optional[str] = None
    ) -> Sheet:
        """A physical sheet now exists (still in the printer)."""
        if serial in self._sheets:
            raise InvalidStateTransition(f"sheet {serial!r} already registered")
        if job_id not in self._jobs:
            raise InvalidStateTransition(f"unknown job {job_id!r}")
        sheet = Sheet(
            serial=serial, job_id=job_id, page_no=page_no, reprint_of=reprint_of
        )
        self._sheets[serial] = sheet
        self._jobs[job_id].page_serials.setdefault(page_no, []).append(serial)
        self._audit_event(
            "sheet.registered",
            f"sheet {serial} created for job {job_id} page {page_no}",
            serial=serial,
            job_id=job_id,
        )
        self.save()
        return sheet

    def report_face(
        self,
        serial: str,
        side: Side,
        *,
        job_id: Optional[str] = None,
        candidates: Optional[Iterable[str]] = None,
        station: Optional[str] = None,
        reported_by: Optional[str] = None,
        note: str = "",
    ) -> FaceMark:
        """Append a face-content report (possibly late, possibly from old work)."""
        sheet = self._require_sheet(serial)
        if sheet.status in (SheetStatus.DELIVERED, SheetStatus.DISPOSED):
            raise InvalidStateTransition(
                f"sheet {serial} is {sheet.status.value}; cannot amend faces"
            )
        cand = frozenset(candidates) if candidates is not None else None
        if cand is not None and len(cand) == 0:
            cand = None
        mark = FaceMark(
            serial=serial,
            side=Side(side),
            seq=self._next_seq(),
            candidates=cand,
            job_id=job_id,
            station=station,
            reported_by=reported_by,
            note=note,
        )
        sheet.face_marks.append(mark)
        sheet.touch()
        self._audit_event(
            "face.reported",
            f"sheet {serial} {Side(side).value}: {mark.state.value}"
            + (f" candidates={sorted(cand)}" if cand else ""),
            serial=serial,
            side=Side(side).value,
        )
        self._sweep_stale_slips()
        self.save()
        return mark

    def report_receipt(
        self,
        serial: str,
        kind: LocKind,
        *,
        cell_id: Optional[str] = None,
        station: Optional[str] = None,
        detail: str = "",
        incident: bool = False,
    ) -> SensorReceipt:
        """Append a sensor receipt about where the physical sheet went."""
        sheet = self._require_sheet(serial)
        if sheet.status is SheetStatus.DELIVERED:
            raise InvalidStateTransition(
                f"sheet {serial} is delivered; receipts are frozen"
            )
        kind = LocKind(kind)
        if kind is LocKind.CELL:
            if cell_id is None:
                raise InvalidStateTransition("cell receipt needs a cell_id")
            cell = self._require_cell(cell_id)
            if cell.sheet_serial not in (None, serial):
                raise CellOccupied(
                    f"cell {cell_id} already holds {cell.sheet_serial}"
                )
            if cell.signed_to is not None:
                raise SameCellConflict(
                    f"cell {cell_id} was already signed to {cell.signed_to}"
                )
        rec = SensorReceipt(
            serial=serial,
            kind=kind,
            seq=self._next_seq(),
            cell_id=cell_id,
            station=station,
            detail=detail,
        )
        sheet.receipts.append(rec)
        self._apply_location(sheet, rec, incident=incident)
        sheet.touch()
        self._sweep_stale_slips()
        self.save()
        return rec

    def _apply_location(self, sheet: Sheet, rec: SensorReceipt, incident: bool) -> None:
        """Update status/cell occupancy to match the newest receipt."""
        # free any cell this sheet previously occupied (and drop its lease)
        prev = self._cell_of(sheet.serial)
        if prev is not None:
            prev.sheet_serial = None
            prev.signed_to = None
        sheet.cell_id = None

        if rec.kind is LocKind.CELL:
            cell = self._cells[rec.cell_id]
            cell.sheet_serial = sheet.serial
            sheet.cell_id = cell.cell_id
            sheet.status = SheetStatus.IN_CELL
            self._audit_event(
                "sheet.cell_receipt",
                f"sheet {sheet.serial} locked in cell {cell.cell_id}",
                serial=sheet.serial,
                cell_id=cell.cell_id,
            )
        elif rec.kind in (LocKind.TRAY, LocKind.JAM):
            # An open waste tray or a jam means the sheet is *not* safely
            # delivered.  Quarantine it (isolate in a bag) and log an incident.
            sheet.status = SheetStatus.QUARANTINED
            self._audit_event(
                "incident.quarantine",
                f"sheet {sheet.serial} at {rec.kind.value} -> quarantined"
                + (f" ({rec.detail})" if rec.detail else ""),
                serial=sheet.serial,
            )
        else:  # PRINTER
            sheet.status = SheetStatus.IN_PRINTER
            self._audit_event(
                "sheet.at_printer",
                f"sheet {sheet.serial} still in printer",
                serial=sheet.serial,
            )

    def mark_card_jam(self, serial: str, station: Optional[str] = None, detail: str = "") -> SensorReceipt:
        """Card-jam event: the sheet did not reach a cell; quarantine it."""
        return self.report_receipt(
            serial,
            LocKind.JAM,
            station=station,
            detail=detail or "card jam",
            incident=True,
        )

    def clamp_clear_and_reprint(
        self,
        serial: str,
        new_serial: str,
        *,
        page_no: Optional[int] = None,
        station: Optional[str] = None,
    ) -> Sheet:
        """Clear a jammed sheet and reprint it as a *new* physical sheet."""
        old = self._require_sheet(serial)
        if old.location_kind() is not LocKind.JAM:
            raise InvalidStateTransition(
                f"sheet {serial} is not jammed; refuse to clear-and-reprint"
            )
        if old.status is not SheetStatus.QUARANTINED:
            # ensure quarantine even if the jam receipt was logged as plain
            old.status = SheetStatus.QUARANTINED
        old.status = SheetStatus.DISPOSED
        old.touch()
        self._audit_event(
            "sheet.disposed",
            f"jammed sheet {serial} removed and disposed after clear",
            serial=serial,
        )
        # carry forward known face reports so the reprint starts from them
        new = self.register_sheet(
            new_serial,
            old.job_id,
            page_no if page_no is not None else old.page_no,
            reprint_of=serial,
        )
        for side in (Side.FRONT, Side.BACK):
            m = old.latest_mark(side)
            if m is not None:
                self.report_face(
                    new_serial,
                    side,
                    job_id=m.job_id,
                    candidates=m.candidates,
                    station=station or m.station,
                    reported_by="reprint-carryover",
                    note=f"carried from {serial}",
                )
        self._audit_event(
            "sheet.reprinted",
            f"sheet {new_serial} reprinted for page {new.page_no} from {serial}",
            serial=new_serial,
            reprint_of=serial,
        )
        self.save()
        return new

    # ===================================================================
    # reconstruction queries
    # ===================================================================
    def reconstruct(self, serial: str) -> dict:
        sheet = self._require_sheet(serial)
        owners = sheet.possible_owners()
        return {
            "serial": sheet.serial,
            "job_id": sheet.job_id,
            "page_no": sheet.page_no,
            "status": sheet.status.value,
            "cell_id": sheet.cell_id,
            "location": sheet.location_kind().value,
            "faces": sheet.face_reconstruction(),
            "possible_owners": sorted(owners) if owners is not None else None,
            "confirmed_owner": sheet.confirmed_owner(),
            "safely_available": self._safely_available(sheet),
        }

    def _safely_available(self, sheet: Sheet) -> bool:
        """Safe to register as delivered: locked cell, resolved content, unclaimed.

        A sheet whose faces are unresolved (unknown or ambiguous) is physically
        in a cell but not *safely* available -- it might carry others' content.
        """
        if sheet.status is not SheetStatus.IN_CELL:
            return False
        cell = self._cell_of(sheet.serial)
        if cell is None or cell.signed_to is not None:
            return False
        return sheet.confirmed_owner() is not None

    def _physically_in_cell(self, sheet: Sheet) -> bool:
        return sheet.status is SheetStatus.IN_CELL and self._cell_of(sheet.serial) is not None

    def in_flight(self) -> List[dict]:
        return [
            self.reconstruct(s.serial)
            for s in self._sheets.values()
            if s.status
            in (SheetStatus.IN_PRINTER, SheetStatus.IN_CELL, SheetStatus.QUARANTINED)
        ]

    # ===================================================================
    # release: consent and signing
    # ===================================================================
    def _active_sheets_for(self, recipient: str) -> List[Sheet]:
        """Newest non-disposed sheet for each page of the recipient's job."""
        rec = self._require_record(recipient)
        job = self._jobs.get(rec.job_id)
        if job is None:
            raise InvalidStateTransition(
                f"commitment references unknown job {rec.job_id!r}"
            )
        out: List[Sheet] = []
        for page_no in range(1, job.pages + 1):
            serial = job.active_serial(page_no, self._sheets)
            if serial is None:
                raise NotComplete(
                    f"job {job.job_id}: no live sheet for page {page_no}"
                )
            out.append(self._sheets[serial])
        return out

    def _check_sheet_for_release(self, sheet: Sheet, recipient: str) -> None:
        """Raise unless *this* sheet may go to ``recipient`` right now."""
        # 1) reconstruct what each face can still be carrying, then classify:
        #    - provably someone else's content on any face -> ForeignContent
        #    - a face that is unknown or could be recipient *or* another ->
        #      AmbiguousOwnership (the "may carry others' content" case)
        foreign = False
        unknown = False
        for side in (Side.FRONT, Side.BACK):
            m = sheet.latest_mark(side)
            if m is None or not m.candidates:
                unknown = True
            elif len(m.candidates) == 1:
                if next(iter(m.candidates)) != recipient:
                    foreign = True
            elif recipient not in m.candidates:
                foreign = True  # a face that can only be somebody else's
            else:
                unknown = True  # may be this recipient's or another's
        if foreign:
            owners = sheet.possible_owners()
            raise ForeignContent(
                f"sheet {sheet.serial} carries content that is not "
                f"{recipient}'s (faces {sheet.face_reconstruction()})"
            )
        if unknown:
            owners = sheet.possible_owners()
            raise AmbiguousOwnership(
                f"sheet {sheet.serial} may still carry others' content "
                f"(possible owners={sorted(owners) if owners else 'unknown'}); "
                "quarantine until resolved"
            )
        if sheet.confirmed_owner() != recipient:
            raise NotAuthorizedRecipient(
                f"sheet {sheet.serial} is not confirmed to be {recipient}'s"
            )
        # 2) receipt check: must be in a locked cell, not signed for someone else
        if sheet.status is SheetStatus.DELIVERED:
            raise AlreadyDelivered(f"sheet {sheet.serial} already delivered")
        if sheet.status is not SheetStatus.IN_CELL:
            raise UnsafeLocation(
                f"sheet {sheet.serial} is {sheet.status.value}, not in a locked cell"
            )
        cell = self._cell_of(sheet.serial)
        if cell is None:
            raise UnsafeLocation(f"sheet {sheet.serial} has no cell receipt")
        if cell.signed_to is not None and cell.signed_to != recipient:
            raise SameCellConflict(
                f"cell {cell.cell_id} already signed to {cell.signed_to}"
            )
        # 3) a dedicated cell must belong to the recipient
        if cell.owner is not None and cell.owner != recipient:
            raise NotAuthorizedRecipient(
                f"cell {cell.cell_id} is {cell.owner}'s, not {recipient}'s"
            )

    def _check_complete(self, recipient: str) -> List[Sheet]:
        sheets = self._active_sheets_for(recipient)
        for s in sheets:
            self._check_sheet_for_release(s, recipient)
        return sheets

    # -- claim-slip lifecycle -----------------------------------------------
    def _invalidate_slip(self, slip: ClaimSlip, reason: str) -> None:
        if slip.valid:
            slip.valid = False
            self._audit_event(
                "claim.invalidated",
                f"{slip.ticket_id} invalidated: {reason}",
                ticket_id=slip.ticket_id,
                recipient=slip.recipient,
            )
        # Release any cell this (undelivered) slip had reserved, but only the
        # ones that still point back at this recipient and whose sheet is here.
        if not slip.delivered:
            for cell_id in slip.cell_ids:
                cell = self._cells.get(cell_id)
                if cell is not None and cell.signed_to == slip.recipient:
                    if cell.sheet_serial in slip.sheets:
                        cell.signed_to = None

    def _slip_is_stale(self, slip: ClaimSlip) -> Optional[str]:
        for s in self._ticket_sheets(slip):
            if s.version != slip.signature.get(s.serial):
                return f"sheet {s.serial} changed since signing"
        return None

    def _sweep_stale_slips(self) -> None:
        for slip in list(self._tickets.values()):
            if slip.delivered or not slip.valid:
                continue
            reason = self._slip_is_stale(slip)
            if reason is not None:
                self._invalidate_slip(slip, reason)

    def revoke_ticket(self, ticket_id: str, *, reason: str = "operator revoke") -> None:
        slip = self._tickets.get(ticket_id)
        if slip is None:
            raise ReceiptInvalid(f"no claim slip {ticket_id!r}")
        if slip.delivered:
            raise AlreadyDelivered(f"{ticket_id} already delivered; cannot revoke")
        self._invalidate_slip(slip, reason)
        self.save()

    def prepare_release(
        self, recipient: str, *, present: bool = False
    ) -> ReleaseOutcome:
        """Verify the whole set and sign a claim slip; deliver if in person.

        Signing a slip is *not* the same as handing paper over: a slip reserves
        the sheets/cells for the recipient.  For a set that is not yet complete
        this raises :class:`NotComplete`, so an early partial cannot be signed
        away from the rest of the set.
        """
        rec = self._require_record(recipient)
        sheets = self._check_complete(recipient)
        if rec.must_collect_in_person and not present:
            raise RecipientAbsent(
                f"{recipient} must collect in person and is not present"
            )
        return self._sign(rec, sheets, present=present)

    def sign_claim(self, recipient: str) -> ReleaseOutcome:
        """Sign a claim slip without handing over (recipient not yet present)."""
        rec = self._require_record(recipient)
        sheets = self._check_complete(recipient)
        return self._sign(rec, sheets, present=False)

    def _sign(
        self, rec: CustodyRecord, sheets: Sequence[Sheet], *, present: bool
    ) -> ReleaseOutcome:
        signature = {s.serial: s.version for s in sheets}
        cell_ids = [self._cell_of(s.serial).cell_id for s in sheets]
        wanted_serials = {s.serial for s in sheets}
        wanted_cells = set(cell_ids)
        # A physical sheet may never be signed twice, ever.
        for ledger in self._ledger:
            if wanted_serials & set(ledger.sheets):
                raise SameCellConflict(
                    f"a sheet in this set was already delivered under "
                    f"{ledger.ticket_id}"
                )
        # Cells are reusable *after* a clean hand-over, so the ledger does not
        # block them.  What must never happen is two *live* (undelivered) slips
        # holding the same sheet or cell at the same time.
        for slip in self._tickets.values():
            if slip.delivered or not slip.valid:
                continue
            if wanted_serials & set(slip.sheets) or wanted_cells & set(slip.cell_ids):
                raise SameCellConflict(
                    f"a sheet/cell in this set is already signed to "
                    f"{slip.recipient} under {slip.ticket_id}"
                )

        ticket = ClaimSlip(
            ticket_id=self._next_ticket_id(),
            recipient=rec.recipient,
            sheets=[s.serial for s in sheets],
            cell_ids=cell_ids,
            signature=signature,
            created_at=self._clock(),
        )
        self._tickets[ticket.ticket_id] = ticket
        # Reserve the cells for this recipient until the paper is handed over
        # (or the slip is revoked), so nobody else is signed the same cell.
        for cell_id in cell_ids:
            self._cells[cell_id].signed_to = rec.recipient
        self._audit_event(
            "claim.signed",
            f"{rec.recipient} signed {ticket.ticket_id} for "
            f"{len(sheets)} sheet(s): {', '.join(ticket.sheets)}",
            ticket_id=ticket.ticket_id,
            recipient=rec.recipient,
        )
        delivered = False
        if not rec.must_collect_in_person or present:
            delivered = self._hand_over(ticket)
        self.save()
        return ReleaseOutcome(
            ticket_id=ticket.ticket_id,
            recipient=rec.recipient,
            sheets=list(ticket.sheets),
            cell_ids=list(ticket.cell_ids),
            in_person=rec.must_collect_in_person,
            delivered=delivered,
        )

    # ===================================================================
    # delivery by ticket
    # ===================================================================
    def _ticket_sheets(self, slip: ClaimSlip) -> List[Sheet]:
        return [self._require_sheet(s) for s in slip.sheets]

    def validate_ticket(self, ticket_id: str) -> bool:
        """Would this *old* claim slip still be safe to honour?  (no side effect)"""
        slip = self._tickets.get(ticket_id)
        if slip is None:
            raise ReceiptInvalid(f"no claim slip {ticket_id!r}")
        if not slip.valid or slip.delivered:
            return False
        if self._slip_is_stale(slip) is not None:
            return False
        try:
            for s in self._ticket_sheets(slip):
                self._check_sheet_for_release(s, slip.recipient)
        except SecurityError:
            return False
        return True

    def deliver(self, ticket_id: str, *, present: bool = False) -> ReleaseOutcome:
        slip = self._tickets.get(ticket_id)
        if slip is None:
            raise ReceiptInvalid(f"no claim slip {ticket_id!r}")
        if slip.delivered:
            raise AlreadyDelivered(f"{ticket_id} already delivered")
        if not slip.valid:
            raise ReceiptInvalid(f"{ticket_id} was revoked")
        rec = self._require_record(slip.recipient)
        if rec.must_collect_in_person and not present:
            raise RecipientAbsent(
                f"{slip.recipient} must collect in person and is not present"
            )
        sheets = self._ticket_sheets(slip)
        # re-validate against the *current* physical state
        stale = self._slip_is_stale(slip)
        if stale is not None:
            self._invalidate_slip(slip, stale)
            self.save()
            raise ReceiptInvalid(
                f"{ticket_id} is stale ({stale}); re-run reconstruction and "
                "sign a new slip"
            )
        for s in sheets:
            self._check_sheet_for_release(s, slip.recipient)
        delivered = self._hand_over(slip)
        self.save()
        return ReleaseOutcome(
            ticket_id=slip.ticket_id,
            recipient=slip.recipient,
            sheets=list(slip.sheets),
            cell_ids=list(slip.cell_ids),
            in_person=rec.must_collect_in_person,
            delivered=delivered,
        )

    def _hand_over(self, slip: ClaimSlip) -> bool:
        if slip.delivered:
            return True
        for s in self._ticket_sheets(slip):
            if s.status is SheetStatus.DELIVERED:
                continue
            cell = self._cell_of(s.serial)
            if cell is not None:
                cell.sheet_serial = None
            s.status = SheetStatus.DELIVERED
            s.cell_id = None
        # A cell physically empties when its sheet is handed over, so it becomes
        # reusable.  We keep the *history* in the ledger and audit trail; the
        # concurrent double-sign guard lives in _sign/validate, not here.
        for cell_id in slip.cell_ids:
            cell = self._cells.get(cell_id)
            if cell is not None:
                cell.sheet_serial = None
                cell.signed_to = None
        slip.delivered = True
        slip.delivered_at = self._clock()
        self._ledger.append(
            LedgerEntry(
                ticket_id=slip.ticket_id,
                recipient=slip.recipient,
                sheets=list(slip.sheets),
                cell_ids=list(slip.cell_ids),
                delivered_at=slip.delivered_at,
                signature=dict(slip.signature),
            )
        )
        self._audit_event(
            "claim.delivered",
            f"{slip.recipient} took {len(slip.sheets)} sheet(s) under {slip.ticket_id}",
            ticket_id=slip.ticket_id,
            recipient=slip.recipient,
        )
        return True

    # ===================================================================
    # safe disposal of quarantined sheets
    # ===================================================================
    def dispose_quarantined(self, serial: str, *, clerk: str) -> None:
        sheet = self._require_sheet(serial)
        if sheet.status is not SheetStatus.QUARANTINED:
            raise InvalidStateTransition(
                f"sheet {serial} is {sheet.status.value}, not quarantined"
            )
        sheet.status = SheetStatus.DISPOSED
        sheet.touch()
        self._audit_event(
            "sheet.disposed",
            f"quarantined sheet {serial} disposed by clerk {clerk}",
            serial=serial,
            clerk=clerk,
        )
        self.save()

    # ===================================================================
    # persistence helpers / reporting
    # ===================================================================
    def save_and_close(self) -> None:
        self.save()
        if self._store is not None:
            self._store.close()

    def changeover(self) -> None:
        """End-of-shift: drop volatile memory; a re-open must reload from store."""
        self.save()
        if self._store is not None:
            self._store.close()
        self._seq = 0
        self._jobs, self._sheets, self._cells = {}, {}, {}
        self._records, self._tickets = {}, {}
        self._ledger, self._audit = [], []
        self._ticket_counter = itertools.count(1)

    @classmethod
    def reopen(cls, store: CustodyStore, clock=None) -> "ReleaseService":
        return cls(store=store, clock=clock)

    # ===================================================================
    # audit / ledger accessors
    # ===================================================================
    def cells(self) -> List[dict]:
        return [c.to_dict() for c in self._cells.values()]

    def free_cells(self) -> List[str]:
        return [c.cell_id for c in self._cells.values() if c.is_free()]

    def free_cells_for(self, owner: str) -> List[str]:
        """Free cells a given recipient's paper may use.

        A cell pinned to somebody else's ownership is not usable even when
        empty; generic cells (``owner is None``) and the recipient's own cells
        are.
        """
        return [
            c.cell_id
            for c in self._cells.values()
            if c.is_free()
            and c.signed_to is None
            and (c.owner is None or c.owner == owner)
        ]

    def sheets(self) -> List[dict]:
        return [s.to_dict() for s in self._sheets.values()]

    def sheet(self, serial: str) -> dict:
        return self._require_sheet(serial).to_dict()

    def job(self, job_id: str) -> dict:
        job = self._jobs.get(job_id)
        if job is None:
            raise InvalidStateTransition(f"unknown job {job_id!r}")
        return job.to_dict()

    def active_sheet_for_page(self, recipient: str, page_no: int) -> Optional[dict]:
        rec = self._require_record(recipient)
        job = self._jobs.get(rec.job_id)
        if job is None:
            return None
        serial = job.active_serial(page_no, self._sheets)
        return self.reconstruct(serial) if serial else None

    def commitment(self, recipient: str) -> Optional[dict]:
        rec = self._records.get(recipient)
        return rec.to_dict() if rec else None

    def page_plan(self, recipient: str) -> List[dict]:
        """Per-page inventory for a recipient's committed job.

        Each entry says whether a live physical sheet exists for that page and
        whether it is already *safely* sitting in a locked cell.
        """
        rec = self._require_record(recipient)
        job = self._jobs.get(rec.job_id)
        if job is None:
            return []
        out: List[dict] = []
        for page in range(1, job.pages + 1):
            serial = job.active_serial(page, self._sheets)
            if serial is None:
                out.append(
                    {"page": page, "serial": None, "in_cell": False, "cell_id": None}
                )
                continue
            s = self._sheets[serial]
            out.append(
                {
                    "page": page,
                    "serial": serial,
                    "in_cell": self._physically_in_cell(s),
                    "cell_id": s.cell_id,
                    "releasable": self._safely_available(s),
                }
            )
        return out

    def missing_pages(self, recipient: str) -> List[int]:
        """Pages of the committed job with no live sheet yet."""
        return [p["page"] for p in self.page_plan(recipient) if p["serial"] is None]

    def pages_not_in_cell(self, recipient: str) -> List[int]:
        """Pages whose live sheet is not yet safely in a locked cell."""
        return [p["page"] for p in self.page_plan(recipient) if not p["in_cell"]]

    def cells_held_by(self, recipient: str) -> List[str]:
        out: List[str] = []
        for page in self.page_plan(recipient):
            if page["cell_id"]:
                out.append(page["cell_id"])
        return out

    def can_release(self, recipient: str) -> bool:
        """Whether the whole set is verified, safe and in locked cells right now."""
        try:
            self._check_complete(recipient)
            return True
        except SecurityError:
            return False

    def active_ticket_for(self, recipient: str) -> Optional[dict]:
        """A valid, undelivered claim slip already reserving this recipient's set."""
        for slip in self._tickets.values():
            if slip.recipient == recipient and slip.valid and not slip.delivered:
                return slip.to_dict()
        return None

    def place_confirmed_sheet(
        self,
        serial: str,
        job_id: str,
        page_no: int,
        owner: str,
        cell_id: str,
        *,
        reprint_of: Optional[str] = None,
    ) -> Sheet:
        """Print a new sheet, confirm *both* faces for ``owner``, drop into a cell.

        This is the only path the planner uses to consume the last free cell:
        a sheet whose two faces are already attributed is placed there, exactly
        one per call ("a single face-confirmed new sheet per beat").
        """
        cell = self._require_cell(cell_id)
        if not cell.is_free():
            raise CellOccupied(f"cell {cell_id} is not free")
        if cell.signed_to is not None and cell.signed_to != owner:
            raise SameCellConflict(
                f"cell {cell_id} is leased to {cell.signed_to}, not {owner}"
            )
        if cell.owner is not None and cell.owner != owner:
            raise SameCellConflict(
                f"cell {cell_id} is dedicated to {cell.owner}, not {owner}"
            )
        # A free, unleased, compatible cell takes this paper.  Generic cells
        # (owner is None) stay generic so they can be reused after a hand-over.
        sheet = self.register_sheet(serial, job_id, page_no, reprint_of=reprint_of)
        self.report_face(serial, Side.FRONT, job_id=job_id, candidates=[owner],
                         station=cell_id, reported_by="operator")
        self.report_face(serial, Side.BACK, job_id=job_id, candidates=[owner],
                         station=cell_id, reported_by="operator")
        self.report_receipt(serial, LocKind.CELL, cell_id=cell_id,
                            station=cell_id, detail="placed by operator")
        return sheet

    def audit_log(self) -> List[dict]:
        return [e.to_dict() for e in self._audit]

    def ledger_entries(self) -> List[dict]:
        return [e.to_dict() for e in self._ledger]

    def ticket(self, ticket_id: str) -> dict:
        slip = self._tickets.get(ticket_id)
        if slip is None:
            raise ReceiptInvalid(f"no claim slip {ticket_id!r}")
        return slip.to_dict()

    def who_received(self, ticket_id: str) -> dict:
        """After re-open: exactly who actually received what, by ticket id."""
        for e in self._ledger:
            if e.ticket_id == ticket_id:
                return {
                    "ticket_id": e.ticket_id,
                    "recipient": e.recipient,
                    "sheets": list(e.sheets),
                    "cell_ids": list(e.cell_ids),
                    "delivered_at": e.delivered_at,
                    "delivered": True,
                }
        return {
            "ticket_id": ticket_id,
            "recipient": None,
            "sheets": [],
            "cell_ids": [],
            "delivered_at": None,
            "delivered": False,
        }

    def events_for(self, serial: str) -> List[dict]:
        return [e.to_dict() for e in self._audit if e.refs.get("serial") == serial]
