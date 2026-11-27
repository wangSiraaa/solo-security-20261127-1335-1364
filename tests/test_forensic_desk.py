from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from forensic_desk import IntakeDesk, ReviewDesk, make_tree_writable, verify_station_bundle
from forensic_desk.authz import READ_RAW_CONTENT, WRITE_WORK_COPY
from forensic_desk.fixtures import write_all_fixtures
from forensic_desk.legacy import LegacyViewer, LegacyWriteBackBlocked


class ForensicScenarioTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="forensic-test-"))
        self.disks = write_all_fixtures(self.tmp / "disks")
        self.cases = self.tmp / "cases"
        self.cases.mkdir()

    def tearDown(self) -> None:
        # Read-only station/vault directories need write bits before removal.
        for child in self.cases.glob("*") if self.cases.exists() else []:
            make_tree_writable(child)
        make_tree_writable(self.tmp)
        shutil.rmtree(self.tmp, ignore_errors=False)

    def _intake_case1401_cow(self, name: str = "case1401-cow") -> Path:
        case_dir = self.cases / name
        IntakeDesk(case_dir).receive(
            self.disks["case1401"],
            case_id="CASE-1401",
            source="虚构卷宗盘 SN-1401",
            generation="卷代 G1 / 接盘快照",
            signed_by="保全员甲；委托人乙签交接书",
            collection_mode="custodian_cow",
            readable_blocks=["DIRECTORY", "JOURNAL", "LAYOUT", "BLK100", "BLK200", "BLK300"],
            page_ranges=["P100", "P201", "P301"],
            notes="测试用虚构盘",
        )
        return case_dir

    def test_station_third_party_verification_and_legacy_direct_open_blocked(self) -> None:
        case_dir = self.cases / "station"
        intake = IntakeDesk(case_dir)
        result = intake.receive(
            self.disks["case1401"],
            case_id="CASE-1401-STATION",
            source="虚构卷宗盘 SN-1401",
            generation="G1",
            signed_by="委托人乙",
            collection_mode="write_protected_station",
            readable_blocks=["DIRECTORY", "JOURNAL", "LAYOUT", "BLK100"],
            page_ranges=None,
        )
        bundle = Path(result["station_bundle"]["path"])

        verification = verify_station_bundle(bundle)
        self.assertTrue(verification["ok"])
        # Repeatability: a second independent verification gives same head ID.
        again = verify_station_bundle(bundle)
        self.assertEqual(again["handoff_record_id"], verification["handoff_record_id"])

        with self.assertRaises(LegacyWriteBackBlocked):
            LegacyViewer.open_direct(bundle)

        reviewer = ReviewDesk(case_dir, "third-party")
        blocked = reviewer.open_legacy("write_protected_station")
        self.assertEqual(
            blocked["conclusion"]["payload"]["status"],
            "LEGACY_DIRECT_OPEN_BLOCKED_WRITE_PROTECTED",
        )

        raw = reviewer.request_raw_block("BLK100")
        self.assertEqual(raw["conclusion"]["payload"]["status"], "RAW_BLOCK_OBSERVED")
        self.assertIn("P100 原始块观察", raw["disclosure"]["text"])
        self.assertTrue(raw["disclosure"]["matches_intake_manifest"])

    def test_unreplayed_rename_investigator_rename_and_immutable_conclusions(self) -> None:
        case_dir = self._intake_case1401_cow()
        intake = IntakeDesk(case_dir)
        alice = ReviewDesk(case_dir, "alice")

        old_physical = alice.request_entry(name="未重放改名-旧名.txt")
        self.assertEqual(
            old_physical["conclusion"]["payload"]["status"],
            "PHYSICAL_PRESENT_AT_INTAKE",
        )
        old_physical_id = old_physical["conclusion"]["id"]
        self.assertIn(
            "E100|未重放改名-旧名.txt",
            old_physical["conclusion"]["payload"]["result"]["original_existence_proof"]["raw_line"],
        )

        replayed = alice.request_entry(name="日志重放-新名.txt")
        self.assertEqual(replayed["conclusion"]["payload"]["status"], "REPLAYED_PRESENT")
        replayed_id = replayed["conclusion"]["id"]
        self.assertEqual(
            replayed["conclusion"]["payload"]["result"]["replay_witnesses"][0]["tx_id"],
            "TX1001",
        )

        journal_added = alice.request_entry(name="Q-开卷即写行-来自日志重放.txt")
        self.assertEqual(journal_added["conclusion"]["payload"]["status"], "REPLAYED_PRESENT")
        self.assertNotIn("Q-开卷即写行-来自日志重放.txt", (case_dir / "vault" / "directory.idx").read_text("utf-8"))
        out_of_scope_block = alice.request_raw_block("BLK900")
        self.assertEqual(
            out_of_scope_block["conclusion"]["payload"]["status"],
            "AUTHORIZATION_REQUIRED",
        )

        absent = alice.request_absence(name="根本不存在的条目.txt")
        self.assertEqual(absent["conclusion"]["payload"]["status"], "ABSENT_IN_AUTHORIZED_SCOPE")
        self.assertIn("negative_proof", absent["conclusion"]["payload"]["result"])

        opened = alice.open_legacy()
        self.assertEqual(
            opened["conclusion"]["payload"]["status"],
            "LEGACY_REPLAY_WRITTEN_TO_PRIVATE_COW",
        )
        journal_after_open = alice.request_entry(name="Q-开卷即写行-来自日志重放.txt")
        self.assertEqual(
            journal_after_open["conclusion"]["payload"]["status"],
            "REPLAYED_PRESENT",
        )

        # A second rename is a new write operation and initially lacks authority.
        denied = alice.rename_entry("E100", "调查员再次改名.txt")
        self.assertEqual(denied["conclusion"]["payload"]["status"], "AUTHORIZATION_REQUIRED")
        self.assertEqual(
            denied["conclusion"]["payload"]["result"]["required_permission"],
            WRITE_WORK_COPY,
        )

        intake.amend_authorization(
            target_investigator="alice",
            authorized_by="保全员甲",
            reason="委托人补准在一次性 COW 层做一次调查员改名",
            add_permissions=[WRITE_WORK_COPY],
        )
        renamed = alice.rename_entry("E100", "调查员再次改名.txt")
        self.assertEqual(
            renamed["conclusion"]["payload"]["status"],
            "INVESTIGATOR_RENAME_WRITTEN_TO_PRIVATE_COW",
        )

        final = alice.request_entry(name="调查员再次改名.txt")
        self.assertEqual(final["conclusion"]["payload"]["status"], "INVESTIGATOR_WORK_PRODUCT")

        # The old conclusion is byte-for-byte the stored conclusion and is not
        # replaced by the later rename.
        stored_old = alice.get_record(old_physical_id)
        self.assertEqual(stored_old["payload"]["status"], "PHYSICAL_PRESENT_AT_INTAKE")
        self.assertEqual(
            stored_old["payload"]["result"]["original_existence_proof"]["raw_line"],
            "E100|未重放改名-旧名.txt|BLK100|P100",
        )
        self.assertNotIn("调查员再次改名", json.dumps(stored_old, ensure_ascii=False))

        stored_replay = alice.get_record(replayed_id)
        self.assertEqual(stored_replay["payload"]["status"], "REPLAYED_PRESENT")
        self.assertEqual(
            stored_replay["payload"]["result"]["replayed_matches"][0]["name"],
            "日志重放-新名.txt",
        )
        self.assertNotIn("调查员再次改名", json.dumps(stored_replay, ensure_ascii=False))

        original_directory = (case_dir / "vault" / "directory.idx").read_text(encoding="utf-8")
        self.assertIn("未重放改名-旧名.txt", original_directory)
        self.assertNotIn("调查员再次改名.txt", original_directory)
        self.assertTrue(alice.verify_ledger()["ok"])

    def test_original_line_first_write_provenance_and_page_rescope(self) -> None:
        case_dir = self.cases / "second"
        intake = IntakeDesk(case_dir)
        intake.receive(
            self.disks["second"],
            case_id="SECOND-VOL",
            source="另起一卷 SN-2",
            generation="G3",
            signed_by="委托人丙",
            collection_mode="custodian_cow",
            readable_blocks=["DIRECTORY", "JOURNAL", "LAYOUT", "BLK001"],
            page_ranges=["P001", "P002"],
        )
        intake.amend_authorization(
            target_investigator="reviewer",
            authorized_by="保全员甲",
            reason="准读原始页内容并可在私有层写入",
            add_permissions=[READ_RAW_CONTENT, WRITE_WORK_COPY],
        )
        desk = ReviewDesk(case_dir, "reviewer")

        original = desk.request_entry(name="接盘时已存在的行.txt")
        self.assertEqual(original["conclusion"]["payload"]["status"], "PHYSICAL_PRESENT_AT_INTAKE")
        original_proof_id = original["conclusion"]["id"]

        p1 = desk.request_page("P001")
        p2 = desk.request_page("P002")
        self.assertEqual(p1["conclusion"]["payload"]["status"], "RAW_PAGE_OBSERVED")
        p1_evidence_urn = p1["conclusion"]["payload"]["result"]["evidence_urn"]
        self.assertIn("原始页", p1["disclosure"]["text"])
        p2_conclusion_id = p2["conclusion"]["id"]
        self.assertIn("邻页保密文本", p2["disclosure"]["text"])

        # Narrow only the requester's page scope; source and hashes stay fixed.
        amendment = intake.amend_authorization(
            target_investigator="reviewer",
            authorized_by="保全员甲",
            reason="只保留 P001 调阅范围",
            replace_page_ranges=["P001"],
        )
        self.assertEqual(
            amendment["payload"]["source_manifest_sha256"],
            desk.intake_record["payload"]["manifest_sha256"],
        )
        p1_after = desk.request_page("P001")
        self.assertEqual(
            p1_after["conclusion"]["payload"]["result"]["evidence_urn"],
            p1_evidence_urn,
        )
        self.assertIn("原始页", p1_after["disclosure"]["text"])
        self.assertNotIn("邻页保密文本", json.dumps(p1_after["disclosure"], ensure_ascii=False))

        p2_after = desk.request_page("P002")
        self.assertEqual(p2_after["conclusion"]["payload"]["status"], "AUTHORIZATION_REQUIRED")
        self.assertNotIn("邻页保密文本", json.dumps(p2_after, ensure_ascii=False))

        block_after = desk.request_raw_block("BLK001")
        self.assertEqual(
            block_after["conclusion"]["payload"]["status"],
            "RAW_BLOCK_PAGE_SCOPE_REDACTED",
        )
        self.assertNotIn("邻页保密文本", json.dumps(block_after, ensure_ascii=False))

        # Even the old stored page conclusion cannot disclose neighboring text:
        # raw text was intentionally kept out of the append-only ledger.
        old_p2_record = desk.get_record(p2_conclusion_id)
        self.assertNotIn("邻页保密文本", json.dumps(old_p2_record, ensure_ascii=False))

        # Investigator first write and the single truthful provenance record.
        desk.open_legacy()
        written = desk.write_line("E777", "调查员首次写入的行.txt")
        self.assertEqual(written["conclusion"]["payload"]["status"], "INVESTIGATOR_LINE_WRITTEN")

        seen = desk.request_entry(name="调查员首次写入的行.txt")
        self.assertEqual(seen["conclusion"]["payload"]["status"], "INVESTIGATOR_WORK_PRODUCT")
        self.assertNotIn("original_existence_proof", seen["conclusion"]["payload"]["result"])

        provenanced = desk.confirm_investigator_first_write("E777")
        self.assertEqual(
            provenanced["conclusion"]["payload"]["status"],
            "INVESTIGATOR_FIRST_WRITE_PROVENANCED",
        )
        self.assertFalse(
            provenanced["conclusion"]["payload"]["result"]["original_existence_proof_available"]
        )

        again = desk.confirm_investigator_first_write("E777")
        self.assertEqual(
            again["conclusion"]["payload"]["status"],
            "INVESTIGATOR_FIRST_WRITE_PROVENANCE_ALREADY_EXISTS",
        )
        provenance_records = [
            r
            for r in desk.ledger.find("PROVENANCE")
            if r["payload"].get("classification") == "INVESTIGATOR_FIRST_WRITE"
        ]
        self.assertEqual(len(provenance_records), 1)

        after = desk.request_entry(name="调查员首次写入的行.txt")
        self.assertEqual(
            after["conclusion"]["payload"]["status"],
            "INVESTIGATOR_FIRST_WRITE_PROVENANCED",
        )
        self.assertIn("provenance_records", after["conclusion"]["payload"]["result"])

        # Original proof remains valid and source manifest is unchanged.
        proof_record = desk.get_record(original_proof_id)
        self.assertEqual(proof_record["payload"]["status"], "PHYSICAL_PRESENT_AT_INTAKE")
        self.assertTrue(desk.verify_ledger()["ok"])

    def test_two_investigators_have_isolated_private_cow_writes(self) -> None:
        case_dir = self._intake_case1401_cow("two-investigators")
        intake = IntakeDesk(case_dir)
        intake.amend_authorization(
            target_investigator="*",
            authorized_by="保全员甲",
            reason="两名调查员各自可在私有 COW 层试验改名",
            add_permissions=[WRITE_WORK_COPY],
        )

        alice = ReviewDesk(case_dir, "alice")
        bob = ReviewDesk(case_dir, "bob")
        alice.open_legacy()
        bob.open_legacy()

        alice.rename_entry("E100", "Alice-再次改名.txt")
        bob.rename_entry("E100", "Bob-再次改名.txt")

        alice_text = (case_dir / "work" / "alice" / "upper" / "directory.idx").read_text("utf-8")
        bob_text = (case_dir / "work" / "bob" / "upper" / "directory.idx").read_text("utf-8")
        self.assertIn("Alice-再次改名.txt", alice_text)
        self.assertNotIn("Bob-再次改名.txt", alice_text)
        self.assertIn("Bob-再次改名.txt", bob_text)
        self.assertNotIn("Alice-再次改名.txt", bob_text)

        self.assertEqual(
            alice.request_entry(name="Alice-再次改名.txt")["conclusion"]["payload"]["status"],
            "INVESTIGATOR_WORK_PRODUCT",
        )
        self.assertEqual(
            alice.request_entry(name="Bob-再次改名.txt")["conclusion"]["payload"]["status"],
            "ABSENT_IN_AUTHORIZED_SCOPE",
        )
        self.assertEqual(
            bob.request_entry(name="Bob-再次改名.txt")["conclusion"]["payload"]["status"],
            "INVESTIGATOR_WORK_PRODUCT",
        )
        self.assertEqual(
            bob.request_entry(name="Alice-再次改名.txt")["conclusion"]["payload"]["status"],
            "ABSENT_IN_AUTHORIZED_SCOPE",
        )

        original = (case_dir / "vault" / "directory.idx").read_text("utf-8")
        self.assertIn("未重放改名-旧名.txt", original)
        self.assertNotIn("Alice-再次改名.txt", original)
        self.assertNotIn("Bob-再次改名.txt", original)

        # Reopening Alice's directory sees her own upper layer, not Bob's.
        alice_reopened = ReviewDesk(case_dir, "alice")
        self.assertTrue(alice_reopened.cow.using_upper("directory.idx"))
        self.assertIn("Alice-再次改名.txt", alice_reopened.cow.read_text("directory.idx"))
        self.assertTrue(alice_reopened.verify_ledger()["ok"])


if __name__ == "__main__":
    unittest.main()
