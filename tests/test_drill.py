import copy
import io
import unittest
from contextlib import redirect_stdout

from draft_drill.demo import run_demo
from draft_drill.model import (
    DeliveryProofRequired,
    DeskUnreachable,
    FreshAttestationRequired,
    OpenDenied,
)
from draft_drill.world import DrillWorld


class DrillSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.world = DrillWorld.standard()
        self.assertTrue(self.world.boot_result.ok)
        self.grant = self.world.complete_loan()
        self.outcome = self.world.open_and_display(self.grant)
        self.order = self.world.order

    def test_boot_receipt_has_generation_and_boot_modules(self) -> None:
        receipt = self.world.boot_receipt
        self.assertIsNotNone(receipt)
        payload = receipt.payload
        self.assertEqual(payload["boot_generation"], "BOOT-20261127-0001")
        self.assertTrue(payload["modules"])
        self.assertIn("module_digest", payload)
        coordinates = {m["coordinate"] for m in payload["modules"]}
        self.assertIn("FONT-SONG@2.4.1", coordinates)

    def test_qualified_terminal_actually_reads_draft(self) -> None:
        self.assertIn("夜航排字房密样第十四号", self.outcome.display_text)
        self.assertEqual(
            self.outcome.delivery.displayed_version, self.world.draft.version
        )
        self.assertEqual(
            self.outcome.delivery.content_sha256, self.world.draft.content_sha256
        )

    def test_fresh_proof_is_bound_to_inquiry_and_nonce(self) -> None:
        proof = self.outcome.proof.payload
        inquiry = self.outcome.inquiry
        self.assertEqual(proof["inquiry_id"], inquiry.inquiry_id)
        self.assertEqual(proof["nonce"], inquiry.nonce)
        self.assertEqual(proof["order_id"], self.order.order_id)
        self.assertEqual(
            proof["attestation_class"], "FICTIONAL_LOCAL_DEVICE_DOUBLE"
        )

    def test_old_inquiry_receipt_cannot_replace_new_inquiry(self) -> None:
        new_inquiry = self.world.verifier.issue_inquiry(
            self.order, purpose="fresh-open"
        )
        result = self.world.verifier.verify_for_order(
            self.grant.proof, new_inquiry, self.order
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "INQUIRY_BINDING_MISMATCH")

    def test_consumed_inquiry_nonce_cannot_be_replayed(self) -> None:
        original_inquiry = self.world.verifier.inquiries["INQ-001"]
        result = self.world.verifier.verify_for_order(
            self.grant.proof, original_inquiry, self.order
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "INQUIRY_ALREADY_CONSUMED")

    def test_original_grant_is_not_direct_opening_capability(self) -> None:
        with self.assertRaises(FreshAttestationRequired):
            self.world.desk.present_credential_directly_for_body(self.grant)

    def test_individually_approved_but_unapproved_pair_is_denied(self) -> None:
        self.world.swap_to_unapproved_pair()
        with self.assertRaises(OpenDenied) as context:
            self.world.open_and_display(self.grant)
        self.assertEqual(
            context.exception.decision.code, "NO_APPROVED_COMBINATION"
        )
        self.assertEqual(len(self.world.desk.ledger.records), 1)

    def test_already_open_reading_remains_pinned_after_swap(self) -> None:
        pinned_generation = self.outcome.delivery.state_generation
        self.world.swap_to_unapproved_pair()
        session = self.world.terminal.continue_reading(self.order.order_id)
        self.assertEqual(session.state_generation, pinned_generation)
        self.assertIn("夜航排字房密样第十四号", session.display_text)
        self.assertNotEqual(
            session.state_generation,
            self.world.terminal.state.state_generation(),
        )

    def test_missing_attachment_denied_even_when_environment_core_is_valid(self):
        self.world.restore_approved_combo(with_attachment=False)

        with self.assertRaises(OpenDenied) as context:
            self.world.open_and_display(self.grant)
        self.assertEqual(
            context.exception.decision.code, "MISSING_REQUIRED_ATTACHMENT"
        )

        env_inquiry = self.world.verifier.issue_inquiry(
            self.order, purpose="environment-check"
        )
        env_proof = self.world.terminal.answer_inquiry(env_inquiry)
        env_result = self.world.verifier.verify_environment_only(
            env_proof, env_inquiry
        )
        self.assertTrue(env_result.ok, env_result.summary)
        self.assertEqual(env_result.code, "ENVIRONMENT_APPROVED")

        with self.assertRaises(DeliveryProofRequired):
            self.world.desk.ledger.append_from_environment_certificate(
                manuscript_no=self.order.manuscript_no,
                environment_verification_id=env_result.verification_id,
            )
        self.assertEqual(len(self.world.desk.ledger.records), 1)

    def test_successor_finds_actual_version_and_verified_generation(self) -> None:
        records = self.world.desk.ledger.for_manuscript(self.order.manuscript_no)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.displayed_version, "v3")
        self.assertEqual(record.state_generation, self.outcome.verification.state_generation)
        self.assertTrue(record.verification_id)

    def test_disconnect_protects_existing_reading_but_blocks_new_open(self) -> None:
        self.world.disconnect()
        existing = self.world.terminal.continue_reading(self.order.order_id)
        self.assertIn("夜航排字房密样第十四号", existing.display_text)

        with self.assertRaises(DeskUnreachable):
            self.world.open_and_display(self.grant)
        self.assertEqual(len(self.world.desk.ledger.records), 1)

        self.world.reconnect()
        second = self.world.open_and_display(self.grant)
        self.assertEqual(len(self.world.desk.ledger.records), 2)
        self.assertTrue(second.verification.ok)

    def test_replay_branches_share_same_initial_order_snapshot(self) -> None:
        snapshot = copy.deepcopy(self.world)
        swap_branch = copy.deepcopy(snapshot)
        outage_branch = copy.deepcopy(snapshot)

        self.assertEqual(
            swap_branch.order.order_id, outage_branch.order.order_id
        )
        self.assertEqual(
            swap_branch.desk.ledger.records,
            outage_branch.desk.ledger.records,
        )
        self.assertEqual(
            swap_branch.initial_grant.token.mac,
            outage_branch.initial_grant.token.mac,
        )

        swap_branch.swap_to_unapproved_pair()
        outage_branch.disconnect()

        with self.assertRaises(OpenDenied):
            swap_branch.open_and_display()
        with self.assertRaises(DeskUnreachable):
            outage_branch.open_and_display()

        self.assertEqual(len(swap_branch.desk.ledger.records), 1)
        self.assertEqual(len(outage_branch.desk.ledger.records), 1)


class FullDemoTests(unittest.TestCase):
    def test_full_demo_assertions_pass(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            return_code = run_demo()
        self.assertEqual(return_code, 0)
        self.assertIn("全部端到端断言通过", output.getvalue())


if __name__ == "__main__":
    unittest.main()
