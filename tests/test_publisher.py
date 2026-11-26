"""Tests for the publishing desk policies."""

import pytest

from privacy_desk.ledger import Ledger
from privacy_desk.model import Constraint, PrivacyError, WeekConfig
from privacy_desk.publisher import Publisher


def cfg_week():
    return WeekConfig(
        week_id="W",
        households=("甲", "乙", "丙"),
        groups={"北翼": ("甲", "乙"), "整层": ("甲", "乙", "丙")},
        protected=("甲", "乙", "丙"),
    )


ANSWERS = {"甲": 1, "乙": 0, "丙": 0}


def make(policy="arrival", reserved=()):
    cfg = cfg_week()
    led = Ledger()
    pub = Publisher(led, cfg, ANSWERS, policy=policy, reserved_groups=reserved)
    return cfg, led, pub


# ---------------------------------------------------------------------------
# Arrival policy: first served keeps exact, later party downgraded.
# ---------------------------------------------------------------------------
def test_arrival_first_exact_second_range():
    cfg, led, pub = make()
    o1 = pub.publish(week="W", group="北翼", requester="租户", want_exact=True)
    assert o1.got_exact and o1.value == 1
    o2 = pub.publish(week="W", group="整层", requester="物业", want_exact=True)
    assert not o2.got_exact and o2.downgraded
    assert (o2.lo, o2.hi) == (1, 2)


def test_arrival_reverse_order():
    cfg, led, pub = make()
    o1 = pub.publish(week="W", group="整层", requester="物业", want_exact=True)
    assert o1.got_exact and o1.value == 1
    o2 = pub.publish(week="W", group="北翼", requester="租户", want_exact=True)
    assert not o2.got_exact and o2.downgraded
    assert (o2.lo, o2.hi) == (0, 1)


def test_published_range_never_blank_when_it_can_be_tight():
    cfg, led, pub = make()
    pub.publish(week="W", group="北翼", requester="租户", want_exact=True)
    o2 = pub.publish(week="W", group="整层", requester="物业", want_exact=True)
    # [1,2] is not blank
    assert not o2.blank
    assert (o2.lo, o2.hi) != (0, 3)


def test_exact_alone_safe_so_not_blank():
    cfg, led, pub = make()
    o = pub.publish(week="W", group="北翼", requester="租户", want_exact=True)
    assert o.got_exact  # must not emit a blank weekly report when exact is safe


def test_force_reference_never_exact():
    cfg, led, pub = make()
    o = pub.publish(week="W", group="北翼", requester="租户", want_exact=True,
                    force_reference=True)
    assert not o.got_exact
    assert o.kind == "range"
    assert o.hi - o.lo >= 1  # never degenerate


# ---------------------------------------------------------------------------
# Pre-agreed policy: reserve exact for one non-overlapping group.
# ---------------------------------------------------------------------------
def test_preagreed_reserve_north():
    cfg, led, pub = make(policy="preagreed", reserved=("北翼",))
    o1 = pub.publish(week="W", group="整层", requester="物业", want_exact=True)
    assert not o1.got_exact  # not reserved -> range
    o2 = pub.publish(week="W", group="北翼", requester="租户", want_exact=True)
    # the earlier non-reserved range must not have spoiled north's exactness
    assert o2.got_exact and o2.value == 1


def test_preagreed_reserve_floor():
    cfg, led, pub = make(policy="preagreed", reserved=("整层",))
    o1 = pub.publish(week="W", group="北翼", requester="租户", want_exact=True)
    assert not o1.got_exact
    o2 = pub.publish(week="W", group="整层", requester="物业", want_exact=True)
    assert o2.got_exact and o2.value == 1


def test_preagreed_rejects_overlapping_reservations():
    cfg = cfg_week()
    with pytest.raises(PrivacyError):
        Publisher(Ledger(), cfg, ANSWERS, policy="preagreed",
                  reserved_groups=("北翼", "整层"))


def test_preagreed_serves_requested_order_regardless_of_reserve():
    """Even if the reserved group is requested second, it still gets exact."""
    cfg, led, pub = make(policy="preagreed", reserved=("北翼",))
    o1 = pub.publish(week="W", group="整层", requester="物业", want_exact=True)
    o2 = pub.publish(week="W", group="北翼", requester="租户", want_exact=True)
    assert o2.got_exact


def test_preagreed_does_not_refuse_range_when_reserved_already_spoiled():
    """If a mailed exact already made the reserved group un-exactable, a
    non-reserved request must still get a safe range, not a refusal."""
    cfg, led, pub = make(policy="preagreed", reserved=("北翼",))
    pub.import_mailed(week="W", group="整层",
                      constraint=Constraint.exact(("甲", "乙", "丙"), 1))
    o = pub.publish(week="W", group="整层", requester="物业", want_exact=True)
    assert not o.refused
    assert o.kind == "range"
    assert pub.detect_breach("W") == {}


# ---------------------------------------------------------------------------
# Safety of every publication.
# ---------------------------------------------------------------------------
def test_all_publications_keep_safety():
    cfg, led, pub = make()
    pub.publish(week="W", group="北翼", requester="租户", want_exact=True)
    pub.publish(week="W", group="整层", requester="物业", want_exact=True)
    assert pub.detect_breach("W") == {}


def test_import_mailed_is_unconditional():
    cfg, led, pub = make()
    # mailing an exact even when it could leak is recorded, not rejected
    r1 = pub.import_mailed(week="W", group="整层",
                           constraint=Constraint.exact(("甲", "乙", "丙"), 1))
    r2 = pub.import_mailed(week="W", group="北翼",
                           constraint=Constraint.exact(("甲", "乙"), 1))
    assert r1.receipt_id and r2.receipt_id
    assert pub.detect_breach("W") == {"丙": 0}


def test_publish_refused_when_state_already_unsafe():
    cfg, led, pub = make()
    pub.import_mailed(week="W", group="整层",
                      constraint=Constraint.exact(("甲", "乙", "丙"), 1))
    pub.import_mailed(week="W", group="北翼",
                      constraint=Constraint.exact(("甲", "乙"), 1))
    o = pub.publish(week="W", group="北翼", requester="租户", want_exact=True)
    assert o.refused


def test_mailed_exact_blocks_north_exact():
    cfg, led, pub = make()
    pub.import_mailed(week="W", group="整层",
                      constraint=Constraint.exact(("甲", "乙", "丙"), 1))
    o = pub.publish(week="W", group="北翼", requester="租户", want_exact=True)
    assert not o.got_exact
    assert o.kind == "range"


def test_invalid_answers_rejected():
    cfg = cfg_week()
    with pytest.raises(PrivacyError):
        Publisher(Ledger(), cfg, {"甲": 1, "乙": 0}, policy="arrival")  # missing 丙
    with pytest.raises(PrivacyError):
        Publisher(Ledger(), cfg, {"甲": 2, "乙": 0, "丙": 0}, policy="arrival")
