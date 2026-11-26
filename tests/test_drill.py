import tempfile
import unittest
from pathlib import Path

from handback_drill.engine import Engine
from handback_drill.errors import DrillError
from handback_drill.planner import build_plan
from handback_drill.scenario import run_branch, run_empty_walkthrough, run_old_plan
from handback_drill.store import Store


class DrillRulesTest(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        import os

        os.close(fd)
        os.unlink(path)
        self.db_path = path
        self.store = Store(path)
        self.engine = Engine(self.store)
        self.engine.reset("empty")

    def tearDown(self):
        self.store.close()
        for suffix in ("", "-wal", "-shm"):
            Path(self.db_path + suffix).unlink(missing_ok=True)

    def test_delivered_command_without_receipt_does_not_open_new_station(self):
        self.engine.deliver_switch("A")
        auth = self.engine.authority_snapshot()["authority"]["A"]
        self.assertEqual(auth["station"], "old-station")
        self.assertIn("without_receipt", auth["basis"])

        self.engine.create_order({"id": "O1", "cabinets": ["A"]})
        with self.assertRaises(DrillError) as ctx:
            self.engine.issue_order("O1", "new-station")
        self.assertEqual(ctx.exception.code, "station_not_authorized")

    def test_durable_deactivation_allows_new_station_success(self):
        self.engine.successful_switch("A")
        self.engine.create_order(
            {"id": "O1", "cabinets": ["A"], "delivery_site": "new-site"}
        )
        result = self.engine.issue_order("O1")
        self.assertEqual(result["voucher"]["status"], "completed")
        self.assertFalse(result["resigned"])

    def test_lease_expiry_transfers_without_receipt(self):
        self.engine.advance(to=4)
        auth = self.engine.authority_snapshot()["authority"]["B"]
        self.assertEqual(auth["station"], "new-station")
        self.assertEqual(auth["basis"], "trusted_lease_expiry")

    def test_joint_keeps_half_and_resumes_without_duplicate(self):
        self.engine.successful_switch("A")
        self.engine.successful_switch("B")
        self.engine.set_route("B", False, "new")
        self.engine.create_order({"id": "J1", "cabinets": ["A", "B"]})

        half = self.engine.issue_order("J1")
        self.assertEqual(half["voucher"]["status"], "partial")
        self.assertEqual(
            [a["cabinet"] for a in half["voucher"]["acceptances"]], ["A"]
        )
        self.assertTrue(self.engine.todos(True))

        self.engine.set_route("B", True, "new")
        done = self.engine.issue_order("J1")
        self.assertEqual(done["voucher"]["status"], "completed")
        self.assertEqual([a["cabinet"] for a in done["new_acceptances"]], ["B"])
        self.assertEqual(len(done["voucher"]["acceptances"]), 2)

        again = self.engine.issue_order("J1")
        self.assertTrue(again["already_completed"])
        self.assertFalse(again["resigned"])
        self.assertEqual(len(again["voucher"]["acceptances"]), 2)

    def test_restart_preserves_facts(self):
        self.engine.successful_switch("A")
        self.engine.create_order({"id": "O1", "cabinets": ["A"]})
        self.engine.issue_order("O1")

        review = self.engine.restart_review()
        order = next(o for o in review["orders"] if o["id"] == "O1")
        self.assertEqual(order["status"], "completed")
        replay = self.engine.issue_order("O1")
        self.assertTrue(replay["already_completed"])

    def test_explicit_future_station_can_keep_half_during_split(self):
        self.engine.successful_switch("A")
        self.engine.create_order({"id": "J1", "cabinets": ["A", "B"]})

        with self.assertRaises(DrillError) as ctx:
            self.engine.issue_order("J1")
        self.assertEqual(ctx.exception.code, "split_authority")

        half = self.engine.issue_order("J1", "new-station")
        self.assertEqual(half["voucher"]["status"], "partial")
        self.assertEqual([a["cabinet"] for a in half["voucher"]["acceptances"]], ["A"])
        self.assertEqual(self.engine.todos(True)[0]["kind"], "waiting_for_generation_boundary")

        self.engine.advance(to=4)
        done = self.engine.issue_order("J1")
        self.assertEqual(done["voucher"]["status"], "completed")
        self.assertEqual([a["cabinet"] for a in done["new_acceptances"]], ["B"])

    def test_actual_image_rewind_recovers_signed_voucher(self):
        self.engine.snapshot_image("last-night", "IMG0")
        self.engine.successful_switch("A")
        self.engine.create_order({"id": "O1", "cabinets": ["A"]})
        self.engine.issue_order("O1")

        # A last-night station image can genuinely rewind station tables, but
        # simulated cabinet receipts survive outside that image.
        self.engine.restore_image_claim({"snapshot_id": "IMG0"})
        self.assertIsNone(self.engine._voucher_for_order("O1"))
        self.engine.create_order({"id": "O1", "cabinets": ["A"]})

        recovered = self.engine.issue_order("O1")
        self.assertTrue(recovered["recovered_from_image"])
        self.assertTrue(recovered["already_completed"])
        self.assertFalse(recovered["resigned"])

    def test_planner_selects_early_a_for_hard_patience(self):
        branch = Engine(Store(":memory:"))
        branch.reset("branch")
        plan = build_plan(branch)
        self.assertEqual(plan["selected_boundary"], "early-per-cabinet")
        self.assertTrue(any(a.get("cabinet") == "A" for p in [plan["plans"]["early_per_cabinet"]] for a in p["actions"]))


class ScenarioReplayTest(unittest.TestCase):
    def test_empty_walkthrough_passes(self):
        path = tempfile.mktemp(suffix=".db")
        result = run_empty_walkthrough(path)
        self.assertTrue(result["ok"], result["checks"])

    def test_reconnect_and_offline_replays_pass(self):
        for case in ("reconnect", "offline"):
            path = tempfile.mktemp(suffix=".db")
            result = run_branch(case, path)
            self.assertTrue(result["ok"], result["checks"])

    def test_old_plan_passes(self):
        path = tempfile.mktemp(suffix=".db")
        result = run_old_plan(path)
        self.assertTrue(result["ok"], result["checks"])


if __name__ == "__main__":
    unittest.main()
