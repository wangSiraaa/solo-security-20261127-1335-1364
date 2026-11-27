from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from proofdesk.actors import AuthorizationDenied, build_task_pass
from proofdesk.environment import BOOK_ID, DeskEnvironment
from proofdesk.scenario import (
    LINE_ID,
    run_hosted_case,
    run_locally_known_revocation_case,
    run_main_story,
    run_portable_order_case,
    setup_mandate_and_pass,
)


class MainStoryTests(unittest.TestCase):
    def test_three_people_two_chapters_delegation_lock_and_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_main_story(Path(tmp) / "main")

            statement = result["terminal_statement"]
            for text in ["陈墨", "林岚", "周砚", "林岚 →", "chapter.edit", "ch1", "有效期"]:
                self.assertIn(text, statement)

            modes = [rev["mode"] for rev in result["revisions"]]
            self.assertEqual(
                modes,
                ["author_self", "portable_pass", "editor_self", "editor_self"],
            )
            operators = [
                (rev["rev_id"], rev["actual_operator"]["name"])
                for rev in result["revisions"]
            ]
            self.assertEqual(
                operators,
                [
                    ("rev-0001", "林岚"),
                    ("rev-0002", "陈墨"),
                    ("rev-0003", "周砚"),
                    ("rev-0004", "周砚"),
                ],
            )

            self.assertTrue(result["station_refused_new_pass"])
            self.assertEqual(result["negative_result"]["status"], "rejected")
            self.assertEqual(
                result["negative_result"]["code"],
                "redelegation_clause_absent",
            )

            self.assertEqual(result["pending_result"]["status"], "pending_merge")
            self.assertIn(
                "stale_base_revision",
                result["pending_result"]["reasons"],
            )
            self.assertIn(
                "chapter_locked_for_other_proof_cycle",
                result["pending_result"]["reasons"],
            )
            self.assertEqual(len(result["pending_merges"]), 1)

            ch1 = result["chapter1_text"]
            self.assertIn("人声鼎沸", ch1)
            self.assertIn("停下脚步", ch1)
            self.assertNotIn("走过灯市", ch1)
            self.assertIn("走过灯市", result["pending_merges"][0]["package"]["replacements"][0]["new"])

            ch2 = result["chapter2_text"]
            self.assertIn("缝隙里", ch2)
            self.assertIn("衣角却先湿了", ch2)

            # A genuinely new object/process reads the same revision numbers.
            reopened = DeskEnvironment(Path(result["state_dir"]))
            report = reopened.library.author_report_text()
            self.assertIn("rev-0001", report)
            self.assertIn("rev-0004", report)
            self.assertIn("merge-001", report)
            self.assertEqual(len(reopened.library.revision_report()), 4)

            merged = reopened.library.merge_pending(
                result["pending_result"]["merge_id"],
                resolver_id="alin",
                resolver_name="林岚",
            )
            self.assertEqual(merged["rev_id"], "rev-0005")
            merged_text = reopened.library.chapter_text("ch1")
            self.assertIn("人声鼎沸", merged_text)
            self.assertIn("停下脚步", merged_text)
            self.assertIn("走过灯市", merged_text)
            reloaded = DeskEnvironment(Path(result["state_dir"]))
            self.assertEqual(len(reloaded.library.revision_report()), 5)
            self.assertIn("manual_merge", reloaded.library.author_report_text())

            cabinet = Path(result["cabinet_root"])
            self.assertTrue((cabinet / "proof_terminal" / "inbox").exists())
            credential_files = [
                path for path in cabinet.rglob("*.json")
                if '"kind": "task_pass"' in path.read_text(encoding="utf-8")
            ]
            self.assertTrue(credential_files)
            self.assertTrue(Path(result["state_dir"], "trust-keys.json").exists())

            choices = result["delivery_choices"]["choices"]
            self.assertTrue(any(c["mode"] == "portable_pass" and c["accepted"] for c in choices))


class OfflinePortableCredentialTests(unittest.TestCase):
    def test_edit_before_revoke_lands_and_later_sync_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = run_portable_order_case(Path(tmp), "edit_first")
            self.assertEqual(case["first_edit_status"], "landed")
            self.assertEqual(case["first_rev_id"], "rev-0001")
            self.assertEqual(case["second_status"], "rejected")
            self.assertEqual(case["second_code"], "locally_revoked")
            self.assertTrue(case["landed_ding_character"])
            self.assertFalse(case["old_top_typo_remaining"])

    def test_global_revoke_before_edit_is_invisible_until_local_sync(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = run_portable_order_case(Path(tmp), "revoke_first")
            # The author/station order was revoke-first, but the library had no
            # local revocation basis while disconnected.
            self.assertEqual(case["first_edit_status"], "landed")
            edit_step = next(
                step for step in case["timeline"]
                if step["step"] == "edit_present_portable_pass_to_library"
            )
            self.assertEqual(edit_step["library_known_revocations"], [])
            self.assertTrue(case["landed_ding_character"])

            self.assertEqual(case["second_status"], "rejected")
            self.assertEqual(case["second_code"], "locally_revoked")
            self.assertEqual(len(case["library_known_revocation_ids"]), 1)

    def test_revocation_already_deposited_locally_blocks_even_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = run_locally_known_revocation_case(Path(tmp))
            self.assertEqual(case["status"], "rejected")
            self.assertEqual(case["code"], "locally_revoked")
            self.assertIn("人声顶沸", case["text"])
            self.assertNotIn("人声鼎沸", case["text"])


class HostedStationTests(unittest.TestCase):
    def test_hosted_flow_records_actual_operator_and_rejects_self_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_hosted_case(Path(tmp))
            self.assertEqual(result["landed_rev_id"], "rev-0001")
            self.assertEqual(result["landed_mode"], "hosted_station")
            self.assertEqual(len(result["actual_operator_log"]), 1)
            log = result["actual_operator_log"][0]
            self.assertEqual(log["actual_operator_id"], "chenmo")
            self.assertEqual(log["represented_author"], "alin")
            self.assertTrue(result["after_revoke_station"])
            self.assertEqual(
                result["after_revoke_station"][0]["status"],
                "stopped_after_revocation",
            )
            self.assertEqual(result["claim_status"], "rejected")
            self.assertEqual(
                result["claim_code"],
                "hosted_claim_not_accepted_offline",
            )
            self.assertIn("陈墨", result["report_after_restart"])

            restarted_env = DeskEnvironment(Path(result["state_dir"]))
            self.assertIn(LINE_ID, restarted_env.station.revoked_targets)
            self.assertEqual(len(restarted_env.station.actual_operator_log), 1)

    def test_hosted_station_does_not_transfer_representative_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = DeskEnvironment(Path(tmp))
            env.author_desk.issue_mandate(
                env.station.person,
                station_id=env.station.station_id,
                book_id=BOOK_ID,
                chapter_ids=["ch1"],
                mandate_id="mandate-no-redelegate-v1",
                line_id="mandate-no-redelegate",
                version=1,
                edit_self=True,
                redelegate=False,
            )
            env.sync_station()
            env.deliver_to_library()

            env.terminal.request_hosted(
                book_id=BOOK_ID,
                chapter_id="ch1",
                base_rev_id="rev-0000",
                replacements=[{"old": "灯市如昼", "new": "灯市如旧"}],
                summary="无转委托条款，但责编站仍可本人办理并记录实际操作者",
                library_connected=True,
            )
            env.sync_station()
            results = env.deliver_to_library()
            landed = next(r for r in results if r.get("landed"))
            self.assertEqual(landed["outcome"]["mode"], "hosted_station")
            self.assertEqual(
                landed["revision"]["actual_operator"]["id"],
                "chenmo",
            )

            env.library.set_station_link(False)
            claim = env.terminal.submit_hosted_claim_only(
                book_id=BOOK_ID,
                chapter_id="ch1",
                base_rev_id=landed["rev_id"],
                replacements=[{"old": "青灯亮起时", "new": "青灯未亮时"}],
                summary="校录员仍不能把自己当成代理人直接送件",
            )
            direct_results = env.deliver_to_library()
            rejected = next(
                r for r in direct_results
                if r.get("receipt", {}).get("package_id") == claim["id"]
            )
            self.assertEqual(rejected["outcome"]["code"], "hosted_claim_not_accepted_offline")

    def test_disconnected_station_package_queued_then_revocation_wins_before_pump(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = DeskEnvironment(Path(tmp))
            setup_mandate_and_pass(env)
            env.library.set_station_link(False)

            env.terminal.request_hosted(
                book_id=BOOK_ID,
                chapter_id="ch1",
                base_rev_id="rev-0000",
                replacements=[{"old": "青灯亮起时", "new": "青灯初上时"}],
                summary="断联前向站申请并由站封存",
                library_connected=False,
            )
            env.sync_station()
            self.assertEqual(env.deliver_to_library(), [])
            outbox = list((env.cabinet.root / "editor_station" / "outbox").glob("*.json"))
            self.assertEqual(len(outbox), 1)

            env.author_desk.revoke(
                LINE_ID,
                "mandate_line",
                "在恢复书库链路前撤回",
                library_connected=False,
            )
            env.sync_station()

            env.cabinet.pump("author_desk", "library")
            env.deliver_to_library()
            env.cabinet.pump("editor_station", "library")
            results = env.deliver_to_library()
            self.assertEqual(results[0]["status"], "rejected")
            self.assertEqual(results[0]["outcome"]["code"], "locally_revoked")
            self.assertEqual(len(env.library.revision_report()), 0)


class AuthorizationEdgeTests(unittest.TestCase):
    def test_expired_pass_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = DeskEnvironment(Path(tmp))
            mandate, _ = setup_mandate_and_pass(env)
            expired = build_task_pass(
                env.trust,
                pass_id="pass-expired",
                mandate=mandate,
                editor=env.station.person,
                proofreader=env.terminal.person,
                chapter_ids=["ch1"],
                valid_minutes=-1,
            )
            pkg = env.terminal.submit_portable(
                mandate=mandate,
                task_pass=expired,
                book_id=BOOK_ID,
                chapter_id="ch1",
                base_rev_id="rev-0000",
                replacements=[{"old": "灯市如昼", "new": "灯市如昼"}],
                summary="过期凭据尝试",
            )
            results = env.deliver_to_library()
            result = next(r for r in results if r.get("receipt", {}).get("package_id") == pkg["id"])
            self.assertEqual(result["status"], "rejected")
            self.assertEqual(result["outcome"]["code"], "pass_expired")

    def test_tampered_mandate_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = DeskEnvironment(Path(tmp))
            mandate, task_pass = setup_mandate_and_pass(env)
            tampered = dict(mandate)
            tampered["permissions"] = {"edit_self": True, "redelegate": True}
            # A fresh dict retains signature but changed signed content.
            tampered["chapter_ids"] = ["ch1", "ch2", "ch9"]
            pkg = env.terminal.submit_portable(
                mandate=tampered,
                task_pass=task_pass,
                book_id=BOOK_ID,
                chapter_id="ch1",
                base_rev_id="rev-0000",
                replacements=[{"old": "灯市如昼", "new": "灯市如旧"}],
                summary="篡改上级授权",
            )
            results = env.deliver_to_library()
            result = next(r for r in results if r.get("receipt", {}).get("package_id") == pkg["id"])
            self.assertEqual(result["status"], "rejected")
            self.assertEqual(result["outcome"]["code"], "bad_mandate_signature")

    def test_station_cannot_issue_pass_without_redelegation_fact(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = DeskEnvironment(Path(tmp))
            mandate, _ = setup_mandate_and_pass(env)
            removed = env.author_desk.remove_redelegation(mandate)
            env.sync_station()
            with self.assertRaises(AuthorizationDenied):
                env.station.issue_pass(
                    env.terminal.person,
                    pass_id="nope",
                    mandate=removed,
                    chapter_ids=["ch1"],
                )


if __name__ == "__main__":
    unittest.main()
