from __future__ import annotations

import json
import io
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from collab_editor.cli import main as cli_main
from collab_editor.privacy import assert_clean, scan_for_leaks
from collab_editor.workspace import Workspace

BASE = "潮水退回桥下。"
CHANGED = "潮水漫过桥下。"
CANARY = "TEST-CANARY-PRIVATE-9182：私有旁注内容"


class CollabEditorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="collab-test-"))
        self.exchange = self.root / "exchange"
        self.exchange.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def make_pair(self, anchors=(("A1", BASE), ("A2", BASE))):
        museum_dir = self.root / "museum"
        coop_dir = self.root / "coop"
        museum = Workspace.init(
            museum_dir, role="museum", author="museum", anchors=list(anchors)
        )
        museum.deliver_to(coop_dir, exchange_packet=self.exchange / "delivery.json")
        return museum_dir, coop_dir, museum, Workspace(coop_dir)

    def test_identical_sentences_second_edit_undo_redo(self) -> None:
        _, coop_dir, _, coop = self.make_pair()
        edit = coop.edit("A2", CHANGED, old=BASE)
        self.assertEqual(edit["anchor"], "A2")
        self.assertEqual(coop.doc["anchors"][0]["text"], BASE)

        coop.undo()
        self.assertEqual(coop.doc["anchors"][1]["text"], BASE)
        coop.redo()
        self.assertEqual(coop.doc["anchors"][1]["text"], CHANGED)
        self.assertEqual(coop.doc["anchors"][0]["text"], BASE)

        packet_path = self.exchange / "coop.json"
        coop.push(packet_path)
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
        kinds = [op["kind"] for op in packet["changes"]]
        self.assertEqual(kinds, ["edit", "undo", "redo"])
        self.assertTrue(all("private" not in json.dumps(op) for op in packet["changes"]))

    def test_private_note_position_and_pure_private_count_stay_private(self) -> None:
        museum_dir, coop_dir, museum, coop = self.make_pair()
        museum.add_private_note("A1", CANARY, "before")
        museum.add_private_note("A1", "second private note", "after")
        first = museum.private["annotations"][0]
        self.assertEqual(first["full_text_start"], 0)
        self.assertGreater(first["full_text_end"], 0)

        # Two pure-private operations do not consume or gap public numbering.
        public_op = museum.edit("A1", "潮光退回桥下。", old=BASE)
        self.assertEqual(public_op["id"], "PUB-M-0001")
        museum.push(self.exchange / "museum.json")

        private_data = json.loads((museum_dir / "private" / "private.json").read_text())
        self.assertEqual(private_data["next_internal_seq"], 3)  # two PV notes + one? Wait edit public-only no internal. Actually two PV -> next 3.
        assert_clean(
            [coop_dir, self.exchange, museum_dir / "outbox", museum_dir / "reports"],
            [CANARY, "second private note"],
        )
        exported = (self.exchange / "museum.json").read_text(encoding="utf-8")
        self.assertNotIn("PV-000", exported)
        self.assertNotIn("internal_seq", exported)
        self.assertNotIn("private_op_count", exported)

    def test_private_note_does_not_create_conflict_or_extra_anchor(self) -> None:
        museum_dir, coop_dir, museum, coop = self.make_pair()
        coop.edit("A2", CHANGED, old=BASE)
        coop.undo()
        coop.redo()
        coop.push(self.exchange / "coop.json")

        museum.add_private_note("A1", CANARY, "before")
        report = museum.merge_packet(
            self.exchange / "coop.json", answer_out=self.exchange / "answer.json"
        )
        self.assertEqual(report["status"], "merged")
        self.assertEqual(report["conflicts"], [])
        self.assertEqual({h["anchor"] for h in report["affected_locations"]}, {"A2"})
        assert_clean([coop_dir, self.exchange], [CANARY])

    def test_same_anchor_one_character_merge_points_to_one_place(self) -> None:
        _, coop_dir, museum, coop = self.make_pair(
            anchors=(("S1", "甲乙丙丁"), ("S2", "另一句"))
        )
        museum.edit("S1", "甲A丙丁", old="甲乙丙丁")
        museum.push(self.exchange / "one.json")
        report = coop.merge_packet(self.exchange / "one.json")
        self.assertEqual(report["status"], "merged")
        self.assertEqual(len(report["affected_locations"]), 1)
        hunk = report["affected_locations"][0]
        self.assertEqual((hunk["anchor"], hunk["base_start"], hunk["base_end"]), ("S1", 1, 2))
        self.assertEqual(hunk["before"], "乙")
        self.assertEqual(hunk["after"], "A")

    def test_concurrent_same_line_cuts_converge(self) -> None:
        museum_dir, coop_dir, museum, coop = self.make_pair(
            anchors=(("L1", "一二三四五六七八"),)
        )
        museum.edit("L1", "\n", start=2, end=2)
        coop.edit("L1", "\n", start=5, end=5)

        coop.push(self.exchange / "cuts-c.json")
        m_report = museum.merge_packet(self.exchange / "cuts-c.json")
        museum.push(self.exchange / "cuts-m.json")
        c_report = coop.merge_packet(self.exchange / "cuts-m.json")

        expected = "一二\n三四五\n六七八"
        self.assertEqual(m_report["status"], "merged")
        self.assertEqual(c_report["status"], "merged")
        self.assertEqual(museum.doc["anchors"][0]["text"], expected)
        self.assertEqual(coop.doc["anchors"][0]["text"], expected)

    def test_identical_cut_is_idempotent(self) -> None:
        from collab_editor.merge import merge_anchor_text

        result = merge_anchor_text("一二三四", "一二\n三四", "一二\n三四", "L1")
        self.assertEqual(result["status"], "clean")
        self.assertEqual(result["text"], "一二\n三四")
        self.assertEqual(len(result["hunks"]), 1)

    def test_same_character_conflict_sheet_is_one_public_item(self) -> None:
        museum_dir, coop_dir, museum, coop = self.make_pair(anchors=(("L1", "甲乙丙"),))
        museum.add_private_note("L1", CANARY)
        museum.edit("L1", "甲A丙", old="甲乙丙")
        coop.edit("L1", "甲B丙", old="甲乙丙")
        coop.push(self.exchange / "conflict.json")

        report = museum.merge_packet(self.exchange / "conflict.json")
        self.assertEqual(report["status"], "conflict")
        self.assertEqual(len(report["conflicts"]), 1)
        item = report["conflicts"][0]
        self.assertEqual((item["anchor"], item["base_start"], item["base_end"]), ("L1", 1, 2))
        conflict_dir = museum_dir / "conflicts"
        findings = scan_for_leaks([conflict_dir, self.exchange], [CANARY])
        self.assertEqual(findings, [])

    def test_mixed_undo_before_and_after_delivery(self) -> None:
        # Before delivery: true whole undo.
        pre_dir = self.root / "pre"
        pre = Workspace.init(pre_dir, role="museum", author="museum", anchors=[("M1", "甲乙")])
        mixed = pre.mixed_edit("M1", "甲X", old="甲乙", note_text=CANARY)
        result = pre.undo_mixed(mixed["internal_id"])
        self.assertEqual(result["policy"], "whole-mixed-undo")
        self.assertEqual(pre.public_text().strip(), "甲乙")

        # After delivery: private withdrawal + new public correction only.
        museum_dir, coop_dir, museum, coop = self.make_pair(anchors=[("M1", "甲乙")])
        mixed = museum.mixed_edit("M1", "甲X", old="甲乙", note_text=CANARY)
        museum.push(self.exchange / "mixed.json")
        coop.merge_packet(self.exchange / "mixed.json", answer_out=self.exchange / "ans.json")
        museum.acknowledge_answer(self.exchange / "ans.json")

        result = museum.undo_mixed(mixed["internal_id"])
        self.assertEqual(result["policy"], "public-correction-proposed")
        with self.assertRaises(PermissionError):
            museum.undo(mixed["public_op"]["id"])
        self.assertNotEqual(result["public_correction"]["id"], mixed["public_op"]["id"])
        museum.push(self.exchange / "correction.json")
        report = coop.merge_packet(self.exchange / "correction.json")
        self.assertEqual(report["status"], "merged")
        self.assertEqual(coop.public_text().strip(), "甲乙")
        assert_clean([coop_dir, self.exchange, museum_dir / "outbox"], [CANARY])

    def test_coop_branch_can_withdraw_delivered_change_offline(self) -> None:
        _, coop_dir, museum, coop = self.make_pair(anchors=[("W1", "甲乙")])
        op = museum.edit("W1", "甲Y", old="甲乙")
        museum.push(self.exchange / "w.json")
        coop.merge_packet(self.exchange / "w.json")
        self.assertIn(op["id"], [o["id"] for o in coop.history["ops"]])

        withdrawal = coop.withdraw_delivered(op["id"])
        self.assertEqual(withdrawal["author"], "coop")
        self.assertEqual(withdrawal["withdraws"], op["id"])
        self.assertEqual(coop.public_text().strip(), "甲乙")

        with self.assertRaises(PermissionError):
            museum.withdraw_delivered(op["id"])

    def test_reopen_and_continue_confirmed_delivery(self) -> None:
        _, coop_dir, museum, coop = self.make_pair()
        coop.edit("A2", CHANGED, old=BASE)
        coop.push(self.exchange / "c.json")
        report = museum.merge_packet(
            self.exchange / "c.json", answer_out=self.exchange / "a.json"
        )
        self.assertEqual(report["status"], "merged")
        # The merge answer goes back to coop and confirms the common base.
        coop.acknowledge_answer(self.exchange / "a.json")

        reopened_coop = Workspace(coop_dir)
        next_op = reopened_coop.edit("A2", "潮水涌过桥下。", old=CHANGED)
        self.assertTrue(next_op["id"].startswith("PUB-C-"))
        reopened_coop.push(self.exchange / "c2.json")

        reopened_museum = Workspace(museum.root)
        report2 = reopened_museum.merge_packet(self.exchange / "c2.json")
        self.assertEqual(report2["status"], "merged")
        self.assertEqual(reopened_museum.doc["anchors"][1]["text"], "潮水涌过桥下。")

    def test_cli_audit_fails_on_canary(self) -> None:
        external = self.root / "external"
        external.mkdir()
        (external / "leak.txt").write_text(f"prefix {CANARY}\n", encoding="utf-8")
        with redirect_stdout(io.StringIO()):
            code = cli_main(
                ["audit", "--external", str(external), "--canary", CANARY]
            )
        self.assertEqual(code, 4)


if __name__ == "__main__":
    unittest.main()
