from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from coupon_demo.scenario import (
    build_full_correction_demo,
    happy_path,
    incident_setup,
)
from coupon_demo.service import CouponError, CouponService


class CouponFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = CouponService(":memory:")

    def tearDown(self) -> None:
        self.service.close()

    def test_independent_stack_delivery_claim_and_redemption(self) -> None:
        result = happy_path(self.service)
        self.assertEqual(result["redeemed_ticket"], "H-001")
        self.assertTrue(result["redemption"]["redemption_id"].startswith("RED-"))

        state = self.service.get_state()
        serialized = json.dumps(state, ensure_ascii=False)
        for item in result["print_manifest_once"]:
            self.assertNotIn(item["secret"], serialized)
        self.assertNotIn("secret_hash", serialized)
        self.assertIn("STACK-HAPPY", state["decision"]["sendable_stack_ids"])

        evaluation = self.service.evaluate_coupon("H-002", "COUNTER-1")
        self.assertTrue(evaluation["claimable"])
        self.assertTrue(evaluation["redeemable"])

    def test_secret_mismatch_and_double_redemption_are_rejected(self) -> None:
        result = happy_path(self.service)
        with self.assertRaises(CouponError):
            self.service.redeem_coupon("H-002", "SCR-WRONG-CODE-0000-0000")

        secret_h1 = next(
            x["secret"] for x in result["print_manifest_once"] if x["ticket_no"] == "H-001"
        )
        with self.assertRaises(CouponError):
            self.service.redeem_coupon("H-001", secret_h1, counter_id="COUNTER-1")

    def test_redemption_survives_restart_and_cannot_be_reissued(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "restart.db")
            first = CouponService(db)
            happy = happy_path(first)
            secret = next(
                x["secret"]
                for x in happy["print_manifest_once"]
                if x["ticket_no"] == "H-001"
            )
            first.close()

            reopened = CouponService(db)
            try:
                with self.assertRaises(CouponError):
                    reopened.redeem_coupon("H-001", secret, counter_id="COUNTER-1")
                count = reopened.conn.execute(
                    "SELECT COUNT(*) AS n FROM redemptions"
                ).fetchone()["n"]
                self.assertEqual(count, 1)
            finally:
                reopened.close()

    def test_new_seed_visits_freeze_old_segment_but_do_not_certify_old_prints(self) -> None:
        setup = incident_setup(self.service)
        visit = self.service.visit_new_seed_station("printer-b", seed="new-seed")
        self.assertIn("installed_source", visit)

        with self.assertRaises(CouponError):
            self.service.print_packet(
                stack_id="STACK-FORBIDDEN-OLD",
                source_id=setup["source_b"]["source_id"],
                material_lot_id="LOT-INCIDENT",
                count=1,
            )

        stack_a = self.service.get_stack("STACK-A-RESERVED")
        self.assertEqual(stack_a["provenance_status"], "old_state_unverified")
        evaluation = self.service.evaluate_coupon("A-RES-001")
        self.assertIn("provenance_not_secured", evaluation["redeem_blocker_codes"])

    def test_old_cabinet_proves_exclusive_original_batch(self) -> None:
        incident_setup(self.service)
        stack = self.service.visit_old_cabinet("STACK-A-RESERVED")
        self.assertEqual(stack["provenance_status"], "original_batch_secured")
        self.assertEqual(stack["seal_status"], "verified_intact")

    def test_late_handoff_corrects_claimed_and_unclaimed_tickets(self) -> None:
        setup = incident_setup(self.service)
        manifest = setup["printed_a"]["print_manifest_once"]
        self.service.record_legacy_claim(
            "A-RES-002", holder_name="陈已领", note="昨夜实名领取，交接单未全"
        )
        self.service.visit_old_cabinet("STACK-A-RESERVED")
        self.service.complete_handoff(
            "HANDOFF-A-OLD", counter_id="COUNTER-9", signed_by="补签"
        )

        claimed = self.service.conn.execute(
            "SELECT status, correction_state FROM coupons WHERE ticket_no='A-RES-002'"
        ).fetchone()
        unclaimed = self.service.conn.execute(
            "SELECT status, correction_state FROM coupons WHERE ticket_no='A-RES-003'"
        ).fetchone()
        self.assertEqual(claimed["status"], "claimed")
        self.assertEqual(claimed["correction_state"], "late_handoff_regularized_claimed")
        self.assertEqual(unclaimed["status"], "at_counter")
        self.assertEqual(
            unclaimed["correction_state"], "late_handoff_cleared_unclaimed"
        )

        secret = next(x["secret"] for x in manifest if x["ticket_no"] == "A-RES-002")
        redemption = self.service.redeem_coupon(
            "A-RES-002", secret, counter_id="COUNTER-9"
        )
        self.assertTrue(redemption["redemption_id"])

    def test_confirmed_reprint_corrects_redeemed_claimed_and_unclaimed(self) -> None:
        result = build_full_correction_demo(self.service)
        holds = result["incident"]["finding"]["corrections"]
        self.assertIn("A-RES-001", holds["redeemed_no_reissue"])
        self.assertIn("A-RES-002", holds["claimed_frozen"])
        self.assertIn("A-RES-003", holds["unclaimed_isolated"])
        self.assertIn("B-EVT-101", holds["unclaimed_isolated"])

        a1 = self.service.conn.execute(
            "SELECT status, correction_state FROM coupons WHERE ticket_no='A-RES-001'"
        ).fetchone()
        a2 = self.service.conn.execute(
            "SELECT status, correction_state FROM coupons WHERE ticket_no='A-RES-002'"
        ).fetchone()
        a3 = self.service.conn.execute(
            "SELECT status, correction_state FROM coupons WHERE ticket_no='A-RES-003'"
        ).fetchone()
        self.assertEqual(a1["status"], "redeemed")
        self.assertEqual(a1["correction_state"], "honored_no_reissue")
        self.assertEqual(a2["status"], "review_hold")
        self.assertEqual(a3["status"], "quarantined")

        # Public ticket numbers differ, but the scratch-code hashes match.
        hashes = self.service.conn.execute(
            """SELECT a.secret_hash AS a_hash, b.secret_hash AS b_hash
               FROM coupons a JOIN coupons b ON a.secret_hash=b.secret_hash
               WHERE a.ticket_no='A-RES-001' AND b.ticket_no='B-EVT-101'"""
        ).fetchone()
        self.assertIsNotNone(hashes)

        evaluation = self.service.evaluate_coupon("B-EVT-102")
        self.assertIn("duplicate_secret", evaluation["redeem_blocker_codes"])
        self.assertIn("reprint_under_investigation", evaluation["redeem_blocker_codes"])

        # No replacement order is created for an already-honored benefit.
        orders = self.service.conn.execute(
            """SELECT COUNT(*) AS n FROM replacement_orders ro
               JOIN coupons c ON c.ticket_no=ro.original_ticket_no
               WHERE c.ticket_no='A-RES-001'"""
        ).fetchone()["n"]
        self.assertEqual(orders, 0)

    def test_receipt_retarget_leaves_original_stack_without_counter_receipt(self) -> None:
        result = build_full_correction_demo(self.service)
        receipt_id = result["incident"]["a_receipt_id"]
        receipt = self.service.get_receipt(receipt_id)
        self.assertEqual(receipt["stack_id"], "STACK-B-NEW")
        self.assertEqual(receipt["original_stack_id"], "STACK-A-RESERVED")

        stack_a = self.service.get_stack("STACK-A-RESERVED")
        self.assertIsNone(stack_a["active_receipt"])
        stack_new = self.service.get_stack("STACK-B-NEW")
        self.assertIsNotNone(stack_new["active_receipt"])
        self.assertIn("STACK-B-NEW", self.service.get_state()["decision"]["sendable_stack_ids"])
        self.assertNotIn("STACK-A-RESERVED", self.service.get_state()["decision"]["sendable_stack_ids"])

    def test_witness_mutation_on_clean_segment_puts_originals_in_holds(self) -> None:
        happy = happy_path(self.service)
        segment_id = happy["segment_id"]
        self.service.update_segment_witness(
            segment_id,
            witness_scope="two_machines_shared",
            reprint_confirmed=True,
            machine_ids=["printer-c", "printer-x"],
            note="演练：只把该段见证改成两机共用且确认重印",
        )
        h1 = self.service.conn.execute(
            "SELECT status, correction_state FROM coupons WHERE ticket_no='H-001'"
        ).fetchone()
        h2 = self.service.conn.execute(
            "SELECT status FROM coupons WHERE ticket_no='H-002'"
        ).fetchone()
        self.assertEqual(h1["status"], "redeemed")
        self.assertEqual(h1["correction_state"], "honored_no_reissue")
        self.assertEqual(h2["status"], "quarantined")

        new_source = self.service.install_independent_source(
            "printer-d", seed="another-independent-source"
        )
        other = self.service.print_packet(
            stack_id="STACK-OTHER",
            source_id=new_source["source_id"],
            material_lot_id="LOT-HAPPY",
            count=2,
            ticket_prefix="O",
            seal_id="SEAL-OTHER",
        )
        # Reuse HAPPY's receipt, changing only its target to the other stack.
        self.service.retarget_counter_receipt("RCPT-HAPPY", "STACK-OTHER")
        happy_stack = self.service.get_stack("STACK-HAPPY")
        other_stack = self.service.get_stack("STACK-OTHER")
        self.assertIsNone(happy_stack["active_receipt"])
        self.assertEqual(other_stack["active_receipt"]["stack_id"], "STACK-OTHER")
        self.assertEqual(other_stack["active_receipt"]["receipt_id"], "RCPT-HAPPY")

        # The other stack can complete a claim/redemption.
        secret_o1 = next(
            x["secret"] for x in other["print_manifest_once"] if x["ticket_no"] == "O-001"
        )
        self.service.claim_coupon("O-001", counter_id="COUNTER-1")
        redemption = self.service.redeem_coupon("O-001", secret_o1, counter_id="COUNTER-1")
        self.assertTrue(redemption["redemption_id"])

    def test_continuation_depends_on_other_machine_witness_and_freeze(self) -> None:
        setup = incident_setup(self.service)
        before = self.service.can_continue_segment("SEG-OLD-SHARED", "printer-a")
        self.assertTrue(before["can_continue_original_segment"])
        self.assertEqual(before["exclusive_machine"], "printer-a")

        self.service.visit_new_seed_station("printer-b", seed="freeze-seed")
        frozen = self.service.can_continue_segment("SEG-OLD-SHARED", "printer-a")
        self.assertFalse(frozen["can_continue_original_segment"])
        self.assertIn("old_segment_frozen_after_independent_source", frozen["reasons"])

        # A fresh unrestricted setup shows the other-machine continuation rule.
        service2 = CouponService(":memory:")
        try:
            setup2 = incident_setup(service2)
            service2.print_packet(
                stack_id="STACK-B-USE",
                source_id=setup2["source_b"]["source_id"],
                material_lot_id="LOT-INCIDENT",
                count=1,
                ticket_nos=["B-9"],
            )
            shared = service2.can_continue_segment("SEG-OLD-SHARED", "printer-a")
            self.assertFalse(shared["can_continue_original_segment"])
            self.assertIn("state_continued_on_another_machine", shared["reasons"])
        finally:
            service2.close()

    def test_literal_witness_mutation_is_reconciled_into_holds(self) -> None:
        happy = happy_path(self.service)
        with self.service.conn:
            self.service.conn.execute(
                """UPDATE segments
                   SET witness_scope='two_machines_shared', reprint_confirmed=1
                   WHERE segment_id=?""",
                (happy["segment_id"],),
            )

        evaluation = self.service.evaluate_coupon("H-002", "COUNTER-1")
        self.assertIn("reprint_under_investigation", evaluation["redeem_blocker_codes"])
        row = self.service.conn.execute(
            "SELECT status, correction_state FROM coupons WHERE ticket_no='H-002'"
        ).fetchone()
        self.assertEqual(row["status"], "quarantined")
        self.assertEqual(row["correction_state"], "reprint_unclaimed_isolate")

        # Reconciliation must not duplicate corrections/orders on every read.
        self.service.get_state()
        correction_count = self.service.conn.execute(
            "SELECT COUNT(*) AS n FROM corrections WHERE aggregate_id='H-002' AND kind='reprint_ticket_correction'"
        ).fetchone()["n"]
        self.assertEqual(correction_count, 1)

    def test_literal_receipt_target_mutation_is_enough_to_move_custody(self) -> None:
        happy = happy_path(self.service)
        source = self.service.install_independent_source(
            "printer-z", seed="literal-receipt-mutation"
        )
        self.service.print_packet(
            stack_id="STACK-LITERAL-OTHER",
            source_id=source["source_id"],
            material_lot_id="LOT-HAPPY",
            count=1,
            ticket_prefix="Z",
            seal_id="SEAL-Z",
        )
        # Deliberately mutate only the receipt row, as in the acceptance brief.
        with self.service.conn:
            self.service.conn.execute(
                "UPDATE counter_receipts SET stack_id='STACK-LITERAL-OTHER' WHERE receipt_id=?",
                (happy["receipt_id"],),
            )

        self.assertIsNone(self.service.get_stack("STACK-HAPPY")["active_receipt"])
        other = self.service.get_stack("STACK-LITERAL-OTHER")
        self.assertIsNotNone(other["active_receipt"])
        self.assertEqual(other["active_receipt"]["receipt_id"], happy["receipt_id"])
        evaluation = self.service.evaluate_coupon("H-002", "COUNTER-1")
        self.assertIn("no_active_counter_receipt", evaluation["claim_blocker_codes"])

    def test_cannot_certify_a_exclusive_after_other_machine_witness(self) -> None:
        setup = incident_setup(self.service)
        self.service.print_packet(
            stack_id="STACK-B-OLD",
            source_id=setup["source_b"]["source_id"],
            material_lot_id="LOT-INCIDENT",
            count=1,
            ticket_nos=["B-1"],
        )
        with self.assertRaises(CouponError):
            self.service.visit_old_cabinet("STACK-A-RESERVED")


if __name__ == "__main__":
    unittest.main()
