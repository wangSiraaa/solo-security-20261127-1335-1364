import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from handoff_service.core import HandOffError, handle, new_state, seed_demo  # noqa: E402
from handoff_service.scenario import run_demo  # noqa: E402


class InvariantTests(unittest.TestCase):
    def setUp(self):
        self.state = new_state()
        seed_demo(self.state)

    def test_baseline_locked_read_posts_once(self):
        result = handle(self.state, {
            "op": "input", "interface": "J2", "capability": "read_sample",
            "value": "S-1", "order": "WO-V", "fixture": "F1",
        })
        self.assertEqual("formal", result["decision"])
        self.assertEqual(1, len(self.state["orders"]["WO-V"]["entries"]))

    def test_same_port_unconfirmed_reconnect_only_quarantines(self):
        handle(self.state, {"op": "remove", "interface": "J2"})
        handle(self.state, {"op": "install", "interface": "J2", "component": "BLK-READ-2"})
        witness = handle(self.state, {"op": "witness", "interface": "J2", "capability": "read_sample"})
        self.assertFalse(witness["trusted"])
        result = handle(self.state, {
            "op": "input", "interface": "J2", "capability": "read_sample",
            "value": "S-2", "order": "WO-V", "fixture": "F1",
        })
        self.assertEqual("quarantined", result["decision"])
        self.assertEqual(0, len(self.state["orders"]["WO-V"]["entries"]))
        self.assertEqual("retained", self.state["quarantine"][0]["retention"])

    def test_trusted_component_with_next_fixture_cannot_post_original_order(self):
        handle(self.state, {"op": "pattern_unload", "fixture": "F1"})
        handle(self.state, {"op": "fixture_load", "fixture": "F1", "pattern": "P-RELOAD", "locked": True})
        result = handle(self.state, {
            "op": "input", "interface": "J2", "capability": "read_sample",
            "value": "S-NEXT", "order": "WO-V", "fixture": "F1",
        })
        self.assertEqual("quarantined", result["decision"])
        self.assertEqual([], self.state["orders"]["WO-V"]["entries"])
        rejected = handle(self.state, {"op": "claim", "quarantine": result["quarantine_id"], "order": "WO-V"})
        self.assertEqual("claim_rejected", rejected["status"])
        accepted = handle(self.state, {"op": "claim", "quarantine": result["quarantine_id"], "order": "WO-R"})
        self.assertEqual("claimed", accepted["status"])

    def test_independent_knob_continues(self):
        handle(self.state, {"op": "freeze", "circuit": "C-SCAN"})
        result = handle(self.state, {
            "op": "input", "interface": "J1", "capability": "turn_page",
            "value": 3, "order": "WO-V", "fixture": "F1",
        })
        self.assertEqual("formal", result["decision"])

    def test_backup_requires_original_disconnect_and_records_loss(self):
        handle(self.state, {
            "op": "buffer_read", "interface": "J2", "value": "VOLATILE",
            "order": "WO-V", "fixture": "F1",
        })
        with self.assertRaises(HandOffError) as ctx:
            handle(self.state, {"op": "bus_connect_backup"})
        self.assertEqual("lever_conflict", ctx.exception.code)
        with self.assertRaises(HandOffError) as ctx:
            handle(self.state, {"op": "bus_disconnect_original"})
        self.assertEqual("confirmation_required", ctx.exception.code)
        result = handle(self.state, {"op": "bus_switch_backup", "accept_loss": True})
        self.assertEqual(1, len(result["disconnect"]["lost"]))
        with self.assertRaises(HandOffError) as ctx:
            handle(self.state, {"op": "backup_attach", "component": "BLK-READ-2"})
            handle(self.state, {"op": "backup_attach", "component": "BLK-DUO-1"})
        # First attach succeeds; second is the asserted conflict.
        self.assertEqual("backup_slot_occupied", ctx.exception.code)

    def test_late_reset_receipt_reverses_stale_formal_posting(self):
        handle(self.state, {"op": "input", "interface": "J2", "capability": "read_sample",
                            "value": "before", "order": "WO-V", "fixture": "F1"})
        effective = self.state["seq"]
        stale = handle(self.state, {"op": "input", "interface": "J2", "capability": "read_sample",
                                    "value": "stale", "order": "WO-V", "fixture": "F1"})
        receipt = handle(self.state, {"op": "receipt_record", "id": "R1", "kind": "reset",
                                      "scope": "backplane", "effective_seq": effective})
        self.assertEqual(stale["entry_id"], receipt["reversed_entries"][0]["entry_id"])
        active = [e for e in self.state["orders"]["WO-V"]["entries"] if e["status"] != "reversed"]
        self.assertEqual(1, len(active))

    def test_reset_receipt_after_rewitness_does_not_refreeze_channel(self):
        effective = self.state["seq"]
        handle(self.state, {"op": "witness", "interface": "J2", "capability": "read_sample"})
        receipt = handle(self.state, {"op": "receipt_record", "id": "R2", "kind": "reset",
                                      "scope": "backplane", "effective_seq": effective})
        self.assertNotIn("J2", receipt["invalidated_ports"])
        self.assertIn("J1", receipt["invalidated_ports"])
        self.assertFalse(self.state["ports"]["J2"]["frozen"])
        result = handle(self.state, {"op": "input", "interface": "J2", "capability": "read_sample",
                                     "value": "post-rewitness", "order": "WO-V", "fixture": "F1"})
        self.assertEqual("formal", result["decision"])


class DemoTests(unittest.TestCase):
    def test_all_built_in_assertions(self):
        result = run_demo()
        self.assertTrue(result["all_assertions_passed"], json.dumps(result, ensure_ascii=False, indent=2))


class CliTests(unittest.TestCase):
    def test_cli_seed_input_and_quarantine_output(self):
        with tempfile.TemporaryDirectory() as td:
            state_path = os.path.join(td, "state.json")
            cli = [sys.executable, str(ROOT / "handoff")]
            subprocess.run(cli + ["--state", state_path, "seed"], check=True, cwd=ROOT,
                           capture_output=True, text=True)
            env = dict(os.environ, HANDOFF_STATE=state_path)
            run = subprocess.run(
                cli + ["--state", state_path, "--compact", "call", "--op", "input",
                       "--interface", "J2", "--capability", "read_sample", "--value", "CLI-S1",
                       "--order", "WO-V", "--fixture", "F1"],
                check=True, capture_output=True, text=True, cwd=ROOT, env=env,
            )
            self.assertEqual("formal", json.loads(run.stdout)["decision"])


if __name__ == "__main__":
    unittest.main()
