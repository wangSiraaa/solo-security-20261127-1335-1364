"""离线回归测试（标准库 unittest，无需联网或第三方依赖）。"""
from __future__ import annotations

import unittest

from dye_archive.archive import (BUDGET_EQH, apply_narrow_bath, build_world,
                                 leadability, restore_t04_receipt)
from dye_archive.color import ciede2000, lab_of
from dye_archive.lab import (DELTA_E_LIMIT, ExternalLab, RecheckOrder,
                             independently_verify, packet_for)
from dye_archive.run_demo import SIGNED_DOSES, eqh_map, make_orders, target_cards
from dye_archive.sealing import GENESIS, verify_envelope


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.world = build_world()

    def test_baseline_only_two_tubes_leadable(self):
        result = {t: leadability(self.world, t) for t in self.world["tubes"]}
        leadable = {t for t, r in result.items() if r["leadable"]}
        self.assertEqual(leadable, {"T-01", "T-02"})

    def test_narrowing_bath_rescues_t03(self):
        wide = leadability(self.world, "T-03")
        self.assertFalse(wide["leadable"])
        apply_narrow_bath(self.world)
        narrow = leadability(self.world, "T-03")
        self.assertTrue(narrow["leadable"])
        self.assertLess(narrow["eq_hours"], wide["eq_hours"])

    def test_t04_mislabeled_then_restored_still_over_budget(self):
        bad = independently_verify(packet_for(self.world, "T-04"))
        self.assertFalse(bad["ok"])
        apply_narrow_bath(self.world)
        restore_t04_receipt(self.world)
        good = independently_verify(packet_for(self.world, "T-04"))
        self.assertTrue(good["ok"])
        final = leadability(self.world, "T-04")
        self.assertFalse(final["leadable"])
        self.assertGreater(final["eq_hours"], BUDGET_EQH)

    def test_tampered_reading_breaks_chain(self):
        env = self.world["probes"].envelopes[5]
        ok, _ = verify_envelope(env, GENESIS if env is self.world["probes"].envelopes[0]
                                else self.world["probes"].envelopes[4]["hash"])
        self.assertTrue(ok)
        env["body"]["record"]["reading_c"] = 42.0
        tip = self.world["probes"].envelopes[4]["hash"]
        ok2, _ = verify_envelope(env, tip)
        self.assertFalse(ok2)

    def test_envelope_covers_gap_and_oot(self):
        from dye_archive.archive import hourly_envelope
        kinds = {r["kind"] for r in hourly_envelope(self.world, "T-05")}
        self.assertIn("gap-clamp", kinds)
        self.assertIn("oot-clamp", kinds)
        self.assertIn("sealed-raw", kinds)


class ColorTests(unittest.TestCase):
    def test_identity_zero(self):
        lab = lab_of({"A": 0.2, "B": 0.3}, {"A": 1.0, "B": 1.0})
        self.assertLess(ciede2000(lab, lab), 1e-12)

    def test_orders_pass_then_step_change_splits_verdict(self):
        world = build_world()
        leadable = ["T-01", "T-02"]
        lab = ExternalLab("T", {"A": 0.02, "B": 0.02})
        lab.receive({t: packet_for(world, t) for t in leadable},
                    eqh_map(world, leadable), target_cards())
        tubes = {"A": "T-01", "B": "T-02"}
        first = make_orders("BL-07")
        for order in first:
            lab.run_order(order, tubes)
            self.assertTrue(all(s["pass"] for s in order.swatches))
            self.assertLessEqual(max(s["delta_e00"] for s in order.swatches),
                                 DELTA_E_LIMIT)

        lab.dispenser.steps_ml["B"] = 0.14
        second = make_orders("BL-07")
        verdicts = []
        for order in second:
            lab.run_order(order, tubes)
            verdicts.append((order.order_id,
                             all(s["pass"] for s in order.swatches)))
        self.assertEqual(dict(verdicts), {"RV-101": True, "RV-102": False})
        # 两张单都按实际混色办结，且共享基液批次被逐笔记录
        self.assertTrue(all(o.status == "closed" for o in second))
        lots = {e["base_lot"] for e in lab.base_ledger}
        self.assertEqual(lots, {"BL-07", "BL-08"})

    def test_lab_rejects_without_verified_tube(self):
        world = build_world()
        lab = ExternalLab("T", {"A": 0.02, "B": 0.02})
        # 未 receive 任何经核源样管
        order = RecheckOrder("X", "f", ["A", "B"], SIGNED_DOSES["RV-101"],
                             target_cards()["CARD-RV-101"], "BL-07")
        lab.run_order(order, {"A": "T-01", "B": "T-02"})
        self.assertEqual(order.status, "rejected")


if __name__ == "__main__":
    unittest.main()
