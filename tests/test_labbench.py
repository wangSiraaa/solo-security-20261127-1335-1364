from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from labbench.errors import AuthorizationDenied, BindingMismatch, InvalidStateError
from labbench.identity import HolderDevice
from labbench.lab import Lab
from labbench.scenarios import (
    issue_correct_bundle,
    issue_presence_only,
    run_core_scenario,
    run_holder_loss_demo,
)
from labbench.store import new_id


class CoreScenarioTests(unittest.TestCase):
    def test_holder_mode_full_security_scenario(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            result = run_core_scenario(Path(td) / "holder", mode="holder")
            self.assertEqual(result["mode"], "holder")
            lab: Lab = result["lab"]
            state_b = lab.bench_b.read_state()
            self.assertEqual(state_b["seats"][result["seat_b_id"]]["status"], "closed")
            self.assertTrue(Path(result["lab"].root / "exports/school_a_export.json").exists())

    def test_registry_mode_full_security_scenario(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            result = run_core_scenario(Path(td) / "registry", mode="registry")
            lab: Lab = result["lab"]
            link = lab.registry.find_by_account("school_a", "A-1001")
            self.assertIsNotNone(link)
            self.assertIn("school_b", link["school_accounts"])
            self.assertTrue(link.get("corrections"))

    def test_holder_loss_cannot_recover_link_but_can_rebind(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            result = run_holder_loss_demo(Path(td) / "loss")
            self.assertTrue(Path(result["lab"].root / "exports/school_a_export.json").exists())


class IsolatedSecurityPropertyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.lab = Lab.create(self.root / "lab", mode="holder")
        self.cast = self.lab.seed_named_cast()
        self.ids = [self.cast["a_card"]["card_id"], self.cast["b_card"]["card_id"]]

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_same_name_does_not_authorize_mixed_cards(self) -> None:
        mixed = [self.cast["a_card"]["card_id"], self.cast["classmate_b_card"]["card_id"]]
        self.assertEqual(self.lab.resolve_card(mixed[0])["status"], "active")
        self.assertEqual(self.lab.resolve_card(mixed[1])["status"], "active")
        self.assertEqual(
            self.lab.resolve_card(mixed[0])["name"],
            self.lab.resolve_card(mixed[1])["name"],
        )

        with self.assertRaises(BindingMismatch):
            self.cast["protagonist"].issue_bundle(
                mixed,
                bench_id="bench_a",
                nonce=self.lab.bench_a.create_challenge("open"),
                oracle=self.lab.oracle,
            )

        nonce = self.lab.bench_a.create_challenge("open")
        pid = new_id("attack")
        bundle = {
            "mode": "holder",
            "nonce": nonce,
            "card_ids": sorted(mixed),
            "same_proof": {"proof_id": new_id("fake"), "presentation_id": pid},
            "presence_proof": issue_presence_only(
                self.lab, self.cast, bench_id="bench_a", nonce=nonce, presentation_id=pid
            ),
        }
        with self.assertRaises(AuthorizationDenied):
            self.lab.bench_a.open_seat(
                bundle,
                resolve_card=self.lab.resolve_card,
                verify_identity=self.lab.verify_identity,
                receipt_book=self.lab.receipt_book,
            )
        self.assertEqual(len(self.lab.bench_a.read_state()["seats"]), 0)

    def test_proof_is_audience_and_nonce_bound(self) -> None:
        nonce_a = self.lab.bench_a.create_challenge("presence")
        pid = new_id("p")
        proof = self.cast["protagonist"].issue_presence_proof(
            bench_id="bench_a",
            nonce=nonce_a,
            oracle=self.lab.oracle,
            presentation_id=pid,
        )
        nonce_b = self.lab.bench_b.create_challenge("presence")
        claims = {"presentation_id": pid}
        self.assertFalse(
            self.lab.oracle.verify_one(
                proof,
                proof_type="presence",
                bench_id="bench_b",
                nonce=nonce_b,
                claims=claims,
            )
        )
        self.assertTrue(
            self.lab.oracle.verify_one(
                proof,
                proof_type="presence",
                bench_id="bench_a",
                nonce=nonce_a,
                claims=claims,
            )
        )
        # One-time use: second verification fails.
        self.assertFalse(
            self.lab.oracle.verify_one(
                proof,
                proof_type="presence",
                bench_id="bench_a",
                nonce=nonce_a,
                claims=claims,
            )
        )

    def test_handoff_requires_new_presence_and_rejects_replay(self) -> None:
        nonce_a = self.lab.bench_a.create_challenge("open")
        bundle_a = issue_correct_bundle(self.lab, self.cast, self.ids, "bench_a", nonce_a)
        seat_a, token_a = self.lab.bench_a.open_seat(
            bundle_a,
            resolve_card=self.lab.resolve_card,
            verify_identity=self.lab.verify_identity,
            receipt_book=self.lab.receipt_book,
        )
        self.lab.bench_a.start_operation(token_a)
        self.lab.bench_a.operate_next(token_a)
        self.lab.bench_a.interrupt_operation(token_a)
        code, receipt = self.lab.bench_a.create_handoff_receipt(token_a, "bench_b")

        nonce_b = self.lab.bench_b.create_challenge("handoff")
        bundle_b = issue_correct_bundle(self.lab, self.cast, self.ids, "bench_b", nonce_b)
        missing = json.loads(json.dumps(bundle_b))
        missing["presence_proof"] = {
            "proof_id": new_id("missing"),
            "presentation_id": bundle_b["same_proof"]["presentation_id"],
        }
        with self.assertRaises(AuthorizationDenied):
            self.lab.bench_b.accept_handoff(
                code,
                missing,
                old_bench=self.lab.bench_a,
                resolve_card=self.lab.resolve_card,
                verify_identity=self.lab.verify_identity,
                receipt_book=self.lab.receipt_book,
            )

        seat_b, token_b = self.lab.bench_b.accept_handoff(
            code,
            bundle_b,
            old_bench=self.lab.bench_a,
            resolve_card=self.lab.resolve_card,
            verify_identity=self.lab.verify_identity,
            receipt_book=self.lab.receipt_book,
        )
        self.assertEqual(seat_b["operation"]["sample"]["next_step"], 1)
        self.assertEqual(
            self.lab.bench_a.read_state()["handoff_receipts"][receipt["receipt_id"]]["status"],
            "accepted",
        )

        replay_nonce = self.lab.bench_b.create_challenge("handoff")
        replay_bundle = issue_correct_bundle(self.lab, self.cast, self.ids, "bench_b", replay_nonce)
        with self.assertRaises(AuthorizationDenied):
            self.lab.bench_b.accept_handoff(
                code,
                replay_bundle,
                old_bench=self.lab.bench_a,
                resolve_card=self.lab.resolve_card,
                verify_identity=self.lab.verify_identity,
                receipt_book=self.lab.receipt_book,
            )

        self.lab.bench_b.resume_operation(token_b)
        completed = False
        for _ in range(4):
            _, view = self.lab.bench_b.operate_next(token_b)
            if view["operation"]["status"] == "completed":
                completed = True
                break
        self.assertTrue(completed)
        _, first_exit = self.lab.bench_b.exit_seat(token_b, self.lab.receipt_book)
        _, second_exit = self.lab.bench_b.exit_seat(token_b, self.lab.receipt_book)
        self.assertEqual(first_exit["receipt_id"], second_exit["receipt_id"])

    def test_operation_cannot_exit_while_running(self) -> None:
        nonce = self.lab.bench_a.create_challenge("open")
        bundle = issue_correct_bundle(self.lab, self.cast, self.ids, "bench_a", nonce)
        _, token = self.lab.bench_a.open_seat(
            bundle,
            resolve_card=self.lab.resolve_card,
            verify_identity=self.lab.verify_identity,
            receipt_book=self.lab.receipt_book,
        )
        self.lab.bench_a.start_operation(token)
        with self.assertRaises(InvalidStateError):
            self.lab.bench_a.exit_seat(token, self.lab.receipt_book)


class RegistryModeTests(unittest.TestCase):
    def test_registry_query_and_correctable_link(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            lab = Lab.create(Path(td), mode="registry")
            cast = lab.seed_named_cast()
            link = lab.registry.find_by_account("school_b", "B-2046")
            self.assertEqual(link["link_id"], cast["link_id"])

            new_card = lab.reissue_card(cast["a_card"]["card_id"], link_id=cast["link_id"])
            updated = lab.registry.find_by_account("school_a", "A-1001")
            self.assertEqual(
                updated["school_accounts"]["school_a"]["card_id"], new_card["card_id"]
            )
            self.assertTrue(updated["corrections"])

    def test_holder_mode_registry_has_no_link(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            lab = Lab.create(Path(td), mode="holder")
            cast = lab.seed_named_cast()
            self.assertIsNone(
                lab.registry.recover_cross_school_link("school_a", "A-1001")
            )
            self.assertEqual(cast["protagonist"].binding_count(), 2)


if __name__ == "__main__":
    unittest.main()
