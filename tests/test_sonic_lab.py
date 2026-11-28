"""Unit tests for the synthetic classroom-isolation toolkit.

Run:  python3 -m unittest discover -s tests -v
"""

import math
import os
import struct
import tempfile
import unittest

from sonic_lab import estimate, model, pipeline, schedule, simulator, signals
from sonic_lab.model import (
    BIAS_FIXED_DB, L_MASK_DB, L_PRIV_DB, LEGAL_CONFIGS, N_BEATS, NOISE_DB,
    RESP_ERROR_DB, TAIL_LONG, TAIL_SHORT, TAU_LEAK_DB, TAU_OWN_DB,
    interlock_ok, make_layout, tail_delays,
)


class TestModel(unittest.TestCase):
    def test_illegal_configs_rejected(self):
        with self.assertRaises(ValueError):
            model.partition_loss(0, ("open", "open"))
        # the three legal ones do not raise
        for cfg in LEGAL_CONFIGS:
            model.partition_loss(0, cfg)

    def test_open_board_lowers_isolation_low_bands_most(self):
        for f in range(model.NB):
            loss = model.partition_loss(f, ("open", "sealed"))
            self.assertGreater(loss, 0.0)
        # low band loses more than high band
        self.assertGreater(model.partition_loss(0, ("open", "sealed")),
                           model.partition_loss(5, ("open", "sealed")))

    def test_masked_beats_are_hand_masked_by_other_course(self):
        cw = schedule.design_courseware("x", {"A": 2, "B": 3})
        exc = make_layout(cw.mask_template, 0, cw.priv_a_rel, cw.priv_b_rel,
                          cw.priv_band)
        ok, why = interlock_ok(exc)
        self.assertTrue(ok, why)
        # A's private (with tail) must be inside B's mask and vice versa
        self.assertTrue(model.expand(exc.priv["A"], exc.tail) <= set(exc.mask["B"]))
        self.assertTrue(model.expand(exc.priv["B"], exc.tail) <= set(exc.mask["A"]))


class TestEstimate(unittest.TestCase):
    def test_bound_is_conservative_for_both_response_sets(self):
        for cfg in LEGAL_CONFIGS:
            lower = estimate.iso_lower_bounds(cfg)
            for f in range(model.NB):
                for seat in model.ALL_SEATS:
                    for rs in ("A", "B"):
                        self.assertLessEqual(lower[(f, seat)],
                                             estimate.true_iso(f, seat, cfg, rs),
                                             "lower bound must not exceed truth")

    def test_leak_bound_dominates_direct_plus_tail(self):
        iso = estimate.iso_lower_bounds(("sealed", "sealed"))
        for f in range(model.NB):
            for seat in model.ALL_SEATS:
                up = estimate.leak_upper_db(f, seat, L_PRIV_DB, iso, TAIL_SHORT)
                direct = L_PRIV_DB - iso[(f, seat)]
                self.assertGreaterEqual(up, direct)  # tail adds energy


class TestSimulator(unittest.TestCase):
    def test_mix_is_sample_sum_of_stems(self):
        cw = schedule.design_courseware("x", {"A": 3, "B": 4})
        slot = pipeline.plan_lesson("onsite", band_pairs=[(3, 4)]).slot
        v = slot.verified
        for seat in (slot.seat_a, slot.seat_b):
            s = v["seats"][seat]
            stems = [k for k in s if k != "mix"]
            for i in range(0, len(s["mix"]), 997):
                tot = sum(s[k][i] for k in stems)
                self.assertAlmostEqual(s["mix"][i], tot, places=9)

    def test_private_power_independent_of_band(self):
        # Time-domain mean square of a private pulse beat ~= L_PRIV (0 dB).
        cw = schedule.design_courseware("x", {"A": 3, "B": 4})
        exc = make_layout(cw.mask_template, 0, cw.priv_a_rel, cw.priv_b_rel,
                          cw.priv_band)
        plan = simulator.make_plan("A", "A1", exc)
        slot = pipeline.plan_lesson("onsite", band_pairs=[(3, 4)]).slot
        s = slot.verified["seats"][slot.seat_a]
        beats = [k for k, r in plan.roles.items() if r == "priv"]
        ms = signals.mean_square_beats(s["own_priv"], simulator.BEATLEN, beats)
        self.assertAlmostEqual(signals.db_from_pow(ms), L_PRIV_DB, delta=0.7)


class TestScheduling(unittest.TestCase):
    def test_fixed_plan_sealed_and_wide_margin(self):
        p = pipeline.plan_lesson("fixed")
        self.assertIsNotNone(p.slot)
        self.assertEqual(tuple(p.slot.config), ("sealed", "sealed"))
        self.assertGreaterEqual(p.slot.predicted_margin,
                                model.FIXED_UNCERTAINTY_DB)
        self.assertTrue(p.slot.verified["ok"])

    def test_onsite_handles_narrow_low_band_course_fixed_does_not(self):
        bp = (0, 3)   # low band, narrow margin
        onsite = pipeline.plan_lesson("onsite", band_pairs=[bp])
        fixed = pipeline.plan_lesson("fixed", band_pairs=[bp])
        self.assertIsNotNone(onsite.slot)
        self.assertTrue(onsite.slot.verified["ok"])
        self.assertIsNone(fixed.slot)   # fixed headroom rejects it

    def test_narrow_margin_smaller_than_wide(self):
        narrow = pipeline.plan_lesson("onsite", band_pairs=[(0, 3)])
        wide = pipeline.plan_lesson("fixed", band_pairs=[(4, 5)])
        self.assertLess(narrow.slot.predicted_margin, wide.slot.predicted_margin)


class TestPerturbations(unittest.TestCase):
    def test_tail_lengthening_adds_a_beat(self):
        base = pipeline.plan_lesson("onsite", band_pairs=[(3, 4)])
        out = pipeline.perturb_tail(base, band_pairs=[(3, 4)])
        self.assertEqual(out["old_required_gap_beats"], 2)
        self.assertEqual(out["new_required_gap_beats"], 3)
        self.assertTrue(out["early_class_needs_extra_beat"])
        self.assertTrue(out["still_feasible"])
        self.assertTrue(out["new_plan"]["contract_ok"])

    def test_seat_change_recertifies_audio(self):
        # wide course: new near seat still OK, same layout preserved
        base = pipeline.plan_lesson("onsite", band_pairs=[(4, 5)])
        out = pipeline.perturb_seat(base, "B2")
        self.assertTrue(out["same_beat_layout"])
        self.assertIn("note", out)

    def test_seat_change_can_fail_for_narrow_course(self):
        base = pipeline.plan_lesson("onsite", band_pairs=[(0, 3)])
        out = pipeline.perturb_seat(base, "B2")
        # moving B to the near seat breaks the leak contract for a low-band class
        self.assertFalse(out["original_slot_still_ok"])
        self.assertFalse(out["ok_leak"])
        self.assertIn("re-certified", out["note"])

    def test_two_coursewares_share_public_differ_private(self):
        # same public template, different private pattern (band + beats)
        cw1 = schedule.design_courseware("c1", {"A": 3, "B": 4})
        cw2 = schedule.design_courseware("c2", {"A": 3, "B": 4})
        self.assertEqual(cw1.mask_template, cw2.mask_template)
        # private bands differ between the two courses within each courseware
        self.assertNotEqual(cw1.priv_band["A"], cw1.priv_band["B"])
        # two coursewares with different band choice -> different private band
        cw3 = schedule.design_courseware("c3", {"A": 2, "B": 5})
        self.assertNotEqual(cw1.priv_band["A"], cw3.priv_band["A"])


class TestAudio(unittest.TestCase):
    def test_tracks_written_and_readable(self):
        p = pipeline.plan_lesson("onsite", band_pairs=[(4, 5)])
        with tempfile.TemporaryDirectory() as d:
            paths = pipeline.render_tracks(p.slot, d)
            self.assertEqual(set(paths), {"A", "B"})
            for c, path in paths.items():
                self.assertTrue(os.path.exists(path))
                x = signals.read_wav(path)
                self.assertGreater(len(x), 0)
                self.assertLessEqual(max(abs(v) for v in x), 1.0)
                # not silent
                self.assertGreater(max(abs(v) for v in x), 0.1)


if __name__ == "__main__":
    unittest.main()
