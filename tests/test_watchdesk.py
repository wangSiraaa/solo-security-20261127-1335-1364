from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from watchdesk.cli import main
from watchdesk.core import Desk, compute_coverage
from watchdesk.handoff import audit_pairs

ROOT = Path(__file__).resolve().parents[1]
RULES = str(ROOT / "examples" / "rules-v1.json")
PREFIX = str(ROOT / "examples" / "events-prefix.jsonl")
INSIDE_TAIL = str(ROOT / "examples" / "events-inside-tail.jsonl")
OUTSIDE_TAIL = str(ROOT / "examples" / "events-outside-tail.jsonl")
INSIDE_FULL = str(ROOT / "examples" / "events-inside.jsonl")


class WatchDeskTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = str(Path(self.tmp.name) / "desk")

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["--state", self.state, *args])
        return code, out.getvalue() + err.getvalue()

    def desk(self):
        return Desk(self.state)

    def setup_base(self):
        code, _ = self.cli("init")
        self.assertEqual(code, 0)
        self.cli("tick", "--beats", "2")
        self.cli("register", RULES)
        self.cli("enable", "seal3", "--version", "2026.11.26-v1", "--by", "a-heng",
                 "--receipt", "EN-S", "--from-beat", "1", "--shift", "noon", "--desk", "old")
        self.cli("enable", "pair2", "--version", "2026.11.26-v1", "--by", "song",
                 "--receipt", "EN-P", "--from-beat", "1", "--shift", "noon", "--desk", "old")
        self.cli("heartbeat", "--beat", "2", "--by", "song", "--receipt", "HB")
        self.cli("load", PREFIX)
        self.cli("ack", "--seq", "1", "--rule", "seal3", "--by", "a", "--receipt", "A-S1", "--shift", "noon")
        self.cli("ack", "--seq", "2", "--rule", "seal3", "--by", "a", "--receipt", "A-S2", "--shift", "noon")
        self.cli("ack", "--seq", "2", "--rule", "pair2", "--by", "song", "--receipt", "A-P2", "--shift", "noon")

    def test_risk_planner_carries_pair_by_default(self):
        self.setup_base()
        code, output = self.cli("plan", "--at-beat", "2", "--slots", "1", "--emergency",
                                "--by", "lead", "--to-owner", "ji")
        self.assertEqual(code, 0)
        self.assertIn("ctx-pair2-2", output)
        plan = self.desk().state()["handoffs"][-1]
        self.assertEqual(plan["selected_context_ids"], ["ctx-pair2-2"])

    def test_inside_reset_carried_pair_allows_timely_notice_but_splits_seal(self):
        self.setup_base()
        self.cli("plan", "--at-beat", "2", "--slots", "1", "--emergency", "--by", "lead", "--to-owner", "ji")
        self.cli("execute", "--plan", "HO-001", "--receipt", "CARRY", "--by", "lead")
        self.cli("load", INSIDE_TAIL)
        code, out = self.cli("ack", "--seq", "3", "--beat", "3", "--rule", "pair2", "--rule", "seal3",
                             "--by", "ji", "--receipt", "A3", "--shift", "afternoon")
        self.assertEqual(code, 0, out)
        self.assertIn("timely", out)

        code, out = self.cli("deliver", "ALR-pair2-3", "--group", "g", "--received-by", "lead", "--receipt", "DLV")
        self.assertEqual(code, 0, out)

        st = self.desk().state()
        cov = compute_coverage(st, "seal3", 1, 3)
        self.assertTrue(cov["fully_covered"])
        self.assertFalse(cov["continuous_context_intact"])
        self.assertTrue(any(c["id"] == "CERT-PREFIX-ctx-seal3-1" for c in st["certificates"]))
        finding = audit_pairs(self.desk())["findings"][0]
        self.assertTrue(finding["notice_claim_allowed"])

    def test_inside_reset_carried_seal_leaves_pair_retrospective_only(self):
        self.setup_base()
        self.cli("plan", "--at-beat", "2", "--slots", "1", "--emergency", "--by", "lead",
                 "--to-owner", "ji", "--select", "ctx-seal3-1")
        self.cli("execute", "--plan", "HO-001", "--receipt", "CARRY", "--by", "lead")
        self.cli("load", INSIDE_TAIL)
        code, out = self.cli("ack", "--seq", "3", "--beat", "3", "--rule", "pair2", "--rule", "seal3",
                             "--by", "ji", "--receipt", "A3", "--shift", "afternoon")
        self.assertEqual(code, 0, out)
        self.assertIn("no_live_pair_context", out)

        code, _ = self.cli("deliver", "ALR-pair2-3", "--group", "g", "--received-by", "lead", "--receipt", "DLV")
        self.assertEqual(code, 2)  # no timely alert exists

        self.cli("signout", "seal3", "--from-beat", "1", "--to-beat", "3",
                 "--shift", "joint", "--by", "lead", "--receipt", "CHAIN")
        st = self.desk().state()
        self.assertTrue(any(c["id"] == "CHAIN" and c["kind"] == "continuous_context_chain"
                            for c in st["certificates"]))
        finding = audit_pairs(self.desk())["findings"][0]
        self.assertTrue(finding["within_window"])
        self.assertIsNone(finding["live_alert_id"])
        self.assertFalse(finding["notice_claim_allowed"])

    def test_outside_reset_creates_timeliness_gap_and_blocks_delivery(self):
        self.setup_base()
        self.cli("plan", "--at-beat", "2", "--slots", "1", "--emergency", "--by", "lead", "--to-owner", "ji")
        self.cli("execute", "--plan", "HO-001", "--receipt", "CARRY", "--by", "lead")
        self.cli("ack", "--beat", "3", "--through-seq", "2", "--rule", "pair2", "--rule", "seal3",
                 "--by", "ji", "--receipt", "POLL3", "--shift", "afternoon")
        self.cli("load", OUTSIDE_TAIL)
        code, out = self.cli("ack", "--seq", "3", "--beat", "4", "--rule", "pair2",
                             "--by", "ji", "--receipt", "A4", "--shift", "afternoon")
        self.assertEqual(code, 0, out)
        self.assertIn("outside_window", out)
        code, _ = self.cli("deliver", "ALR-pair2-3", "--group", "g", "--received-by", "lead", "--receipt", "DLV")
        self.assertEqual(code, 2)
        st = self.desk().state()
        self.assertTrue(any(g["kind"] == "timeliness_gap" for g in st["gaps"]))
        finding = audit_pairs(self.desk())["findings"][0]
        self.assertFalse(finding["within_window"])
        self.assertIn("临界窗口外", finding["classification"])

    def test_missing_middle_ack_splits_legal_intervals(self):
        self.cli("init")
        self.cli("register", RULES)
        self.cli("enable", "seal3", "--version", "2026.11.26-v1", "--by", "a", "--receipt", "EN", "--from-beat", "1")
        self.cli("heartbeat", "--beat", "2", "--by", "a", "--receipt", "HB2")
        self.cli("load", INSIDE_FULL)
        self.cli("ack", "--seq", "1", "--rule", "seal3", "--by", "a", "--receipt", "A1")
        self.cli("ack", "--seq", "3", "--rule", "seal3", "--by", "a", "--receipt", "A3")

        st = self.desk().state()
        cov = compute_coverage(st, "seal3", 1, 3)
        self.assertEqual(cov["gap_beats"], [2])
        self.assertEqual(cov["intervals"], [[1, 1], [3, 3]])

        code, out = self.cli("signout", "seal3", "--from-beat", "1", "--to-beat", "3",
                             "--shift", "noon", "--by", "lead", "--receipt", "CERT")
        self.assertEqual(code, 0, out)
        st = self.desk().state()
        self.assertEqual(len([c for c in st["certificates"] if c["rule_id"] == "seal3"]), 2)
        self.assertTrue(any(g["kind"] == "coverage_gap" and g["beat"] == 2 for g in st["gaps"]))

        code, out = self.cli("rebuild")
        self.assertEqual(code, 0)
        self.assertIn("连续高水位拍=1", out)

    def test_successful_delivery_then_revoke_keeps_hit_but_removes_notice_claim(self):
        self.setup_base()
        self.cli("plan", "--at-beat", "2", "--slots", "2", "--by", "lead", "--to-owner", "ji")
        self.cli("execute", "--plan", "HO-001", "--receipt", "CARRY2", "--by", "lead")
        self.cli("load", INSIDE_TAIL)
        self.cli("ack", "--seq", "3", "--beat", "3", "--rule", "pair2", "--rule", "seal3",
                 "--by", "ji", "--receipt", "A3", "--shift", "afternoon")
        self.cli("dispatch", "ALR-pair2-3", "--by", "ji")
        code, _ = self.cli("deliver", "ALR-pair2-3", "--group", "seal-duty-group",
                           "--received-by", "oncall", "--receipt", "DLV-S")
        self.assertEqual(code, 0)
        st = self.desk().state()
        self.assertEqual(st["alerts"]["ALR-pair2-3"]["formal_delivery_complete"], True)

        code, _ = self.cli("revoke-delivery", "DLV-S", "--by", "compliance",
                           "--reason", "误盖签收章", "--receipt", "REV1")
        self.assertEqual(code, 0)
        st = self.desk().state()
        self.assertTrue(st["signoffs"]["DLV-S"]["revoked"])
        self.assertEqual(st["alerts"]["ALR-pair2-3"]["formally_notified_groups"], [])
        finding = audit_pairs(self.desk())["findings"][0]
        self.assertFalse(finding["notice_claim_allowed"])

    def test_heartbeat_alone_is_not_coverage(self):
        self.cli("init")
        self.cli("register", RULES)
        self.cli("enable", "seal3", "--version", "2026.11.26-v1", "--by", "a", "--receipt", "EN", "--from-beat", "1")
        self.cli("heartbeat", "--beat", "1", "--by", "a", "--receipt", "HB")
        st = self.desk().state()
        cov = compute_coverage(st, "seal3", 1, 1)
        self.assertFalse(cov["beats"][0]["covered"])
        self.assertIn("missing_processing_receipt", cov["beats"][0]["reasons"])


if __name__ == "__main__":
    unittest.main()
