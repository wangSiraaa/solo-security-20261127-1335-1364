"""Tests that each worked scenario from the brief behaves as required."""

from privacy_desk.scenarios import (
    PROPERTY,
    TENANT,
    replay_week1,
    replay_week2,
    run_composition_demo,
    run_correction_demo,
    run_generic_week,
    run_late_discovery,
    run_preagreed,
)
from privacy_desk.model import WeekConfig


def test_composition_demo_shows_composition_leak_and_avoids_it():
    r = run_composition_demo()
    # each exact alone leaves 丙 with two possibilities
    assert r.data["bing_alone_north"] == [0, 1]
    assert r.data["bing_alone_floor"] == [0, 1]
    # together they collapse to a single value
    assert r.data["bing_together"] == [0]
    # but the desk prevents that: 丙 keeps two possibilities, nothing derivable
    assert r.data["bing_after_desk"] == [0, 1]
    assert r.data["derived_after_desk"] == {}


# ---------------------------------------------------------------------------
# Week 1: two arrival orders.
# ---------------------------------------------------------------------------
def test_week1_two_orders():
    tenant_first, property_first = replay_week1()
    # tenant first -> tenant exact + booked; property downgraded + no booking
    assert tenant_first.data["who_got_exact"] == [TENANT]
    assert tenant_first.data["who_booked"] == [TENANT]
    # property first -> property exact + booked; tenant downgraded + no booking
    assert property_first.data["who_got_exact"] == [PROPERTY]
    assert property_first.data["who_booked"] == [PROPERTY]
    # either way, nobody is derivable
    assert tenant_first.data["derived"] == {}
    assert property_first.data["derived"] == {}


def test_week1_never_blank_when_exact_possible():
    for r in replay_week1():
        # each replay always has at least one exact
        assert r.data["who_got_exact"]


# ---------------------------------------------------------------------------
# Week 2: three cases.
# ---------------------------------------------------------------------------
def test_week2_three_cases():
    case1, case2, case3 = replay_week2()

    # case 1: no history -> north exact -> booked
    assert case1.data["got_exact"] is True
    assert case1.data["booked"] is True
    assert case1.data["derived"] == {}

    # case 2: mailed floor exact -> north cannot be exact -> not booked
    assert case2.data["got_exact"] is False
    assert case2.data["booked"] is False
    assert case2.data["derived"] == {}

    # case 3: blank history but slot occupied -> report still mailed, booking moved
    assert case3.data["report_mailed"] is True
    assert case3.data["booked"] is True
    assert case3.data["final_booking"]["slot"] == "入户时段-下午"
    assert case3.data["derived"] == {}


# ---------------------------------------------------------------------------
# Late discovery.
# ---------------------------------------------------------------------------
def test_late_discovery_records_derivation():
    r = run_late_discovery()
    assert r.data["cuang_derived"] is True
    assert r.data["derived"]["丙"] == 0
    assert len(r.data["audit_log"]) == 1
    assert r.data["audit_log"][0]["finding"] == "leak"


def test_correction_is_append_only():
    r = run_correction_demo()
    assert r.data["original_still_public"] is True
    assert r.data["receipt_count"] == 2
    assert r.data["origins"] == ["mailed", "correction"]


# ---------------------------------------------------------------------------
# Pre-agreed reservation.
# ---------------------------------------------------------------------------
def test_preagreed_each_reservation_keeps_exact():
    north = run_preagreed("北翼")
    assert north.data["who_got_exact"] == [TENANT]
    assert north.data["derived"] == {}

    floor = run_preagreed("整层")
    assert floor.data["who_got_exact"] == [PROPERTY]
    assert floor.data["derived"] == {}


# ---------------------------------------------------------------------------
# Generic acceptance week (<= 8 households).
# ---------------------------------------------------------------------------
def _generic_cfg():
    # four households, nested + disjoint groups
    return WeekConfig(
        week_id="G",
        households=("a", "b", "c", "d"),
        groups={
            "ab": ("a", "b"),
            "abc": ("a", "b", "c"),
            "cd": ("c", "d"),
        },
        protected=("a", "b", "c", "d"),
    )


def test_generic_week_runs_and_stays_safe():
    cfg = _generic_cfg()
    answers = {"a": 1, "b": 0, "c": 0, "d": 1}
    r = run_generic_week(cfg, answers, [("x", "ab"), ("y", "abc"), ("z", "cd")])
    assert r.data["safe"] is True
    assert r.data["derived"] == {}


def test_generic_week_books_only_exact():
    cfg = _generic_cfg()
    answers = {"a": 1, "b": 0, "c": 0, "d": 1}
    r = run_generic_week(
        cfg, answers,
        [("x", "ab"), ("y", "abc"), ("z", "cd")],
        bookings=[("x", "ab", "s1"), ("y", "abc", "s2"), ("z", "cd", "s3")],
    )
    assert r.data["who_got_exact"] == ["x", "z"]
    assert r.data["who_booked"] == ["x", "z"]
    statuses = {b["requester"]: b["status"] for b in r.data["bookings"]}
    assert statuses["y"] == "refused"  # got a range, cannot book


def test_generic_week_rejects_too_many_households():
    import pytest

    from privacy_desk.model import PrivacyError
    cfg = WeekConfig(
        week_id="G9",
        households=tuple(f"h{i}" for i in range(9)),
        groups={"all": tuple(f"h{i}" for i in range(9))},
    )
    with pytest.raises(PrivacyError):
        run_generic_week(cfg, {h: 0 for h in cfg.households}, [])


def test_generic_week_at_eight_households_ok():
    cfg = WeekConfig(
        week_id="G8",
        households=tuple(f"h{i}" for i in range(8)),
        groups={"all": tuple(f"h{i}" for i in range(8))},
    )
    answers = {h: (i % 2) for i, h in enumerate(cfg.households)}
    r = run_generic_week(cfg, answers, [("x", "all")])
    assert r.data["week"] == "G8"
