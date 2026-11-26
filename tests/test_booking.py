"""Tests for the repair booking desk."""

import pytest

from privacy_desk.booking import BookingDesk
from privacy_desk.ledger import Ledger
from privacy_desk.model import BookingError, Constraint, WeekConfig


def setup():
    cfg = WeekConfig(
        week_id="W",
        households=("甲", "乙", "丙"),
        groups={"北翼": ("甲", "乙"), "整层": ("甲", "乙", "丙")},
        protected=("甲", "乙", "丙"),
    )
    led = Ledger()
    exact = led.record(week="W", group="北翼",
                       constraint=Constraint.exact(("甲", "乙"), 1), origin="published")
    rng = led.record(week="W", group="整层",
                     constraint=Constraint.range(("甲", "乙", "丙"), 1, 2), origin="published")
    desk = BookingDesk(led, cfg)
    return cfg, led, desk, exact, rng


def test_exact_receipt_books():
    cfg, led, desk, exact, rng = setup()
    bk = desk.attempt(receipt_id=exact.receipt_id, requester="租户", slot="入户时段",
                      week="W", all_slots={"入户时段"})
    assert bk.booked
    assert bk.slot == "入户时段"


def test_range_receipt_cannot_book():
    cfg, led, desk, exact, rng = setup()
    bk = desk.attempt(receipt_id=rng.receipt_id, requester="物业", slot="入楼检修",
                      week="W", all_slots={"入楼检修"})
    assert bk.status == "refused"
    assert bk.slot is None


def test_look_up_rejects_range():
    cfg, led, desk, exact, rng = setup()
    with pytest.raises(BookingError):
        desk.look_up(rng.receipt_id)


def test_occupied_slot_refused_and_reschedule():
    cfg, led, desk, exact, rng = setup()
    desk.mark_occupied("入户时段-上午", "prior-work")
    bk = desk.attempt(receipt_id=exact.receipt_id, requester="租户",
                      slot="入户时段-上午", week="W",
                      all_slots={"入户时段-上午", "入户时段-下午"})
    assert bk.status == "refused"
    moved = desk.reschedule(bk.booking_id, "入户时段-下午")
    assert moved.booked and moved.slot == "入户时段-下午"


def test_booking_wrong_week_rejected():
    cfg, led, desk, exact, rng = setup()
    with pytest.raises(BookingError):
        desk.book(receipt_id=exact.receipt_id, requester="租户", slot="x",
                  week="OTHER")


def test_booking_from_leaked_basis_needs_reschedule():
    # add a second, conflicting exact so 丙 becomes derivable
    cfg, led, desk, exact, rng = setup()
    led.record(week="W", group="整层",
               constraint=Constraint.exact(("甲", "乙", "丙"), 1), origin="mailed")
    bk = desk.book(receipt_id=exact.receipt_id, requester="租户", slot="入户时段",
                   week="W")
    assert bk.status == "needs_reschedule"


def test_double_book_same_slot_second_refused():
    cfg, led, desk, exact, rng = setup()
    desk.book(receipt_id=exact.receipt_id, requester="A", slot="s", week="W")
    bk2 = desk.book(receipt_id=exact.receipt_id, requester="B", slot="s", week="W")
    assert bk2.status == "refused"
