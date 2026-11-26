"""Incident, trade-off, and replacement-order tests from the brief."""

import tempfile
import unittest

from lottery import DrawError, DrawService, verify_package
from lottery.cabinets import CabinetOffline
from lottery.scenarios import (
    commit_all,
    escrow_all,
    incident_cabinet_offline_submitters_present,
    incident_escrow_absent_depositor,
    make_participants,
    reveal_all,
    witness_shift_choice,
)


def fresh_service():
    return DrawService(package_dir=tempfile.mkdtemp())


class TestWitnessShiftTradeoff(unittest.TestCase):
    """One witness, one shift: escrow XOR opening-day sealing."""

    def test_shift_capabilities_are_exclusive(self):
        escrow = witness_shift_choice("escrow")
        opening = witness_shift_choice("opening")
        self.assertTrue(escrow["can_escrow"])
        self.assertFalse(escrow["can_host_opening_seal"])
        self.assertFalse(opening["can_escrow"])
        self.assertTrue(opening["can_host_opening_seal"])
        # they cannot both be true for a single shift
        self.assertFalse(escrow["can_escrow"] and escrow["can_host_opening_seal"])
        self.assertFalse(opening["can_escrow"] and opening["can_host_opening_seal"])

    def test_mixed_sources_rejected_in_one_round(self):
        # 甲 wants escrow, 乙 wants onsite: a single-shift round cannot serve both.
        svc = fresh_service()
        tickets = [
            {"ticket_id": "T1", "household_id": "H1", "source": "escrow"},
            {"ticket_id": "T2", "household_id": "H2", "source": "onsite"},
        ]
        with self.assertRaises(DrawError):
            svc.create_round("R1", tickets, 1, "m", "escrow")

    def test_escrow_shift_rejects_onsite_reveal(self):
        svc = fresh_service()
        parts = make_participants([("T1", "H1"), ("T2", "H2")], source="escrow")
        svc.create_round("R1", [p.as_ticket() for p in parts], 1, "m", "escrow")
        commit_all(svc, "R1", parts)
        from lottery import UploadError

        with self.assertRaises(UploadError):
            svc.submit_reveal("R1", "T1", parts[0].salt, parts[0].value)


class TestIncidentEscrowAbsent(unittest.TestCase):
    """甲 escrows early then is absent; cabinets hand over -> opens."""

    def test_absent_depositor_does_not_stall(self):
        svc = fresh_service()
        info = incident_escrow_absent_depositor(svc)
        out = info["outcome"]
        self.assertTrue(out.opened)
        self.assertFalse(out.postponed)
        self.assertNotIn(info["absent_depositor"], svc.rounds["R-escrow"].attended)
        # winner set still recomputes for outside readers
        res = verify_package(svc.package("R-escrow"))
        self.assertTrue(res.ok, res.errors)
        self.assertEqual(res.recomputed_winners, out.winners)

    def test_absent_depositor_relies_only_on_cabinet_receipts(self):
        # with one cabinet offline, the same absent-depositor round must postpone
        svc = fresh_service()
        parts = make_participants(
            [("A1", "H1"), ("A2", "H2"), ("A3", "H3")], source="escrow"
        )
        svc.create_round("R", [p.as_ticket() for p in parts], 2, "m", "escrow")
        commit_all(svc, "R", parts)
        escrow_all(svc, "R", parts)
        svc.cabinets.set_online("cabinet-A", False)
        out = svc.open_round("R", witness="early-shift")
        self.assertTrue(out.postponed)
        self.assertEqual(svc.winners("R"), [])
        self.assertIn("cabinet-A", out.offline_cabinets)

    def test_failed_recovery_leaks_no_partial_reveals(self):
        svc = fresh_service()
        parts = make_participants(
            [("A1", "H1"), ("A2", "H2"), ("A3", "H3")], source="escrow"
        )
        svc.create_round("R", [p.as_ticket() for p in parts], 2, "m", "escrow")
        commit_all(svc, "R", parts)
        escrow_all(svc, "R", parts)
        svc.cabinets.set_online("cabinet-B", False)
        out = svc.open_round("R", witness="early-shift")
        self.assertTrue(out.postponed)
        # recovery is atomic: no reveal may have been stored from the failed run
        self.assertEqual(svc.rounds["R"].reveals, {})
        with self.assertRaises(Exception):
            svc.first_reveal("R")


class TestIncidentCabinetOffline(unittest.TestCase):
    """One cabinet offline but all submitters present -> still opens."""

    def test_offline_cabinet_irrelevant_when_all_present(self):
        svc = fresh_service()
        info = incident_cabinet_offline_submitters_present(svc)
        out = info["outcome"]
        self.assertTrue(out.opened)
        self.assertFalse(out.postponed)
        res = verify_package(svc.package("R-onsite"))
        self.assertTrue(res.ok, res.errors)

    def test_offline_cabinet_with_missing_onsite_postpones(self):
        svc = fresh_service()
        parts = make_participants(
            [("B1", "H1"), ("B2", "H2"), ("B3", "H3")], source="onsite"
        )
        svc.create_round("R", [p.as_ticket() for p in parts], 2, "m", "opening")
        commit_all(svc, "R", parts)
        svc.cabinets.set_online("cabinet-A", False)
        svc.submit_reveal("R", "B1", parts[0].salt, parts[0].value)
        out = svc.open_round("R", witness="opening-shift")
        self.assertTrue(out.postponed)
        self.assertEqual(svc.winners("R"), [])


class TestReplacementOrder(unittest.TestCase):
    """Replacement follows the original, independently recomputable order."""

    def _open_four(self, svc):
        parts = make_participants(
            [("T1", "H1"), ("T2", "H2"), ("T3", "H3"), ("T4", "H4")], source="escrow"
        )
        svc.create_round("R1", [p.as_ticket() for p in parts], 2, "m", "escrow")
        commit_all(svc, "R1", parts)
        escrow_all(svc, "R1", parts)
        out = svc.open_round("R1", witness="r")
        return parts, out

    def test_replacement_comes_from_sealed_order(self):
        svc = fresh_service()
        parts, out = self._open_four(svc)
        draw_order = svc.rounds["R1"].draw_order
        absent = [out.winners[0]]
        attended = {p.ticket_id for p in parts}
        repl = svc.replace("R1", absent, attended=attended)
        self.assertEqual(len(repl), 1)
        # the promoted ticket must be the next eligible in draw order
        idx_of_absent = draw_order.index(absent[0])
        candidate = None
        seated = {svc.rounds["R1"].household_of(w) for w in out.winners if w != absent[0]}
        for tid in draw_order:
            if tid == absent[0] or tid in out.winners:
                continue
            if svc.rounds["R1"].household_of(tid) in seated:
                continue
            candidate = tid
            break
        self.assertEqual(repl[0], candidate)
        res = verify_package(svc.package("R1"))
        self.assertTrue(res.ok, res.errors)

    def test_replacement_requires_attendance(self):
        svc = fresh_service()
        parts, out = self._open_four(svc)
        absent = [out.winners[0]]
        # nobody attended the makeup -> cannot fill
        with self.assertRaises(DrawError):
            svc.replace("R1", absent, attended=set())

    def test_replacement_respects_household_already_claimed(self):
        svc = fresh_service()
        parts = make_participants(
            [("T1", "H1"), ("T2", "H2"), ("T3", "H3"), ("T4", "H4")], source="escrow"
        )
        svc.create_round("R1", [p.as_ticket() for p in parts], 2, "m", "escrow")
        commit_all(svc, "R1", parts)
        escrow_all(svc, "R1", parts)
        out = svc.open_round("R1", witness="r")
        # a household that already claimed cannot be reseated via replacement
        claimed = out.winners[0]
        svc.claim("R1", claimed)
        absent = [out.winners[1]]
        attended = {p.ticket_id for p in parts}
        repl = svc.replace("R1", absent, attended=attended)
        self.assertNotEqual(svc.rounds["R1"].household_of(repl[0]), svc.rounds["R1"].household_of(claimed))

    def test_cannot_replace_a_claimed_winner(self):
        svc = fresh_service()
        parts, out = self._open_four(svc)
        svc.claim("R1", out.winners[0])
        with self.assertRaises(DrawError):
            svc.replace("R1", [out.winners[0]], attended={p.ticket_id for p in parts})

    def test_tampered_replacement_history_fails_verification(self):
        svc = fresh_service()
        parts, out = self._open_four(svc)
        absent = [out.winners[0]]
        attended = {p.ticket_id for p in parts}
        svc.replace("R1", absent, attended=attended)
        pkg = svc.package("R1")
        # forge a replacement step that picks someone out of order
        import copy

        from lottery.crypto import digest

        bad = copy.deepcopy(pkg)
        bad["replacements"][0]["replacements"] = [parts[-1].ticket_id]
        bad["winners"] = [w for w in pkg["winners"] if w not in pkg["replacements"][0]["replacements"]]
        bad["winners"].append(parts[-1].ticket_id)
        bad["packet_hash"] = digest({k: v for k, v in bad.items() if k != "packet_hash"})
        res = verify_package(bad)
        self.assertFalse(res.ok)
        self.assertFalse(res.checks["replacements_replay"])


if __name__ == "__main__":
    unittest.main()
