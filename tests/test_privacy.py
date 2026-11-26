"""Tests for the privacy engine: admissibility, safety, range search."""

import itertools

import pytest

from privacy_desk.model import Constraint, PrivacyError, WeekConfig
from privacy_desk.privacy import (
    admissible_assignments,
    all_possible_values,
    candidate_ranges,
    exact_is_safe,
    forced_answers,
    group_count_values,
    is_safe,
    possible_values,
    range_is_blank,
    tightest_safe_range,
)


def floor_config():
    return WeekConfig(
        week_id="W",
        households=("甲", "乙", "丙"),
        groups={"北翼": ("甲", "乙"), "整层": ("甲", "乙", "丙")},
        protected=("甲", "乙", "丙"),
    )


# ---------------------------------------------------------------------------
# The headline example: two exact counts compose into a leak.
# ---------------------------------------------------------------------------
def test_two_exact_counts_leak_bing():
    cfg = floor_config()
    north = Constraint.exact(("甲", "乙"), 1)
    floor = Constraint.exact(("甲", "乙", "丙"), 1)
    assert not is_safe(cfg, [north, floor])
    forced = forced_answers(cfg, [north, floor])
    assert forced == {"丙": 0}


def test_each_exact_alone_is_safe():
    cfg = floor_config()
    north = Constraint.exact(("甲", "乙"), 1)
    floor = Constraint.exact(("甲", "乙", "丙"), 1)
    assert is_safe(cfg, [north])
    assert is_safe(cfg, [floor])
    # either exact alone hides which of 甲/乙 complained
    assert possible_values(cfg, [north], "甲") == {0, 1}
    assert possible_values(cfg, [north], "乙") == {0, 1}


def test_blank_range_is_safe_and_uninformative():
    cfg = floor_config()
    blank = Constraint.range(("甲", "乙"), 0, 2)
    assert range_is_blank(cfg, "北翼", blank)
    assert is_safe(cfg, [blank])
    assert possible_values(cfg, [blank], "甲") == {0, 1}


def test_tightest_safe_range_after_exact():
    cfg = floor_config()
    north = Constraint.exact(("甲", "乙"), 1)
    rng = tightest_safe_range(cfg, [north], "整层", 1, min_width=1)
    # must exclude lo==hi==1 (exact in disguise); [1,2] is the tightest valid
    assert (rng.lo, rng.hi) == (1, 2)
    assert is_safe(cfg, [north, rng])


def test_min_width_forbids_degenerate_range():
    cfg = floor_config()
    # With no prior public info, a lone exact on 整层 (count=1) does NOT force
    # any individual (each of 甲/乙/丙 can still be 0 or 1), so the degenerate
    # range [1,1] is "safe" -- but the publisher must never emit it as a
    # *reference*, hence min_width=1.
    rng0 = tightest_safe_range(cfg, [], "整层", 1, min_width=0)
    assert (rng0.lo, rng0.hi) == (1, 1)
    rng1 = tightest_safe_range(cfg, [], "整层", 1, min_width=1)
    assert (rng1.lo, rng1.hi) == (0, 1)
    assert rng1.hi - rng1.lo >= 1


def test_degenerate_range_unsafe_after_north_exact():
    cfg = floor_config()
    north = Constraint.exact(("甲", "乙"), 1)
    # [1,1] on 整层 would derive 丙=0, so it is rejected regardless of min_width
    deg = Constraint.range(("甲", "乙", "丙"), 1, 1)
    assert not is_safe(cfg, [north, deg])
    rng = tightest_safe_range(cfg, [north], "整层", 1, min_width=0)
    assert (rng.lo, rng.hi) == (1, 2)


def test_exact_is_safe_matches_is_safe():
    cfg = floor_config()
    north = Constraint.exact(("甲", "乙"), 1)
    assert exact_is_safe(cfg, [], "北翼", 1) is True
    assert exact_is_safe(cfg, [north], "整层", 1) is False


# ---------------------------------------------------------------------------
# Admissible set / safety invariants across many random-ish constraint sets.
# ---------------------------------------------------------------------------
def _all_binary(cfg):
    for bits in itertools.product((0, 1), repeat=len(cfg.households)):
        yield dict(zip(cfg.households, bits))


def test_admissible_matches_bruteforce():
    cfg = floor_config()
    cons = [Constraint.exact(("甲", "乙"), 1)]
    adm = admissible_assignments(cfg, cons)
    expected = [a for a in _all_binary(cfg) if all(c.holds(a) for c in cons)]
    assert adm == expected


def test_empty_admissible_is_not_safe():
    cfg = floor_config()
    # contradictory: 北翼 must be 2 but 整层 must be 0
    cons = [Constraint.exact(("甲", "乙"), 2), Constraint.exact(("甲", "乙", "丙"), 0)]
    assert admissible_assignments(cfg, cons) == []
    assert not is_safe(cfg, cons)


def test_group_count_values():
    cfg = floor_config()
    north = Constraint.exact(("甲", "乙"), 1)
    # with north exact=1, 整层 count (甲+乙+丙) can be 1 or 2
    assert group_count_values(cfg, [north], "整层") == {1, 2}


def test_candidate_ranges_contain_truth_and_sorted():
    cfg = floor_config()
    cands = candidate_ranges(cfg, "北翼", 1)
    assert all(lo <= 1 <= hi for lo, hi in cands)
    widths = [hi - lo for lo, hi in cands]
    assert widths == sorted(widths)


def test_possible_values_unknown_household():
    cfg = floor_config()
    with pytest.raises(PrivacyError):
        possible_values(cfg, [], "丁")


def test_all_possible_values_covers_protected():
    cfg = floor_config()
    vals = all_possible_values(cfg, [])
    assert set(vals) == {"甲", "乙", "丙"}
    assert all(v == {0, 1} for v in vals.values())


def test_constraint_validation():
    with pytest.raises(PrivacyError):
        Constraint.exact(("甲",), 2)  # value > count
    with pytest.raises(PrivacyError):
        Constraint.range(("甲", "乙"), 2, 1)  # lo > hi
    with pytest.raises(PrivacyError):
        Constraint(("甲", "甲"), "exact", value=1)  # duplicate covers
    with pytest.raises(PrivacyError):
        Constraint(("甲",), "bogus", value=1)
