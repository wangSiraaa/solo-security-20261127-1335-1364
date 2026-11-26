"""End-to-end tests for the secret print-room release service.

Run with::

    python3 -m unittest discover -s tests -v
"""

import os
import tempfile
import unittest

from secureprint import (
    AmbiguousOwnership,
    AlreadyDelivered,
    CustodyStore,
    ForeignContent,
    LocKind,
    NoFreeCell,
    NotComplete,
    ReceiptInvalid,
    ReleaseService,
    SameCellConflict,
    Side,
    UnsafeLocation,
)
from secureprint.scenarios import build_one_free_cell_world, run_two_arrival_changes


def make_svc(path=None, clock=None):
    store = CustodyStore(path) if path else None
    return ReleaseService(store=store, clock=clock)


def single_page_world(svc, *, recipient="甲", job="J1", serial="S1", cell="C1"):
    svc.add_cell(cell, owner=recipient)
    svc.sign_commitment(recipient, job, in_person=True)
    svc.submit_job(job, recipient, pages=1, in_person=True)
    svc.place_confirmed_sheet(serial, job, 1, recipient, cell)
    return serial


class ReconstructionGatingTests(unittest.TestCase):
    def test_both_faces_confirmed_and_in_cell_can_release(self):
        svc = make_svc()
        serial = single_page_world(svc)
        rec = svc.reconstruct(serial)
        self.assertEqual(rec["confirmed_owner"], "甲")
        self.assertTrue(rec["safely_available"])
        self.assertTrue(svc.can_release("甲"))
        out = svc.prepare_release("甲", present=True)
        self.assertTrue(out.delivered)
        self.assertEqual(out.sheets, [serial])

    def test_front_only_should_not_release(self):
        svc = make_svc()
        svc.add_cell("C1", owner="甲")
        svc.sign_commitment("甲", "J1", in_person=True)
        svc.submit_job("J1", "甲", pages=1, in_person=True)
        svc.register_sheet("S1", "J1", 1)
        svc.report_face("S1", Side.FRONT, candidates=["甲"])
        svc.report_receipt("S1", LocKind.CELL, cell_id="C1")
        with self.assertRaises(AmbiguousOwnership):
            svc.prepare_release("甲", present=True)

    def test_back_face_could_be_someone_else_blocks_release(self):
        """Even if the back *might* carry 乙's content, 甲 may not get it."""
        svc = make_svc()
        svc.add_cell("C1", owner="甲")
        svc.sign_commitment("甲", "J1", in_person=True)
        svc.submit_job("J1", "甲", pages=1, in_person=True)
        svc.register_sheet("S1", "J1", 1)
        svc.report_face("S1", Side.FRONT, candidates=["甲"])
        svc.report_face("S1", Side.BACK, candidates=["甲", "乙"])  # ambiguous
        svc.report_receipt("S1", LocKind.CELL, cell_id="C1")
        with self.assertRaises(AmbiguousOwnership):
            svc.prepare_release("甲", present=True)
        # physically in a cell, but NOT safely releasable
        self.assertFalse(svc.reconstruct("S1")["safely_available"])
        self.assertFalse(svc.can_release("甲"))

    def test_provably_foreign_sheet_refused(self):
        svc = make_svc()
        svc.add_cell("C1", owner="甲")
        svc.add_cell("C2", owner="乙")
        svc.sign_commitment("甲", "J1", in_person=True)
        svc.submit_job("J1", "甲", pages=1, in_person=True)
        svc.register_sheet("S1", "J1", 1)
        svc.report_face("S1", Side.FRONT, candidates=["甲"])
        svc.report_face("S1", Side.BACK, candidates=["乙"])  # provably 乙
        svc.report_receipt("S1", LocKind.CELL, cell_id="C1")
        with self.assertRaises(ForeignContent):
            svc.prepare_release("甲", present=True)

    def test_face_missing_treated_as_unknown(self):
        svc = make_svc()
        svc.add_cell("C1", owner="甲")
        svc.sign_commitment("甲", "J1", in_person=True)
        svc.submit_job("J1", "甲", pages=1, in_person=True)
        svc.register_sheet("S1", "J1", 1)
        svc.report_face("S1", Side.FRONT, candidates=["甲"])
        # no back report at all
        svc.report_receipt("S1", LocKind.CELL, cell_id="C1")
        self.assertIsNone(svc.reconstruct("S1")["possible_owners"])
        with self.assertRaises(AmbiguousOwnership):
            svc.prepare_release("甲", present=True)


class LateReportInvalidationTests(unittest.TestCase):
    def test_late_ambiguous_back_under_old_work_order_invalidates_slip(self):
        svc = make_svc()
        serial = single_page_world(svc)
        slip = svc.sign_claim("甲")
        self.assertTrue(svc.validate_ticket(slip.ticket_id))

        # A late back-face report arrives under an OLD work order, and it can
        # no longer distinguish 甲 from 乙.
        svc.report_face(
            serial,
            Side.BACK,
            job_id="OLD-JOB-42",
            candidates=["甲", "乙"],
            reported_by="断班前的旧班次",
            note="换人后才传来的背面完成回报",
        )
        self.assertFalse(svc.validate_ticket(slip.ticket_id))
        self.assertFalse(svc.ticket(slip.ticket_id)["valid"])
        with self.assertRaises(ReceiptInvalid):
            svc.deliver(slip.ticket_id, present=True)

    def test_restore_faces_then_tray_receipt_quarantines(self):
        svc = make_svc()
        serial = single_page_world(svc)
        old = svc.sign_claim("甲")

        # 1) back becomes ambiguous -> old slip invalid
        svc.report_face(serial, Side.BACK, job_id="OLD", candidates=["甲", "乙"])
        self.assertFalse(svc.validate_ticket(old.ticket_id))

        # 2) restore both faces to 甲
        svc.report_face(serial, Side.BACK, candidates=["甲"])
        self.assertEqual(svc.reconstruct(serial)["confirmed_owner"], "甲")

        # 3) now change only the drop receipt to an open waste tray
        svc.report_receipt(serial, LocKind.TRAY, detail="敞口废纸盘")
        rec = svc.reconstruct(serial)
        self.assertEqual(rec["status"], "quarantined")
        self.assertFalse(rec["safely_available"])

        # an incident was logged
        kinds = [e["kind"] for e in svc.audit_log()]
        self.assertIn("incident.quarantine", kinds)

        # cannot be signed as safely delivered
        with self.assertRaises(UnsafeLocation):
            svc.sign_claim("甲")

        # nothing was registered as delivered for 甲
        self.assertEqual(svc.ledger_entries(), [])
        self.assertFalse(svc.who_received(old.ticket_id)["delivered"])

        # clerk can safely dispose the quarantined sheet
        svc.dispose_quarantined(serial, clerk="柜员-07")
        self.assertEqual(svc.reconstruct(serial)["status"], "disposed")

    def test_old_slip_does_not_revive_after_new_slip(self):
        svc = make_svc()
        serial = single_page_world(svc)
        old = svc.sign_claim("甲")
        svc.report_face(serial, Side.BACK, candidates=["甲", "乙"])
        svc.report_face(serial, Side.BACK, candidates=["甲"])  # resolved
        new = svc.sign_claim("甲")
        self.assertFalse(svc.validate_ticket(old.ticket_id))
        self.assertTrue(svc.validate_ticket(new.ticket_id))
        out = svc.deliver(new.ticket_id, present=True)
        self.assertTrue(out.delivered)
        with self.assertRaises(AlreadyDelivered):
            svc.deliver(new.ticket_id, present=True)


class CardJamReprintTests(unittest.TestCase):
    def test_jam_requires_clear_and_reprint_new_physical_sheet(self):
        svc = make_svc()
        svc.add_cell("C1", owner="甲")
        svc.sign_commitment("甲", "J1", in_person=True)
        svc.submit_job("J1", "甲", pages=1, in_person=True)
        svc.register_sheet("S1", "J1", 1)
        svc.report_face("S1", Side.FRONT, candidates=["甲"])
        svc.report_face("S1", Side.BACK, candidates=["甲"])
        svc.mark_card_jam("S1", detail="卡在定影")
        self.assertEqual(svc.reconstruct("S1")["status"], "quarantined")
        # cannot release the jammed sheet
        with self.assertRaises(UnsafeLocation):
            svc.sign_claim("甲")

        new = svc.clamp_clear_and_reprint("S1", "S1-R", station="C1")
        self.assertEqual(new.reprint_of, "S1")
        # carry-over of face reports means the reprint is still attributable
        self.assertEqual(svc.reconstruct("S1-R")["confirmed_owner"], "甲")
        # old jammed sheet disposed, repro is a distinct physical sheet
        self.assertEqual(svc.reconstruct("S1")["status"], "disposed")

        svc.report_receipt("S1-R", LocKind.CELL, cell_id="C1")
        out = svc.prepare_release("甲", present=True)
        self.assertTrue(out.delivered)
        self.assertEqual(out.sheets, ["S1-R"])

    def test_two_page_job_only_complete_after_both_pages(self):
        svc = make_svc()
        svc.add_cell("C1", owner="甲")
        svc.add_cell("C2", owner="甲")
        svc.sign_commitment("甲", "J2", in_person=True)
        svc.submit_job("J2", "甲", pages=2, in_person=True)
        svc.place_confirmed_sheet("P1", "J2", 1, "甲", "C1")
        self.assertEqual(svc.missing_pages("甲"), [2])
        with self.assertRaises(NotComplete):
            svc.sign_claim("甲")
        svc.place_confirmed_sheet("P2", "J2", 2, "甲", "C2")
        self.assertEqual(svc.missing_pages("甲"), [])
        out = svc.prepare_release("甲", present=True)
        self.assertEqual(sorted(out.sheets), ["P1", "P2"])


class TwoPickupScenarioTests(unittest.TestCase):
    def _world(self):
        svc = make_svc()
        info = build_one_free_cell_world(svc, jia_job="JIA", yi_job="YI")
        return svc, info

    def test_exactly_one_free_cell(self):
        svc, info = self._world()
        self.assertEqual(info["free"], ["FREE"])
        self.assertEqual(len(svc.free_cells()), 1)
        # both parties can use the one generic free cell
        self.assertIn("FREE", svc.free_cells_for("甲"))
        self.assertIn("FREE", svc.free_cells_for("乙"))

    def test_甲_first_frees_two_cells_but_late_甲_strands_乙(self):
        svc, _ = self._world()
        res = run_two_arrival_changes(svc)
        # 甲 late, 乙 on time -> recommend 乙 first, both complete
        d = res["甲晚到、乙按期"]["decision"]
        self.assertEqual(d["recommended_order"], ["乙", "甲"])
        jia_first = d["candidates"]["甲->乙"]
        self.assertIn("乙", jia_first["empty_handed"])
        yi_first = d["candidates"]["乙->甲"]
        self.assertEqual(sorted(yi_first["completes"]), ["乙", "甲"])

    def test_乙_reschedule_makes_jia_first_better(self):
        svc, _ = self._world()
        res = run_two_arrival_changes(svc)
        d = res["乙改期、甲按期"]["decision"]
        self.assertEqual(d["recommended_order"], ["甲", "乙"])
        yi_first = d["candidates"]["乙->甲"]
        self.assertIn("甲", yi_first["empty_handed"])
        jia_first = d["candidates"]["甲->乙"]
        self.assertEqual(sorted(jia_first["completes"]), ["乙", "甲"])

    def test_risks_are_reported_for_each_order(self):
        svc, _ = self._world()
        res = run_two_arrival_changes(svc)
        cands = res["甲晚到、乙按期"]["decision"]["candidates"]
        self.assertTrue(any("甲" in r for r in cands["甲->乙"]["risks"]))
        self.assertTrue(any("乙" in r for r in cands["乙->甲"]["risks"]))

    def test_choose_does_not_mutate_live_state(self):
        svc, _ = self._world()
        before = svc._snapshot()
        run_two_arrival_changes(svc)
        after = svc._snapshot()
        self.assertEqual(before["sheets"], after["sheets"])
        self.assertEqual(before["ledger"], after["ledger"])
        self.assertEqual([c["cell_id"] for c in svc.cells()],
                         [c["cell_id"] for c in svc.cells()])
        # still exactly one free cell and no paper placed by the simulation
        self.assertEqual(svc.free_cells(), ["FREE"])

    def test_execute_produces_consistent_ledger(self):
        svc, _ = self._world()
        from secureprint.planner import Planner
        from secureprint.scenarios import default_parties
        parties = default_parties()
        planner = Planner(svc, list(parties.values()))
        # 甲 early, 乙 reschedules -> 甲 first is feasible and completes both
        cand = planner.execute(("甲", "乙"), {"甲": 2.0, "乙": 5.0})
        self.assertEqual(sorted(cand.completes), sorted(["甲", "乙"]))
        # every delivered sheet appears in exactly one ledger entry
        seen = {}
        for e in svc.ledger_entries():
            for s in e["sheets"]:
                self.assertNotIn(s, seen, f"{s} delivered twice")
                seen[s] = e["ticket_id"]
        # JIA page1, printed page2, and 乙's page all delivered
        self.assertIn("JIA-P1", seen)


class DoubleSigningGuardTests(unittest.TestCase):
    def test_live_slip_reserves_cell_and_blocks_reuse(self):
        svc = make_svc()
        svc.add_cell("C1", owner="甲")
        svc.add_cell("C2", owner="乙")
        svc.sign_commitment("甲", "J1", in_person=True)
        svc.sign_commitment("乙", "J2", in_person=True)
        svc.submit_job("J1", "甲", pages=1, in_person=True)
        svc.submit_job("J2", "乙", pages=1, in_person=True)
        svc.place_confirmed_sheet("A1", "J1", 1, "甲", "C1")
        # 甲 signs but is not present -> slip reserves C1 for 甲
        slip = svc.sign_claim("甲")
        self.assertFalse(slip.delivered)
        self.assertEqual(
            [c for c in svc.cells() if c["cell_id"] == "C1"][0]["signed_to"], "甲"
        )
        # the cell is physically occupied, so 乙 cannot be placed there at all
        with self.assertRaises(Exception):
            svc.place_confirmed_sheet("B1", "J2", 1, "乙", "C1")

    def test_same_sheet_cannot_be_in_two_live_slips(self):
        svc = make_svc()
        single_page_world(svc, recipient="甲", job="J1", serial="S1", cell="C1")
        slip = svc.sign_claim("甲")
        # signing the same (sheet, cell) again while the first slip is live is
        # a same-sheet/same-cell conflict, not a second delivery
        with self.assertRaises(SameCellConflict):
            svc.sign_claim("甲")

    def test_revoked_slip_frees_lease_for_release(self):
        svc = make_svc()
        single_page_world(svc)
        slip = svc.sign_claim("甲")
        svc.revoke_ticket(slip.ticket_id, reason="柜员核实")
        self.assertFalse(svc.validate_ticket(slip.ticket_id))
        # a fresh slip can be signed after revoke (actual arrival resolves it)
        new = svc.sign_claim("甲")
        self.assertNotEqual(new.ticket_id, slip.ticket_id)
        out = svc.deliver(new.ticket_id, present=True)
        self.assertTrue(out.delivered)

    def test_two_different_deliveries_never_share_a_sheet(self):
        svc = make_svc()
        serial = single_page_world(svc, recipient="甲", job="J1", serial="S1", cell="C1")
        out = svc.prepare_release("甲", present=True)
        self.assertEqual(out.sheets, [serial])
        # once delivered, the same sheet cannot be signed again
        svc.sign_commitment("乙", "J2", in_person=True)
        svc.submit_job("J2", "乙", pages=1, in_person=True)
        # 乙's job has no live sheet for page 1 -> not complete, not S1 reuse
        with self.assertRaises(NotComplete):
            svc.sign_claim("乙")
        # ledger shows exactly one receipt for S1
        entries = [e for e in svc.ledger_entries() if serial in e["sheets"]]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["recipient"], "甲")


class PersistenceTests(unittest.TestCase):
    def test_reopen_answers_who_received_what_by_ticket(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "custody.db")
        svc = make_svc(path)
        serial = single_page_world(svc)
        out = svc.prepare_release("甲", present=True)
        ticket = out.ticket_id
        svc.save_and_close()

        reopened = ReleaseService.reopen(CustodyStore(path))
        who = reopened.who_received(ticket)
        self.assertTrue(who["delivered"])
        self.assertEqual(who["recipient"], "甲")
        self.assertEqual(who["sheets"], [serial])
        # audit trail survived too
        kinds = [e["kind"] for e in reopened.audit_log()]
        self.assertIn("claim.delivered", kinds)
        reopened.save_and_close()

    def test_reopen_preserves_pending_ticket_validity(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "custody.db")
        svc = make_svc(path)
        single_page_world(svc)
        slip = svc.sign_claim("甲")  # signed, not delivered
        svc.save_and_close()

        reopened = ReleaseService.reopen(CustodyStore(path))
        self.assertTrue(reopened.validate_ticket(slip.ticket_id))
        out = reopened.deliver(slip.ticket_id, present=True)
        self.assertTrue(out.delivered)
        reopened.save_and_close()


class OperatorStoryTests(unittest.TestCase):
    """The full operator sequence from the brief, in order."""

    def test_submit_confront_clear_confirm_and_collect(self):
        svc = make_svc()
        svc.add_cell("JIA", owner="甲")
        svc.add_cell("JIA2", owner="甲")
        svc.sign_commitment("甲", "JX", in_person=True)

        # 1) operator submits a two-page job
        svc.submit_job("JX", "甲", pages=2, in_person=True)
        self.assertEqual(svc.missing_pages("甲"), [1, 2])

        # page 1 prints, jams, is cleared and reprinted
        svc.register_sheet("P1-a", "JX", 1)
        svc.report_face("P1-a", Side.FRONT, candidates=["甲"])
        svc.mark_card_jam("P1-a", detail="卡纸")
        svc.clamp_clear_and_reprint("P1-a", "P1-b")
        self.assertEqual(svc.reconstruct("P1-a")["status"], "disposed")

        # 2) confirm faces page by page and land each in a cell
        svc.report_face("P1-b", Side.FRONT, candidates=["甲"])
        svc.report_face("P1-b", Side.BACK, candidates=["甲"])
        svc.report_receipt("P1-b", LocKind.CELL, cell_id="JIA")

        svc.register_sheet("P2", "JX", 2)
        svc.report_face("P2", Side.FRONT, candidates=["甲"])
        svc.report_face("P2", Side.BACK, candidates=["甲"])
        svc.report_receipt("P2", LocKind.CELL, cell_id="JIA2")

        # 3) collect: only now is the set complete and verified
        out = svc.prepare_release("甲", present=True)
        self.assertTrue(out.delivered)
        self.assertEqual(sorted(out.sheets), ["P1-b", "P2"])

        # the disposed jammed sheet is not in the delivered set
        self.assertNotIn("P1-a", out.sheets)

        # 4) after re-open, the ticket answers who got what
        who = svc.who_received(out.ticket_id)
        self.assertEqual(who["recipient"], "甲")
        self.assertEqual(sorted(who["sheets"]), ["P1-b", "P2"])


if __name__ == "__main__":
    unittest.main()
