"""Repair booking desk.

A work slot may be reserved **only** from a receipt that was actually signed
out with an exact count.  A range (even a tight one, even one the reader
requested) is a reference only and cannot be booked against.  This is the
"租户会凭安全发布的北翼确数约成入户检修" path: a booking succeeds precisely
when a safe exact count exists and the requested slot is free.

The desk is intentionally ignorant of true answers -- it works only from
receipts and slot state.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Set

from .ledger import Ledger
from .model import Booking, BookingError, Receipt, WeekConfig
from .privacy import forced_answers


class BookingDesk:
    def __init__(self, ledger: Ledger, config: WeekConfig) -> None:
        self.ledger = ledger
        self.config = config
        self._bookings: List[Booking] = []
        self._occupied: Dict[str, str] = {}  # slot -> holder label
        self._all_slots: Set[str] = set()
        self._counter = 0

    # -- helpers ----------------------------------------------------------
    def _next_id(self) -> str:
        self._counter += 1
        return f"BK-{self._counter:03d}"

    def occupied_slots(self) -> Set[str]:
        return set(self._occupied)

    def is_slot_free(self, slot: str) -> bool:
        return slot not in self._occupied

    def mark_occupied(self, slot: str, holder: str) -> None:
        """Mark a slot as taken by something outside the desk (e.g. prior work)."""
        self._occupied[slot] = holder

    def look_up(self, receipt_id: str) -> Receipt:
        receipt = self.ledger.get(receipt_id)
        if not receipt.bookable:
            raise BookingError(
                f"receipt {receipt_id!r} is a range, not an exact signed-out count; "
                "a reference cannot book a slot"
            )
        if not receipt.signature:
            raise BookingError(f"receipt {receipt_id!r} is not signed out")
        return receipt

    def slots_available(self) -> List[str]:
        return sorted(s for s in self._all_slots if self.is_slot_free(s))

    # -- booking ----------------------------------------------------------
    def book(
        self,
        *,
        receipt_id: str,
        requester: str,
        slot: str,
        week: str,
        all_slots: Optional[Set[str]] = None,
    ) -> Booking:
        """Attempt to book ``slot`` from the exact receipt ``receipt_id``."""
        if all_slots is not None:
            self._all_slots |= set(all_slots)
        receipt = self.look_up(receipt_id)
        if receipt.week != week:
            raise BookingError(
                f"receipt {receipt_id!r} belongs to week {receipt.week!r}, not {week!r}"
            )

        # If an already-public exact number has since exposed a household, the
        # booking basis itself is compromised; we still record the attempt.
        derived = forced_answers(self.config, self.ledger.constraints_for_week(week))

        if not self.is_slot_free(slot):
            bk = Booking(
                booking_id=self._next_id(),
                requester=requester,
                group=receipt.group,
                receipt_id=receipt_id,
                slot=slot,
                status="refused",
                reason=f"slot {slot!r} already occupied by {self._occupied[slot]}",
                created_at=receipt.issued_at,
            )
            self._bookings.append(bk)
            return bk

        status = "booked"
        reason = f"booked from exact count on group {receipt.group!r}"
        if derived:
            status = "needs_reschedule"
            shown = ", ".join(f"{h}={v}" for h, v in sorted(derived.items()))
            reason = (
                "exact basis is compromised: public info now derives "
                f"{shown}; slot must be re-contracted after re-issue"
            )

        self._occupied[slot] = f"{requester}:{receipt.group}"
        bk = Booking(
            booking_id=self._next_id(),
            requester=requester,
            group=receipt.group,
            receipt_id=receipt_id,
            slot=slot,
            status=status,
            reason=reason,
            created_at=receipt.issued_at,
        )
        self._bookings.append(bk)
        return bk

    def attempt(
        self,
        *,
        receipt_id: str,
        requester: str,
        slot: str,
        week: str,
        all_slots: Optional[Set[str]] = None,
    ) -> Booking:
        """Like :meth:`book`, but a range/reference yields a recorded refusal
        instead of raising."""
        if all_slots is not None:
            self._all_slots |= set(all_slots)
        receipt = self.ledger.get(receipt_id)
        if not receipt.bookable:
            bk = Booking(
                booking_id=self._next_id(),
                requester=requester,
                group=receipt.group,
                receipt_id=receipt_id,
                slot=None,
                status="refused",
                reason="reference only: a truthfully-labelled range cannot book a slot",
                created_at=receipt.issued_at,
            )
            self._bookings.append(bk)
            return bk
        return self.book(
            receipt_id=receipt_id,
            requester=requester,
            slot=slot,
            week=week,
            all_slots=all_slots,
        )

    def reschedule(self, booking_id: str, new_slot: str) -> Booking:
        """Move an existing booking to a free slot (used when a slot is taken)."""
        prior = next((b for b in self._bookings if b.booking_id == booking_id), None)
        if prior is None:
            raise BookingError(f"unknown booking {booking_id!r}")
        if not self.is_slot_free(new_slot):
            raise BookingError(f"slot {new_slot!r} already occupied")
        if prior.slot and prior.slot in self._occupied:
            del self._occupied[prior.slot]
        self._occupied[new_slot] = f"{prior.requester}:{prior.group}"
        moved = Booking(
            booking_id=prior.booking_id,
            requester=prior.requester,
            group=prior.group,
            receipt_id=prior.receipt_id,
            slot=new_slot,
            status="booked",
            reason=f"rescheduled from {prior.slot!r} to {new_slot!r}",
            created_at=prior.created_at,
        )
        idx = self._bookings.index(prior)
        self._bookings[idx] = moved
        return moved

    def bookings(self) -> List[Booking]:
        return list(self._bookings)

    def to_list(self) -> List[dict]:
        return [b.to_dict() for b in self._bookings]
