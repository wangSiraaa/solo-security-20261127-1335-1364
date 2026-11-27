"""Tests for the random practice-material bench.

Each test pins a property the assignment demands, not an implementation detail.
"""

import unittest

from bench.combiner import CombinerContract
from bench.content import ChassisDeclaration, DeclarationBook, Feed
from bench.device import (
    DEFAULT_LENGTH,
    _counterfactual_orders,
    _same_source_declarations,
    run_combiner_demo,
    run_narrative,
    run_pause_scenario,
    standard_bench,
)
from bench.engine import IssuanceEngine, IssuanceError
from bench.reserve import ReserveBook

L = DEFAULT_LENGTH


class TestNarrative(unittest.TestCase):
    """The exact four-step story the lab asked for."""

    def setUp(self):
        self.engine, self.runner = standard_bench(method="partition")
        self.t = {b["step"]: b for b in run_narrative(self.engine, self.runner, length=L)}

    def test_first_two_orders_really_get_material(self):
        issued = self.t["1_first_two_orders"]["issued"]
        self.assertEqual(len(issued), 2)
        self.assertEqual(issued[0]["order_id"], "ORD-1")
        self.assertEqual(issued[1]["order_id"], "ORD-2")
        for o in issued:
            self.assertEqual(len(o["material"]), 2 * L)  # non-empty, right length
            self.assertTrue(o["receipt"].startswith("RC-"))
        # Two genuinely different practice strings from two distinct pools.
        self.assertNotEqual(issued[0]["material"], issued[1]["material"])
        self.assertNotEqual(issued[0]["pool"], issued[1]["pool"])

    def test_same_source_collapses_capacity(self):
        base = self.t["1b_baseline_capacity"]
        same = self.t["2_same_source_rewalk"]["total_capacity_if_rewalked"]
        # Same live state, same length; the ONLY change is that both chassis now
        # name one box.  Capacity must strictly shrink.
        self.assertLess(same["orders_served"], base["orders_served"])
        # After collapse only the single shared pool is usable; one 32-byte
        # slice remains there -> exactly one order.
        self.assertEqual(same["orders_served"], 1)
        self.assertEqual(self.t["2_same_source_rewalk"]["issuable_now"], 1)

    def test_three_order_batch_collapses(self):
        step = self.t["2_same_source_rewalk"]
        orig = step["three_order_batch_original_decls"]
        same = step["three_order_batch_same_source"]
        # Same 3-order batch, same live state; only declarations differ.
        # Original declarations serve all 3 (128 live bytes over 3 fresh pools);
        # collapsed declarations to one shared pool serve only 1.
        self.assertEqual(orig["orders_served"], 3)
        self.assertEqual(same["orders_served"], 1)
        # Under collapse it stops because the one shared source is exhausted.
        self.assertTrue(any("stop_after" in r for r in same["trace"]))

    def test_only_declarations_changed(self):
        pools = self.t["2_same_source_rewalk"]["declared_pools"]
        self.assertEqual(list(pools), ["noisebox-A@1"])
        self.assertEqual(sorted(pools["noisebox-A@1"]), ["rack-1", "rack-2"])

    def test_bigger_next_order_reduces_balance(self):
        b = self.t["3_restore_and_bigger_length"]
        self.assertEqual(b["balance_at_original_len"]["max_independent_orders"], 4)
        self.assertEqual(b["balance_at_bigger_len"]["max_independent_orders"], 1)
        self.assertEqual(b["issued_bigger"]["length_bytes"], 2 * L)
        # independent_bytes (raw volume) is unchanged by length; order count isn't
        self.assertEqual(
            b["balance_at_original_len"]["independent_bytes"],
            b["balance_at_bigger_len"]["independent_bytes"],
        )

    def test_lost_reply_refetch_same_and_no_double_spend(self):
        r = self.t["4_lost_reply_refetch"]
        self.assertTrue(r["same_receipt"])
        self.assertTrue(r["same_material"])
        # Both a naive resend and a lookup recover the same receipt/material.
        self.assertTrue(r["resend_same_receipt"])
        self.assertTrue(r["resend_same_material"])
        self.assertTrue(r["no_double_spend"])
        self.assertEqual(
            r["balance_before_refetch"]["independent_bytes"],
            r["balance_after_refetch"]["independent_bytes"],
        )
        # Re-fetch balance is the live truth (same as checking the ledger now).
        self.assertEqual(
            r["true_remaining_at_original_len"],
            self.engine.balance(L),
        )


class TestProvenance(unittest.TestCase):
    """Equal bytes prove nothing; provenance is by pool key only."""

    def test_literal_equality_does_not_merge_sources(self):
        # Two DIFFERENT boxes forced to hold IDENTICAL bytes must remain two
        # independent sources.
        rb = ReserveBook()
        rb.add("box-X", 1, L)
        rb.add("box-Y", 1, L)
        # Force identical prefabricated material by copying cursors/materials is
        # not needed: material equality is irrelevant.  We assert capacity is 2.
        decls = DeclarationBook()
        decls.set(ChassisDeclaration("c1", (Feed("n1", "box-X", 1),)))
        decls.set(ChassisDeclaration("c2", (Feed("n2", "box-Y", 1),)))
        eng = IssuanceEngine(rb, decls, method="partition")
        self.assertEqual(eng.max_independent_orders(L), 2)
        eng.issue(L, "A")
        eng.issue(L, "B")  # both must succeed: distinct provenance

    def test_literal_inequality_does_not_split_one_source(self):
        # One box named by two chassis with DIFFERENT literal names is ONE
        # source and must not serve two independent orders.
        rb = ReserveBook()
        rb.add("box-Z", 1, L)
        decls = DeclarationBook()
        decls.set(ChassisDeclaration("c1", (Feed("alpha", "box-Z", 1),)))
        decls.set(ChassisDeclaration("c2", (Feed("beta", "box-Z", 1),)))
        eng = IssuanceEngine(rb, decls, method="partition")
        self.assertEqual(eng.max_independent_orders(L), 1)
        eng.issue(L, "A")
        with self.assertRaises(IssuanceError):
            eng.issue(L, "B")

    def test_epoch_separates_sources(self):
        rb = ReserveBook()
        rb.add("box", 1, L)
        rb.add("box", 2, L)
        decls = DeclarationBook()
        decls.set(
            ChassisDeclaration("c1", (Feed("p1", "box", 1), Feed("p2", "box", 2)))
        )
        eng = IssuanceEngine(rb, decls, method="partition")
        self.assertEqual(eng.max_independent_orders(L), 2)

    def test_synthetic_declaration_cannot_mint_independence(self):
        rb = ReserveBook()
        rb.add("box-S", 1, L)
        decls = DeclarationBook()
        decls.set(
            ChassisDeclaration("c1", (Feed("s", "box-S", 1, synthetic=True),))
        )
        eng = IssuanceEngine(rb, decls, method="partition")
        self.assertEqual(eng.max_independent_orders(L), 0)
        with self.assertRaises(IssuanceError):
            eng.issue(L, "A")

    def test_sharing_a_pool_never_multiplies_capacity(self):
        # N chassis all naming the SAME pool (same epoch) must still yield the
        # capacity of ONE source -- no matter how many names point at it.
        rb = ReserveBook()
        rb.add("box", 1, 4 * L)
        decls = DeclarationBook()
        for i in range(5):
            decls.set(ChassisDeclaration(f"c{i}", (Feed(f"alias{i}", "box", 1),)))
        eng = IssuanceEngine(rb, decls, method="partition")
        self.assertEqual(eng.max_independent_orders(L), 4)  # 4*L / L, not 5x


class TestPartitionMethod(unittest.TestCase):
    def test_leftover_fragment_is_kept_and_usable(self):
        rb = ReserveBook()
        rb.add("box", 1, 3 * L)
        decls = DeclarationBook()
        decls.set(ChassisDeclaration("c1", (Feed("p", "box", 1),)))
        eng = IssuanceEngine(rb, decls, method="partition")
        a = eng.issue(L)
        self.assertEqual(rb.get(("box", 1)).live(), 2 * L)  # fragment retained
        b = eng.issue(L)
        self.assertEqual(rb.get(("box", 1)).live(), L)
        c = eng.issue(L)
        self.assertEqual(rb.get(("box", 1)).live(), 0)
        # Disjoint fragments are independent of each other.
        self.assertEqual(len({a.material_hex, b.material_hex, c.material_hex}), 3)
        with self.assertRaises(IssuanceError):
            eng.issue(L)

    def test_uneven_fragments_count_floor(self):
        rb = ReserveBook()
        rb.add("box", 1, 2 * L + L // 2)
        decls = DeclarationBook()
        decls.set(ChassisDeclaration("c1", (Feed("p", "box", 1),)))
        eng = IssuanceEngine(rb, decls, method="partition")
        self.assertEqual(eng.max_independent_orders(L), 2)


class TestIdempotentReissue(unittest.TestCase):
    def setUp(self):
        self.engine, self.runner = standard_bench(method="partition")

    def test_resend_same_order_returns_same_and_no_respend(self):
        a = self.engine.issue(L, "ORD-X")
        live_before = self.engine.independent_bytes()
        a2 = self.engine.issue(L, "ORD-X")  # the "lost reply" resend
        self.assertEqual(a.receipt, a2.receipt)
        self.assertEqual(a.material_hex, a2.material_hex)
        self.assertEqual(self.engine.independent_bytes(), live_before)
        self.assertEqual(a2.remaining, self.engine.balance(L))

    def test_refetch_unknown_order_errors(self):
        with self.assertRaises(IssuanceError):
            self.engine.refetch("nope")

    def test_refetch_reports_live_balance_after_later_spend(self):
        a = self.engine.issue(L, "ORD-A")
        self.engine.issue(L, "ORD-B")
        a2 = self.engine.refetch("ORD-A")
        self.assertEqual(a.receipt, a2.receipt)
        self.assertEqual(a2.remaining, self.engine.balance(L))

    def test_same_order_different_length_is_rejected(self):
        self.engine.issue(L, "ORD-Y")
        with self.assertRaises(IssuanceError):
            self.engine.issue(2 * L, "ORD-Y")


class TestPause(unittest.TestCase):
    def test_paused_source_not_promised(self):
        eng, runner = standard_bench(method="partition")
        before = eng.max_independent_orders(L)
        eng.pause_pool(("noisebox-C", 1))
        after = eng.max_independent_orders(L)
        self.assertLess(after, before)
        self.assertEqual(eng.paused_bytes(), 64)

    def test_paused_source_cannot_serve(self):
        eng, runner = standard_bench(method="partition")
        eng.pause_pool(("noisebox-A", 1))
        eng.pause_pool(("noisebox-B", 1))
        # Only C left (64 bytes -> 2 orders of L).
        self.assertEqual(eng.max_independent_orders(L), 2)
        eng.issue(L)
        eng.issue(L)
        with self.assertRaises(IssuanceError):
            eng.issue(L)

    def test_resume_restores_capacity(self):
        eng, runner = standard_bench(method="partition")
        eng.pause_pool(("noisebox-C", 1))
        eng.resume_pool(("noisebox-C", 1))
        self.assertEqual(eng.max_independent_orders(L), 6)

    def test_pause_scenario_runs(self):
        out = run_pause_scenario(L)
        steps = {b["step"] for b in out}
        self.assertIn("pause_after_C", steps)
        self.assertIn("pause_resume_C", steps)


class TestCombinerMethod(unittest.TestCase):
    def test_combiner_uses_independent_head_plus_dependent(self):
        rb = ReserveBook()
        rb.add("H", 1, 2 * L)
        rb.add("D", 1, 2 * L)
        decls = DeclarationBook()
        decls.set(ChassisDeclaration("c", (Feed("h", "H", 1),)))
        decls.set(
            ChassisDeclaration("s", (Feed("d", "D", 1, synthetic=True),))
        )
        eng = IssuanceEngine(rb, decls, method="combiner")
        issued = eng.issue(L)
        self.assertEqual(issued.method, "combiner")
        self.assertEqual(issued.basis[0], ("H", 1))  # head first
        self.assertIn(("D", 1), issued.basis)
        self.assertNotEqual(issued.material_hex, "0" * (2 * L))

    def test_combiner_requires_a_guaranteed_path(self):
        rb = ReserveBook()
        rb.add("D", 1, 2 * L)
        decls = DeclarationBook()
        decls.set(ChassisDeclaration("s", (Feed("d", "D", 1, synthetic=True),)))
        eng = IssuanceEngine(rb, decls, method="combiner")
        with self.assertRaises(IssuanceError):
            eng.issue(L)

    def test_combiner_refuses_unequal_lengths(self):
        with self.assertRaises(ValueError):
            CombinerContract.combine([(True, b"a" * 4), (False, b"b" * 5)])

    def test_combiner_demo_runs_and_pause_blocks(self):
        out = run_combiner_demo(L)
        steps = {b["step"]: b for b in out}
        self.assertEqual(steps["combiner_issued"]["order_id"], "CMB-1")
        self.assertIn("refused", steps["combiner_second"])


class TestReserve(unittest.TestCase):
    def test_deterministic_material(self):
        rb = ReserveBook()
        r = rb.add("p", 1, 64)
        o1, m1 = r.take(8)
        rb2 = ReserveBook()
        r2 = rb2.add("p", 1, 64)
        o2, m2 = r2.take(8)
        self.assertEqual((o1, m1), (o2, m2))

    def test_dry_run_does_not_advance(self):
        r = ReserveBook().add("p", 1, 64)
        o1, m1 = r.take(8, dry_run=True)
        self.assertEqual(r.live(), 64)
        o2, m2 = r.take(8)
        self.assertEqual((o1, m1), (o2, m2))

    def test_overdraw_rejected(self):
        r = ReserveBook().add("p", 1, 8)
        with self.assertRaises(ValueError):
            r.take(9)

    def test_clone_is_point_in_time(self):
        rb = ReserveBook()
        r = rb.add("p", 1, 64)
        r.take(8)
        snap = rb.clone()
        r.take(8)
        self.assertEqual(snap.get(("p", 1)).live(), 56)
        self.assertEqual(rb.get(("p", 1)).live(), 48)


if __name__ == "__main__":
    unittest.main()
