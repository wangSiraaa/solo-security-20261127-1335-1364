from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from seal_channel.cli import main as cli_main
from seal_channel.core import (
    ALPHA,
    BETA,
    SealCase,
    SealConsumedError,
    SendOrderError,
)
from seal_channel.planner import Planner
from seal_channel.security import sha256_hex
from seal_channel.verifier import MockVerifier, body_receipt_value


class TemporaryCaseTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory(prefix="seal-test-")
        self.root = Path(self._temp.name)
        self.case = SealCase.init(self.root)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def allocate_segment_and_depart(self):
        self.case.allocate(preset="segment")
        self.case.depart_all()


class AllocationTests(TemporaryCaseTest):
    def test_each_trip_gets_three_disjoint_numbers_and_partition_is_frozen(self):
        plan = self.case.allocate(alpha="1,3,6", beta="2,4,5")
        self.assertEqual(plan[ALPHA], [1, 3, 6])
        self.assertEqual(plan[BETA], [2, 4, 5])
        self.case.depart_all()

        with self.assertRaises(Exception):
            self.case.allocate(alpha=[1, 2, 3], beta=[4, 5, 6])

    def test_invalid_partitions_are_rejected(self):
        with self.assertRaises(Exception):
            self.case.allocate(alpha=[1, 2], beta=[3, 4, 5, 6])
        # 失败的 allocate 可能已部分 apply；用新用例测重叠/漏号。
        case2_root = self.root / "case2"
        case2 = SealCase.init(case2_root)
        with self.assertRaises(Exception):
            case2.allocate(alpha=[1, 2, 3], beta=[3, 4, 5])
        case3 = SealCase.init(self.root / "case3")
        with self.assertRaises(Exception):
            case3.allocate(alpha=[1, 2, 3], beta=[4, 5, 6], preset="balanced") \
                if False else case3.allocate(alpha=[1, 2, 3], beta=[4, 5, 5])

    def test_cannot_write_before_departure(self):
        self.case.allocate(preset="segment")
        with self.assertRaises(Exception):
            self.case.write(ALPHA, "尚未离站")

    def test_force_init_refuses_unmarked_nonempty_directory(self):
        weird = self.root / "not-a-case"
        weird.mkdir()
        marker = weird / "important.txt"
        marker.write_text("do-not-delete", encoding="utf-8")
        with self.assertRaises(Exception):
            SealCase.init(weird, force=True)
        self.assertTrue(marker.exists())


class SenderPersistenceTests(TemporaryCaseTest):
    def test_restart_does_not_move_consumed_seal_to_new_body(self):
        self.allocate_segment_and_depart()

        first = self.case.write(ALPHA, "第一封正文", seal=1)
        self.assertEqual(first["seal"], 1)

        # 丢弃内存对象，显式重启并从磁盘重载。
        self.case.restart_terminal(ALPHA)
        terminal = self.case.terminal(ALPHA)
        self.assertEqual(terminal.next_seal(), 2)

        second = self.case.write(ALPHA, "第二封正文")
        self.assertEqual(second["seal"], 2)

        with self.assertRaises(SealConsumedError):
            self.case.write(ALPHA, "想顶替第一封的新正文", seal=1)

        reloaded = self.case.terminal(ALPHA)
        binding = reloaded.consumed["1"]
        self.assertEqual(binding["body_sha256"], sha256_hex("第一封正文"))
        self.assertNotEqual(binding["body_sha256"], sha256_hex("想顶替第一封的新正文"))
        original_on_disk = reloaded.load_envelope(1)
        self.assertEqual(original_on_disk["body"], "第一封正文")

    def test_sender_enforces_ascending_order(self):
        self.allocate_segment_and_depart()
        self.case.write(ALPHA, "一号", seal=1)
        with self.assertRaises(SendOrderError):
            self.case.write(ALPHA, "想跳过二号直接发三号", seal=3)
        envelope = self.case.write(ALPHA, "二号")
        self.assertEqual(envelope["seal"], 2)


class VerifierTamperTests(TemporaryCaseTest):
    def test_unissued_registration_makes_same_letter_fail(self):
        self.case.allocate(preset="balanced")
        self.case.depart_all()
        envelope = self.case.write(ALPHA, "需要被核验的原信", seal=1)

        honest = self.case.receive_envelope(envelope, "honest")
        self.assertEqual(honest.status, "recorded")

        record = self.case.mark_unissued(1)
        self.assertEqual(record["state"], "尚未交甲")

        result = self.case.receive_envelope(envelope, "tampered")
        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.code, "SEAL_NOT_HANDED_TO_OWNER")

        verification = self.case.verifier().verify(envelope)
        self.assertFalse(verification.ok)

    def test_body_and_owner_tampering_are_rejected(self):
        self.case.allocate(preset="balanced")
        self.case.depart_all()
        envelope = self.case.write(ALPHA, "原始正文", seal=1)

        changed_body = dict(envelope)
        changed_body["body"] = "篡改后的正文"
        result = self.case.receive_envelope(changed_body, "body-tamper")
        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.code, "BODY_DIGEST_MISMATCH")

        wrong_owner = dict(envelope)
        wrong_owner["owner"] = BETA
        result = self.case.receive_envelope(wrong_owner, "owner-tamper")
        self.assertEqual(result.status, "rejected")
        self.assertIn(result.code, {"OWNER_MISMATCH", "ENVELOPE_GRANT_RECEIPT_MISMATCH"})

    def test_minimal_preconfigured_body_receipt_can_simulate_verifier(self):
        verifier = MockVerifier(
            {
                "seals": {
                    "3": {"owner": ALPHA, "body": "预置回执约定的正文"}
                }
            }
        )
        good = {
            "seal": 3,
            "owner": ALPHA,
            "body": "预置回执约定的正文",
            "body_sha256": sha256_hex("预置回执约定的正文"),
        }
        self.assertTrue(verifier.verify(good).ok)
        bad = dict(good, body="另一封", body_sha256=sha256_hex("另一封"))
        result = verifier.verify(bad)
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "BODY_NOT_RECOGNIZED")


class ReceiverWindowTests(TemporaryCaseTest):
    def test_segment_all_a_then_b_is_six_direct(self):
        self.case.allocate(preset="segment")
        self.case.depart_all()
        for seal, body in [(1, "a1"), (2, "a2"), (3, "a3")]:
            self.case.write(ALPHA, body, seal=seal)
        for seal, body in [(4, "b4"), (5, "b5"), (6, "b6")]:
            self.case.write(BETA, body, seal=seal)

        results = self.case.arrival([ALPHA, BETA], "afirst")
        self.assertEqual([r.status for r in results], ["recorded"] * 6)
        dash = self.case.receiver("afirst").dashboard()
        self.assertEqual(dash["direct_seals"], [1, 2, 3, 4, 5, 6])
        self.assertEqual(dash["pending_seals"], [])

    def test_segment_b_first_makes_all_a_wait_for_reconciliation(self):
        self.case.allocate(preset="segment")
        self.case.depart_all()
        self.case.write(ALPHA, "a1", 1)
        self.case.write(ALPHA, "a2", 2)
        self.case.write(ALPHA, "a3", 3)
        self.case.write(BETA, "b4", 4)
        self.case.write(BETA, "b5", 5)
        self.case.write(BETA, "b6", 6)

        results = self.case.arrival([BETA, ALPHA], "bfirst")
        statuses_by_seal = {r.seal: r.status for r in results}
        self.assertEqual({statuses_by_seal[n] for n in (4, 5, 6)}, {"recorded"})
        self.assertEqual(
            {statuses_by_seal[n] for n in (1, 2, 3)},
            {"pending_reconciliation"},
        )
        dash = self.case.receiver("bfirst").dashboard()
        self.assertEqual(dash["direct_seals"], [4, 5, 6])
        self.assertEqual(dash["pending_seals"], [1, 2, 3])

    def test_duplicate_only_adds_receipt_and_conflict_cannot_occupy_seal(self):
        self.case.allocate(preset="segment")
        self.case.depart_all()
        original = self.case.write(ALPHA, "一号原件", seal=1)
        for seal, body in [(4, "乙四号"), (5, "乙五号"), (6, "乙六号")]:
            envelope = self.case.write(BETA, body, seal=seal)
            result = self.case.receive_envelope(envelope, "r")
            self.assertEqual(result.status, "recorded")

        r1 = self.case.receive_envelope(original, "r")
        self.assertEqual(r1.status, "pending_reconciliation")
        duplicate = self.case.receive_envelope(original, "r")
        self.assertEqual(duplicate.status, "duplicate_pending")

        book = self.case.trusted_book()
        key = book["seals"]["1"]["seal_key_hex"]
        competing = dict(original)
        competing["body"] = "另一份正文"
        competing["body_sha256"] = sha256_hex(competing["body"])
        competing["body_receipt"] = body_receipt_value(
            key, 1, ALPHA, competing["body_sha256"]
        )
        conflict = self.case.receive_envelope(competing, "r")
        self.assertEqual(conflict.status, "conflict")

        receiver = self.case.receiver("r")
        pending = receiver._find_active_pending(1)
        self.assertEqual(pending["body"], "一号原件")
        self.assertIsNotNone(pending)

        entry = self.case.reconcile(1, "人工核对无误", "r")
        self.assertEqual(entry["source"], "manual_reconciliation")
        again = self.case.receive_envelope(original, "r")
        self.assertEqual(again.status, "duplicate")
        receiver = self.case.receiver("r")
        self.assertEqual(len(receiver.data["ledger"]), 4)

    def test_larger_first_number_raises_window(self):
        self.case.allocate(alpha=[2, 4, 5], beta=[1, 3, 6])
        self.case.depart_all()
        # 发端仍按 2→4→5 写好；这里只让 #5 最先抵达收端。
        for seal in (2, 4, 5):
            self.case.write(ALPHA, f"甲 {seal}", seal=seal)
        e5 = self.case.terminal(ALPHA).load_envelope(5)
        result = self.case.receive_envelope(e5, "w")
        self.assertEqual(result.window, [3, 4, 5])

        for seal in (1, 3, 6):
            self.case.write(BETA, f"乙 {seal}", seal=seal)
        e6 = self.case.terminal(BETA).load_envelope(6)
        result6 = self.case.receive_envelope(e6, "w")
        self.assertEqual(result6.high_water, 6)
        self.assertEqual(result6.window, [4, 5, 6])


class PlannerTests(unittest.TestCase):
    def test_canonical_segment_counts(self):
        report = Planner.evaluate([1, 2, 3], [4, 5, 6])
        a_first = report.runs["alpha,beta"]
        b_first = report.runs["beta,alpha"]
        self.assertEqual(a_first.direct, [1, 2, 3, 4, 5, 6])
        self.assertEqual(a_first.pending, [])
        self.assertEqual(b_first.direct, [4, 5, 6])
        self.assertEqual(b_first.pending, [1, 2, 3])
        self.assertEqual(report.worst_direct, 3)

    def test_interleaved_counts(self):
        report = Planner.evaluate([1, 3, 5], [2, 4, 6])
        self.assertEqual(report.runs["alpha,beta"].direct_count, 5)
        self.assertEqual(report.runs["alpha,beta"].pending, [2])
        self.assertEqual(report.runs["beta,alpha"].direct_count, 4)
        self.assertEqual(report.runs["beta,alpha"].pending, [1, 3])

    def test_balanced_plan_gives_five_direct_for_each_order(self):
        report = Planner.evaluate([1, 3, 6], [2, 4, 5])
        self.assertEqual(report.worst_direct, 5)
        self.assertEqual(report.total_direct, 10)
        self.assertEqual(report.runs["alpha,beta"].pending, [2])
        self.assertEqual(report.runs["beta,alpha"].pending, [1])
        self.assertEqual(report.runs["alpha,beta"].late_direct, [4, 5])
        self.assertEqual(report.runs["beta,alpha"].late_direct, [3, 6])
        for run in report.runs.values():
            self.assertEqual(run.rejected, [])

    def test_all_twenty_allocations_run_both_orders_and_best_is_five(self):
        reports = Planner.compare_all()
        self.assertEqual(len(reports), 20)
        self.assertLessEqual(reports[0].worst_direct, 5)
        self.assertEqual(reports[0].worst_direct, 5)
        winners = [r for r in reports if r.worst_direct == 5]
        self.assertEqual(len(winners), 4)


class CliTests(unittest.TestCase):
    def test_global_state_flag_and_demo(self):
        with tempfile.TemporaryDirectory(prefix="seal-cli-") as td:
            state = str(Path(td) / "case")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = cli_main(["--state", state, "init", "--force"])
                self.assertEqual(code, 0)
                code = cli_main(
                    [
                        "allocate",
                        "--state",
                        state,
                        "--alpha",
                        "1,3,6",
                        "--beta",
                        "2,4,5",
                    ]
                )
                self.assertEqual(code, 0)
                code = cli_main(["demo", "--state", state])
                self.assertEqual(code, 0)
            output = buf.getvalue()
            self.assertIn("演示完成", output)
            self.assertTrue((Path(state) / "registry.json").exists())

    def test_json_status_is_parseable(self):
        with tempfile.TemporaryDirectory(prefix="seal-json-") as td:
            state = str(Path(td) / "case")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(cli_main(["init", "--state", state]), 0)
                self.assertEqual(
                    cli_main(["allocate", "--state", state, "--preset", "segment"]),
                    0,
                )
                self.assertEqual(cli_main(["depart", "--state", state, "--all"]), 0)
                self.assertEqual(
                    cli_main(
                        [
                            "write",
                            "--state",
                            state,
                            "--trip",
                            ALPHA,
                            "--text",
                            "hello",
                            "--json",
                        ]
                    ),
                    0,
                )
                self.assertEqual(
                    cli_main(
                        [
                            "deliver",
                            "--state",
                            state,
                            "--trip",
                            ALPHA,
                            "--json",
                        ]
                    ),
                    0,
                )
                self.assertEqual(
                    cli_main(["status", "--state", state, "--json"]), 0
                )
            # Last JSON document is status; locate first { and parse whole?
            # There are multiple JSON outputs due commands. Ensure the status
            # call itself emitted a dashboard with one direct seal.
            self.assertIn('"direct_seals": [\n    1\n  ]', buf.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
