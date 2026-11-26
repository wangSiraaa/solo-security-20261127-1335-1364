import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import scenarios  # noqa: E402
from recovery_desk import Desk, DeskError  # noqa: E402


class RecoveryDeskTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "desk.db"
        self.desk = Desk(self.db).reset()

    def tearDown(self) -> None:
        self.desk.close()
        self.tmp.cleanup()

    def test_two_independent_sources_complete_and_persist(self) -> None:
        oid = scenarios.seed_alice(self.desk)
        self.assertEqual(oid, "ord-alice-1")
        self.assertEqual(self.desk.get_setting("acct-alice"), "5")
        rows = self.desk.history_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["old_value"], rows[0]["new_value"]), ("30", "5"))

        # A fresh process/connection represents a restart.
        with Desk(self.db) as reopened:
            self.assertEqual(reopened.get_setting("acct-alice"), "5")
            self.assertEqual(len(reopened.history_rows()), 1)
            self.assertEqual(reopened.verify(), [])

    def test_common_old_key_makes_sources_dependent(self) -> None:
        d = self.desk
        d.add_account("acct-key", "Key family")
        d.add_source("src-old", "acct-key", "old_key", "old master")
        d.add_source("src-a", "acct-key", "device_key", "reissue A")
        d.add_source("src-b", "acct-key", "device_key", "reissue B")
        d.add_credential("cred-old", "acct-key", "src-old", "old")
        d.reissue_credential("cred-a", "acct-key", "src-a", ["cred-old"], "A")
        d.reissue_credential("cred-b", "acct-key", "src-b", ["cred-old"], "B")

        ok, why = d.independent_sources("src-a", "src-b")
        self.assertFalse(ok)
        self.assertIn("src-old", why)

    def test_tablet_and_synced_phone_count_once(self) -> None:
        err = scenarios.seed_eric_sync_family(self.desk)
        self.assertIn("NO_INDEPENDENT_PAIR", err)
        row = self.desk.conn.execute(
            "SELECT 1 FROM orders WHERE id='ord-eric-duplicate'"
        ).fetchone()
        self.assertIsNone(row)

    def test_independent_once_then_sync_copy_blocks_new_takeover(self) -> None:
        first_oid, err = scenarios.seed_bob_sync_copy(self.desk)
        self.assertEqual(first_oid, "ord-bob-1")
        self.assertIn("NO_INDEPENDENT_PAIR", err)
        self.assertEqual(self.desk.get_setting("acct-bob"), "5")
        self.assertIsNone(self.desk.conn.execute(
            "SELECT 1 FROM orders WHERE id='ord-bob-2'"
        ).fetchone())
        self.assertEqual(self.desk.conn.execute(
            "SELECT state FROM orders WHERE id='ord-bob-1'"
        ).fetchone()["state"], "paused")

    def test_claimed_but_unsigned_order_and_old_draft_are_blocked(self) -> None:
        out = scenarios.seed_carol_unsigned(self.desk)
        for key in ("old_via_takeover", "old_direct", "old_sign_order",
                    "new_before_sign", "old_after_takeover"):
            self.assertTrue(out[key], key)
        self.assertIn("committed revision", out["new_after_sign"])
        self.assertEqual(self.desk.get_setting("acct-carol"), "7")
        pending = self.desk.conn.execute(
            "SELECT status, confirmed_at FROM pending_changes WHERE id='chg-carol-draft'"
        ).fetchone()
        self.assertEqual(pending["status"], "pending")
        self.assertIsNone(pending["confirmed_at"])

        # Only the successful new-device revision exists; proposed old value 1
        # was never committed.
        values = [r["new_value"] for r in self.desk.history_rows()
                  if r["account_id"] == "acct-carol"]
        self.assertEqual(values, ["7"])

    def test_order_freezes_two_and_discards_unselected_proof(self) -> None:
        scenarios.build_dana_base(self.desk)
        ev = self.desk.conn.execute(
            "SELECT COUNT(*) c FROM order_evidence WHERE order_id='ord-dana'"
        ).fetchone()["c"]
        self.assertEqual(ev, 2)
        cnf = self.desk.conn.execute(
            "SELECT retained, statement, signature FROM confirmations WHERE id='cnf-dana-witness'"
        ).fetchone()
        self.assertEqual(cnf["retained"], 0)
        self.assertIsNone(cnf["statement"])
        self.assertIsNone(cnf["signature"])

        with self.assertRaises(DeskError) as ctx:
            self.desk.backfill_evidence("ord-dana", "cnf-dana-witness")
        self.assertIn("THIRD_PROOF_DISCARDED", str(ctx.exception))

        with self.assertRaises(DeskError) as ctx:
            self.desk.submit_application(
                "app-dana-shadow", "acct-dana",
                new_device_id="dev-dana-new",
                replacement_for_order_id="ord-dana",
                person_present=False)
        self.assertIn("PERSONAL_APPEARANCE_REQUIRED", str(ctx.exception))

    def test_two_replay_futures_from_same_completed_takeover(self) -> None:
        scenarios.build_dana_base(self.desk)
        scenarios.DATA_DIR = Path(self.tmp.name) / "branch-data"
        results = scenarios.run_dana_replays(self.desk)
        by_id = {r["id"]: r for r in results}

        self.assertFalse(by_id["paper-loss"]["frozen_order_setting_allowed"])
        self.assertIn("EVIDENCE_REVOKED", by_id["paper-loss"]["frozen_order_error"])
        self.assertEqual(by_id["paper-loss"]["frozen_order_state"], "paused")
        self.assertFalse(by_id["paper-loss"]["backfill_allowed"])
        self.assertEqual(by_id["paper-loss"]["remedy_order_id"], "ord-dana-remedy")

        self.assertTrue(by_id["witness-correction"]["frozen_order_setting_allowed"])
        self.assertEqual(by_id["witness-correction"]["frozen_order_state"], "active")
        self.assertFalse(by_id["witness-correction"]["backfill_allowed"])
        self.assertIsNone(by_id["witness-correction"]["remedy_order_id"])

        # Branch databases persist and contain the expected real commits.
        paper_db = Path(by_id["paper-loss"]["branch_db"])
        witness_db = Path(by_id["witness-correction"]["branch_db"])
        if not paper_db.is_absolute():
            paper_db = ROOT / paper_db
        if not witness_db.is_absolute():
            witness_db = ROOT / witness_db
        with Desk(paper_db) as bd:
            vals = [r["new_value"] for r in bd.history_rows()
                    if r["account_id"] == "acct-dana"]
            self.assertEqual(vals, ["10", "5"])
            orders = {r["id"]: r["state"] for r in bd.conn.execute("SELECT id,state FROM orders")}
            self.assertEqual(orders["ord-dana"], "paused")
            self.assertEqual(orders["ord-dana-remedy"], "active")
        with Desk(witness_db) as bd:
            vals = [r["new_value"] for r in bd.history_rows()
                    if r["account_id"] == "acct-dana"]
            self.assertEqual(vals, ["10", "5"])
            self.assertEqual(bd.conn.execute(
                "SELECT state FROM orders WHERE id='ord-dana'"
            ).fetchone()["state"], "active")


if __name__ == "__main__":
    unittest.main(verbosity=2)
