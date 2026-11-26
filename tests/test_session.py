"""Tests for the reviewer/worker session and persistence round-trip."""

import json

from privacy_desk.model import WeekConfig
from privacy_desk.session import Session


def build():
    cfg = WeekConfig(
        week_id="W1",
        households=("甲", "乙", "丙"),
        groups={"北翼": ("甲", "乙"), "整层": ("甲", "乙", "丙")},
        protected=("甲", "乙", "丙"),
    )
    return Session.from_raw(cfg, {"甲": 1, "乙": 0, "丙": 0})


def test_reviewer_imports_mailed_then_publisher_downgrades():
    s = build()
    s.import_mailed_exact(group="整层", value=1)
    out = s.publish(group="北翼", requester="租户")
    assert not out.got_exact
    assert s.derived() == {}


def test_worker_can_only_book_exact():
    s = build()
    # a safe exact on 北翼 can book
    exact_out = s.publish(group="北翼", requester="租户")
    assert exact_out.got_exact
    bk_ok = s.book(receipt_id=exact_out.receipt_id, requester="租户", slot="入户时段",
                   all_slots=["入户时段"])
    assert bk_ok.booked
    # a reference range on 整层 (requested as reference) cannot book
    range_out = s.publish(group="整层", requester="物业", want_exact=False)
    assert range_out.kind == "range"
    bk_bad = s.book(receipt_id=range_out.receipt_id, requester="物业", slot="入楼检修",
                    all_slots=["入楼检修"])
    assert bk_bad.status == "refused"
    assert s.derived() == {}


def test_slot_occupied_then_reschedule_via_session():
    s = build()
    s.mark_slot_occupied("入户时段-上午")
    out = s.publish(group="北翼", requester="租户")
    bk = s.book(receipt_id=out.receipt_id, requester="租户", slot="入户时段-上午",
                all_slots=["入户时段-上午", "入户时段-下午"])
    assert bk.status == "refused"
    moved = s.desk.reschedule(bk.booking_id, "入户时段-下午")
    assert moved.booked


def test_json_roundtrip_preserves_everything():
    s = build()
    s.import_mailed_exact(group="整层", value=1)
    out = s.publish(group="北翼", requester="租户")
    s.book(receipt_id=out.receipt_id, requester="租户", slot="入户时段",
           all_slots=["入户时段"])
    text = s.to_json()
    restored = Session.from_json(text)
    assert restored.derived() == s.derived()
    assert [r.receipt_id for r in restored.week_receipts()] == \
           [r.receipt_id for r in s.week_receipts()]
    assert len(restored.desk.bookings()) == len(s.desk.bookings())
    # booking counter is restored: a new booking gets a fresh id
    out2 = restored.publish(group="北翼", requester="第二人")
    bk2 = restored.book(receipt_id=out2.receipt_id, requester="第二人", slot="s2")
    assert bk2.booking_id != "BK-001"


def test_report_mentions_derivation_when_leaked():
    s = build()
    s.import_mailed_exact(group="整层", value=1)
    s.import_mailed_exact(group="北翼", value=1)
    rep = s.report()
    assert "DERIVED" in rep and "丙=0" in rep
