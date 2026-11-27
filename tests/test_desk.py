from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from remediation_desk.desk import Desk
from remediation_desk.rules import (
    assess_ticket,
    closure_candidates,
    issue_closure,
    verify_closure,
)
from remediation_desk.seed import CODE, build_seed
from remediation_desk.views import engineer_board
from remediation_desk.versions import version_in_range


class VersionTests(unittest.TestCase):
    def test_fictional_range(self) -> None:
        self.assertTrue(version_in_range("1.9.4", ">=1.0.0,<2.0.0"))
        self.assertFalse(version_in_range("2.0.1-fictional", ">=1.0.0,<2.0.0"))
        self.assertFalse(version_in_range("0.9.9", ">=1.0.0,<2.0.0"))


class SeedScenarioTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "desk.json"
        self.desk = build_seed(self.path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def ticket(self, machine: str) -> dict:
        return next(t for t in self.desk.data["tickets"] if t["machine_no"] == machine)

    def test_board_groups_each_ticket_under_its_machine(self) -> None:
        board = engineer_board(self.desk.data, CODE)
        self.assertEqual(3, len(board))
        for row in board:
            self.assertEqual(1, len(row["tickets"]), row["machine_no"])
            ticket = next(
                t for t in self.desk.data["tickets"] if t["id"] == row["tickets"][0]["ticket_id"]
            )
            self.assertEqual(row["machine_no"], ticket["machine_no"])

    def test_window_goes_to_yi_and_control_first_to_jia(self) -> None:
        plan = self.desk.data["plans"][-1]
        self.assertEqual("TERM-YI", plan["window_machine"])
        self.assertEqual("TERM-JIA", plan["control_first_machine"])
        jia_order = plan["control_order"][0]
        self.assertEqual(20, jia_order["tonight_completable"])
        self.assertEqual(20, jia_order["carried_unfinished"])
        self.assertEqual("SHIFT-2-FRI-PM", jia_order["control_through_shift"])

    def test_three_machines_and_corrected_scope(self) -> None:
        self.assertEqual(
            {"TERM-JIA", "TERM-YI", "TERM-BING"},
            {m["machine_no"] for m in self.desk.data["machines"]},
        )
        adv = self.desk.active_advisory(CODE)
        self.assertEqual("ADV-DCP710-R2", adv["id"])
        # R1 incorrectly excluded 1.9.4; corrected R2 creates the BING finding.
        assessment = assess_ticket(self.desk.data, self.ticket("TERM-BING"))
        keys = {i["key"] for i in assessment["items"]}
        self.assertIn("installed:BING-single", keys)

    def test_whole_new_image_still_has_old_subcomponent(self) -> None:
        dossier_report = self.desk.latest_inventory("TERM-JIA")
        self.assertEqual("7.2.0-fictional", dossier_report["whole_version"])
        # Historical open report proves the old subcomponent was visible.
        old = next(r for r in self.desk.reports_for("TERM-JIA") if r["id"] == "INV-JIA-YDAY-OPEN")
        self.assertEqual("1.8.4", old["components"][0]["installed_version"])

    def test_only_yi_has_effective_final_closure_in_canonical_end_state(self) -> None:
        yi = verify_closure(self.desk.data, "CL-YI-FINAL")
        jia = verify_closure(self.desk.data, "CL-JIA-TMP-YDAY")
        bing = verify_closure(self.desk.data, "CL-BING-BRIEF-FINAL")

        self.assertTrue(yi["record_valid"])
        self.assertTrue(yi["effective_under_current_bulletin"])
        self.assertTrue(jia["record_valid"])
        self.assertTrue(jia["effective_under_current_bulletin"])
        self.assertEqual("TEMPORARY_CONTROL", jia["kind"])
        self.assertTrue(bing["record_valid"], "old closure record is retained")
        self.assertFalse(bing["effective_under_current_bulletin"])

        candidates = closure_candidates(self.desk.data, CODE)
        self.assertEqual([], candidates["candidates"])

    def test_jia_can_earn_final_disablement_closure_with_one_new_receipt(self) -> None:
        ticket = self.ticket("TERM-JIA")
        before = assess_ticket(self.desk.data, ticket)
        self.assertFalse(before["routes"]["FUNCTIONS_DISABLED"]["eligible"])

        self.desk.add_disablement_receipt(
            "TERM-JIA",
            receipt_id="DIS-JIA-DURABLE",
            covered_uids=["JIA-batch", "JIA-single"],
            covered_functions=["BATCH_READ", "SINGLE_READ"],
            durable=True,
            old_processes_stopped=True,
            control="FEATURE_REMOVED_AND_CONFIG_LOCKED_AFTER_RESTART",
        )
        closure = issue_closure(
            self.desk.data,
            "TERM-JIA",
            closure_id="CL-JIA-FINAL",
            kind="FINAL",
            route="FUNCTIONS_DISABLED",
        )
        validity = verify_closure(self.desk.data, closure["id"])
        self.assertTrue(validity["effective_under_current_bulletin"])

    def test_staffing_report_flip_does_not_rewrite_temporary_closure(self) -> None:
        before = verify_closure(self.desk.data, "CL-JIA-TMP-YDAY")
        self.desk.add_staffing_report(
            "DESK-YI",
            "SHIFT-1-FRI-AM",
            True,
            report_id="STAFF-YI-AM-CONFIRMED-FLIP",
            source="signed-roster",
            reliable=True,
        )
        after = verify_closure(self.desk.data, "CL-JIA-TMP-YDAY")
        self.assertEqual(before["scope_explanation"], after["scope_explanation"])
        self.assertTrue(after["record_valid"])
        self.assertEqual("NONE_REQUIRED", after["staffing_dependency"])
        ids = [r["id"] for r in self.desk.data["staffing_reports"]]
        self.assertIn("STAFF-YI-AM-ORIGINAL", ids)
        self.assertIn("STAFF-YI-AM-CONFIRMED-FLIP", ids)

    def test_bing_spare_loan_does_not_fix_machine_and_revokes_promise(self) -> None:
        ticket = self.ticket("TERM-BING")
        self.assertEqual("OPEN", ticket["status"])
        self.assertEqual("IMPOSSIBLE", ticket["promise"]["status"])
        self.assertFalse(self.desk.spare_available("SPARE-BING-1"))
        assessment = assess_ticket(self.desk.data, ticket)
        self.assertGreater(assessment["open_item_count"], 0)

    def test_pure_withdrawal_without_successor_allows_no_new_advisory_closure(self) -> None:
        self.desk.active_advisory(CODE)["status"] = "WITHDRAWN"
        candidates = closure_candidates(self.desk.data, CODE)
        self.assertIsNone(candidates["active_advisory_id"])
        self.assertIn("withdrawn", candidates["reason"])
        with self.assertRaises(ValueError):
            issue_closure(self.desk.data, "TERM-YI", kind="FINAL")
        writeoff = issue_closure(
            self.desk.data,
            "TERM-YI",
            closure_id="CL-WRITEOFF",
            kind="WITHDRAWN_BULLETIN",
        )
        writeoff_check = verify_closure(self.desk.data, writeoff["id"])
        self.assertFalse(writeoff_check["rectification_closure"])
        self.assertTrue(writeoff_check["record_valid"])
        yi = verify_closure(self.desk.data, "CL-YI-FINAL")
        self.assertTrue(yi["historical_maintenance_receipt_valid"])
        self.assertFalse(yi["effective_under_current_bulletin"])


class EvidenceTransitionTests(unittest.TestCase):
    def _desk_with_old_single(self) -> Desk:
        desk = Desk()
        desk.data["shift_order"] = ["S0", "S1"]
        desk.data["current_shift"] = "S0"
        desk.add_advisory(
            code="ADV",
            revision=1,
            part_numbers=["P-1"],
            affected_range=">=1.0.0,<2.0.0",
            fixed_version="2.0.0",
        )
        desk.register_machine("M1")
        desk.create_ticket("M1", "ADV", ticket_id="T1")
        desk.submit_inventory(
            "M1",
            [
                {
                    "uid": "u1",
                    "part_no": "P-1",
                    "installed_version": "1.4.0",
                    "function": "SINGLE_READ",
                    "function_enabled": True,
                    "processes": [{"pid": 100, "running_version": "1.4.0", "state": "RUNNING"}],
                }
            ],
            report_id="R1",
        )
        return desk

    def test_enablement_condition_excludes_never_enabled_dormant_part(self) -> None:
        desk = Desk()
        desk.add_advisory(
            code="ADV",
            revision=1,
            part_numbers=["P-1"],
            affected_range=">=1.0.0,<2.0.0",
            fixed_version="2.0.0",
            enablement="FUNCTION_ENABLED",
        )
        desk.register_machine("M2")
        ticket = desk.create_ticket("M2", "ADV", ticket_id="T2")
        desk.submit_inventory(
            "M2",
            [
                {
                    "uid": "dormant",
                    "part_no": "P-1",
                    "installed_version": "1.4.0",
                    "function": "BATCH_READ",
                    "function_enabled": False,
                    "processes": [],
                }
            ],
            report_id="R-DORMANT",
        )
        assessment = assess_ticket(desk.data, ticket)
        self.assertEqual([], assessment["items"])
        self.assertEqual(0, assessment["open_item_count"])

    def test_installed_fix_with_old_running_process_is_not_enough(self) -> None:
        desk = self._desk_with_old_single()
        desk.submit_inventory(
            "M1",
            [
                {
                    "uid": "u1",
                    "part_no": "P-1",
                    "installed_version": "2.0.0",
                    "function": "SINGLE_READ",
                    "function_enabled": True,
                    "processes": [{"pid": 100, "running_version": "1.4.0", "state": "RUNNING"}],
                }
            ],
            report_id="R2-STALE-PROCESS",
        )
        assessment = assess_ticket(desk.data, "T1")
        self.assertFalse(assessment["routes"]["REPLACED_NEW_PROCESS"]["eligible"])
        self.assertTrue(
            any("old decoder process is still running" in m for m in assessment["routes"]["REPLACED_NEW_PROCESS"]["missing"])
        )

    def test_inventory_alone_with_exit_and_new_process_closes(self) -> None:
        desk = self._desk_with_old_single()
        desk.submit_inventory(
            "M1",
            [
                {
                    "uid": "u1",
                    "part_no": "P-1",
                    "installed_version": "2.0.0",
                    "function": "SINGLE_READ",
                    "function_enabled": True,
                    "processes": [
                        {"pid": 100, "running_version": "1.4.0", "state": "EXITED"},
                        {"pid": 200, "running_version": "2.0.0", "state": "RUNNING"},
                    ],
                }
            ],
            report_id="R3-GOOD-COUNT",
        )
        closure = issue_closure(desk.data, "M1", closure_id="C1", kind="FINAL")
        self.assertEqual("REPLACED_NEW_PROCESS", closure["route"])
        self.assertIn("R3-GOOD-COUNT", closure["evidence_ids"])

    def test_reliable_disablement_route(self) -> None:
        desk = self._desk_with_old_single()
        desk.submit_inventory(
            "M1",
            [
                {
                    "uid": "u1",
                    "part_no": "P-1",
                    "installed_version": "1.4.0",
                    "function": "SINGLE_READ",
                    "function_enabled": False,
                    "processes": [{"pid": 100, "running_version": "1.4.0", "state": "EXITED"}],
                }
            ],
            report_id="R2-DISABLED",
        )
        # A config report alone is not treated as supplier-grade reliable evidence.
        self.assertFalse(assess_ticket(desk.data, "T1")["routes"]["FUNCTIONS_DISABLED"]["eligible"])
        desk.add_disablement_receipt(
            "M1",
            receipt_id="D1",
            covered_uids=["u1"],
            durable=True,
            old_processes_stopped=True,
        )
        closure = issue_closure(desk.data, "M1", kind="FINAL", route="FUNCTIONS_DISABLED")
        self.assertTrue(verify_closure(desk.data, closure["id"])["effective_under_current_bulletin"])


if __name__ == "__main__":
    unittest.main()
