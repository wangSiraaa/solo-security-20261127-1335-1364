from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from museum_update import controller
from museum_update.errors import PowerCut, VerificationError
from museum_update.store import Store

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "packages" / "gen6"


class ControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.tmp.name) / "state"
        self.store = Store(self.state_dir)
        self.state = controller.initialize_state(self.store, force=True, package_dir=PACKAGE)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def reload(self):
        self.state = self.store.load()
        return self.state

    def order(self, machine="M2", package=PACKAGE):
        return {
            "order_id": f"TICKET-{machine}",
            "package_order_id": "ORD-G6-001",
            "machine": machine,
            "package": str(package),
            "target_slot": "b",
        }

    def test_seed_matches_requested_start(self):
        m1 = self.state["machines"]["M1"]
        self.assertEqual(m1["state"], "gen6_active_uncommitted")
        self.assertEqual(m1["active_slot"], "b")
        self.assertEqual(m1["min_boot_generation"], 5)
        self.assertFalse(m1["bridge_burned"])
        self.assertEqual(len(m1["receipts"]), 1)
        self.assertEqual(self.state["clock"]["deadline_shift"], 3)

    def test_tampered_component_stops_before_slot_switch(self):
        bad_pkg = Path(self.tmp.name) / "bad-package"
        shutil.copytree(PACKAGE, bad_pkg)
        component = bad_pkg / "files" / "components" / "reader.pack"
        component.write_bytes(component.read_bytes() + b"tampered\n")

        with self.assertRaises(VerificationError):
            controller.install_order(self.store, self.state, self.order("M2", bad_pkg))

        state = self.reload()
        m2 = state["machines"]["M2"]
        self.assertNotIn("b", m2["slots"])
        self.assertIsNone(m2["trial_slot"])
        self.assertEqual(m2["active_slot"], "a")
        self.assertEqual(state["orders"]["TICKET-M2"]["phase"], "rejected")

    def test_failed_trial_with_qualified_material_does_not_take_over(self):
        controller.install_order(self.store, self.state, self.order("M3"))
        outcome = controller.boot_machine(self.store, self.state, "M3", "fail", note="injected")
        self.assertTrue(outcome["rolled_back"])

        m3 = self.reload()["machines"]["M3"]
        self.assertEqual(m3["active_slot"], "a")
        self.assertIsNone(m3["trial_slot"])
        self.assertEqual(m3["state"], "rolled_back_gen5")
        self.assertEqual(m3["last_boot"]["generation"], 5)
        self.assertEqual(m3["slots"]["b"]["status"], "failed")

    def test_early_burn_then_next_failure_loses_rollback(self):
        controller.burn_bridge(
            self.store,
            self.state,
            "M1",
            by="test",
            reason="early",
            accept_early_risk=True,
        )
        controller.advance_shift(self.store, self.state, 1)
        outcome = controller.boot_machine(self.store, self.state, "M1", "fail")
        self.assertTrue(outcome.get("disabled"))

        m1 = self.reload()["machines"]["M1"]
        self.assertEqual(m1["min_boot_generation"], 6)
        self.assertTrue(m1["bridge_burned"])
        self.assertEqual(m1["state"], "disabled_repair_required")

        blocked = controller.boot_machine(self.store, self.state, "M1", "pass", slot="a")
        self.assertTrue(blocked["blocked"])

    def test_waiting_allows_rollback_before_deadline_but_old_blocked_after(self):
        controller.advance_shift(self.store, self.state, 1)
        outcome = controller.boot_machine(self.store, self.state, "M1", "fail")
        self.assertTrue(outcome["rolled_back"])
        m1 = self.reload()["machines"]["M1"]
        self.assertEqual(m1["state"], "rolled_back_gen5")

        controller.advance_shift(self.store, self.state, 2)  # reaches shift 3
        blocked = controller.boot_machine(self.store, self.state, "M1", "pass")
        self.assertTrue(blocked["blocked"])
        self.assertEqual(self.reload()["machines"]["M1"]["state"], "retired_stopped")

    def test_power_cut_after_write_resumes(self):
        with self.assertRaises(PowerCut):
            controller.install_order(
                self.store,
                self.state,
                self.order("M2"),
                power_loss_after="written",
            )

        fresh_store = Store(self.state_dir)
        state = fresh_store.load()
        self.assertEqual(state["orders"]["TICKET-M2"]["phase"], "written")
        controller.resume_pending(fresh_store, state, "M2")

        state = fresh_store.load()
        self.assertEqual(state["orders"]["TICKET-M2"]["status"], "awaiting_boot")
        self.assertEqual(state["machines"]["M2"]["trial_slot"], "b")

        outcome = controller.boot_machine(fresh_store, state, "M2", "pass")
        self.assertFalse(outcome["blocked"])
        self.assertEqual(fresh_store.load()["machines"]["M2"]["last_boot"]["generation"], 6)

    def test_deadline_reconcile_promotes_healthy_trial(self):
        controller.install_order(self.store, self.state, self.order("M2"))
        controller.boot_machine(self.store, self.state, "M2", "pass")
        controller.advance_shift(self.store, self.state, 3)
        results = controller.reconcile_deadline(self.store, self.state)
        actions = {r["machine"]: r["action"] for r in results}
        self.assertEqual(actions["M2"], "deadline_commit")
        m2 = self.reload()["machines"]["M2"]
        self.assertTrue(m2["bridge_burned"])
        self.assertEqual(m2["min_boot_generation"], 6)

    def test_receipt_signature_is_verified(self):
        controller.install_order(self.store, self.state, self.order("M2"))
        controller.boot_machine(self.store, self.state, "M2", "pass")
        state = self.reload()
        receipt = dict(state["machines"]["M2"]["receipts"][-1])
        receipt["result"] = "fail"  # mutate without re-signing
        with self.assertRaises(VerificationError):
            controller.verify_external_receipt(state, "M2", receipt)


if __name__ == "__main__":
    unittest.main()
