"""End-to-end and unit tests for the offline festival wallet.

Run with:  python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from festival import audit, crypto
from festival.assertion import LocalProofService, verify_assertion
from festival.calendar import Program, Screening, age_on, days, default_program
from festival.credential import ExerciseCredential, LocalCredentialIssuer
from festival.door import DoorTerminal, TicketView
from festival.paths import PATH_A, PATH_B, for_mode, plan_program
from festival.preissue import PreIssueAuthority, verify_token
from festival.story import run
from festival.tickets import (
    DeskRejection,
    IssuedTicket,
    MixedHolderError,
    TicketDesk,
)
from festival.wallet import EligibilityUnavailable, NotEligible, Wallet

MODES = ("assertion", "preissue")


# --------------------------------------------------------------------------- #
# Crypto
# --------------------------------------------------------------------------- #
class CryptoTest(unittest.TestCase):
    def test_sign_verify_roundtrip(self):
        sk = crypto.PrivateKey.generate()
        payload = {"a": 1, "b": [1, 2, 3]}
        sig = sk.sign("dom", payload)
        self.assertTrue(crypto.verify(sk.public, "dom", payload, sig))

    def test_tamper_and_wrong_key_rejected(self):
        sk = crypto.PrivateKey.generate()
        sig = sk.sign("dom", {"a": 1})
        self.assertFalse(crypto.verify(sk.public, "dom", {"a": 2}, sig))
        self.assertFalse(crypto.verify(crypto.PrivateKey.generate().public, "dom", {"a": 1}, sig))
        self.assertFalse(crypto.verify(sk.public, "other-dom", {"a": 1}, sig))

    def test_group_sanity(self):
        self.assertEqual(crypto.P.bit_length(), 2048)
        self.assertEqual(pow(crypto.G, crypto.Q, crypto.P), 1)
        self.assertNotEqual(crypto.H_COMMIT, crypto.G)


# --------------------------------------------------------------------------- #
# Calendar / age math
# --------------------------------------------------------------------------- #
class CalendarTest(unittest.TestCase):
    def test_age_on_boundary(self):
        self.assertEqual(age_on(date(2008, 11, 29), date(2026, 11, 29)), 18)
        self.assertEqual(age_on(date(2008, 11, 29), date(2026, 11, 28)), 17)

    def test_default_program_has_gap_day(self):
        p = default_program()
        self.assertEqual((p.screening(1).date - p.screening(0).date).days, 2)


# --------------------------------------------------------------------------- #
# Story: both paths
# --------------------------------------------------------------------------- #
class StoryTestBase:
    mode: str = "assertion"

    @classmethod
    def setUpClass(cls):
        cls.result = run(cls.mode)
        cls.s = cls.result.steps

    # -- 1. programme registered ---------------------------------------- #
    def test_programme_registered_with_two_tiers(self):
        tiers = [x["tier"] for x in self.s["program"]["screenings"]]
        self.assertEqual(len(tiers), 2)
        self.assertEqual(sorted(tiers), [12, 18])
        self.assertGreaterEqual(self.s["program"]["gap_days_between"], 2)

    def test_path_choice_recorded(self):
        self.assertEqual(self.s["path_choice"]["name"], for_mode(self.mode).name)

    # -- 2/3. bundle for one holder ------------------------------------- #
    def test_full_bundle_bought_for_one_holder(self):
        b = self.s["alice_bundle"]
        self.assertEqual(b["stub_indices"], [0, 1])
        self.assertTrue(b["single_holder"])

    # -- 3. privacy of the presented material --------------------------- #
    def test_presentation_carries_no_dob_or_global_id(self):
        self.assertTrue(self.s["presentation_has_no_dob"])
        self.assertTrue(self.s["presentation_has_no_global_id"])

    # -- 4. door admission, replay, exit -------------------------------- #
    def test_entry_admitted(self):
        self.assertTrue(self.s["entry"]["accepted"])
        self.assertEqual(self.s["entry"]["seat"], 1)

    def test_same_stub_again_takes_no_extra_seat(self):
        r = self.s["replay"]
        self.assertFalse(r["accepted"])
        self.assertEqual(r["seats_used"], 1)
        self.assertIn("already admitted", r["reason"])

    def test_reentry_after_exit_takes_no_new_seat(self):
        r = self.s["reentry_same_stub"]
        self.assertFalse(r["accepted"])
        self.assertEqual(r["seats_used"], 1)
        self.assertEqual(r["inside"], 0)

    def test_exit_receipt_and_entry_receipt_valid(self):
        self.assertTrue(self.s["exit"]["accepted"])
        self.assertTrue(self.s["exit"]["receipt_valid"])
        self.assertTrue(self.s["entry_receipt_valid"])

    def test_door_sees_only_its_own_screening(self):
        self.assertTrue(self.s["door_sees_only_early"])

    # -- 5. birthday between the shows ---------------------------------- #
    def test_carol_birthday_falls_between_shows(self):
        c = self.s["carol"]
        self.assertTrue(c["birthday_is_gap_day"])
        self.assertFalse(c["early_eligible"])   # 17 at the 18+ opening
        self.assertTrue(c["late_eligible"])     # 18 at the 12+ closing

    def test_stitching_two_viewers_is_refused(self):
        self.assertTrue(self.s["stitch_rejected"])
        self.assertIn("different holders", self.s["stitch_reason"])

    def test_carols_own_bundle_refused_for_early_show(self):
        self.assertFalse(self.s["carol_own_bundle_sold"])
        self.assertIn("below tier", self.s["carol_early_refusal"])

    def test_refused_attempts_created_no_seats(self):
        books = self.s["desk_books_after_carol"]
        self.assertTrue(books["ok"])
        # Alice 2 + Carol late-only 1 = 3 seats; early kept just Alice's.
        self.assertEqual(books["seats"], 3)
        self.assertEqual(books["by_index"], {"0": 1, "1": 2})

    # -- 6. birthday on the late-show day, screening moved --------------- #
    def test_dave_turns_tier_age_on_late_day(self):
        d = self.s["dave"]
        self.assertEqual(d["age_on_late_day"], 12)
        self.assertEqual(d["age_one_day_earlier"], 11)
        self.assertTrue(d["late_eligible_on_day"])

    def test_moved_date_rejects_old_eligibility(self):
        m = self.s["dave_moved_date"]
        self.assertFalse(m["accepted"])
        self.assertEqual(m["seats_used"], 0)
        self.assertIn("does not match", m["reason"])

    def test_prior_receipts_stay_queryable(self):
        q = self.s["prior_admissions_still_queryable"]
        self.assertGreaterEqual(q["count"], 1)
        self.assertTrue(q["entry_receipt_still_valid"])

    # -- 7. second programme, normal entrance, audit -------------------- #
    def test_second_programme_normal_entrance(self):
        self.assertTrue(self.s["shop2_normal_entrance"]["accepted"])

    def test_no_retained_material_contains_dob_or_global_id(self):
        for key in ("desk1_privacy", "desk2_privacy", "door_privacy"):
            p = self.s[key]
            self.assertTrue(p["searches_clean"], key)
            self.assertFalse(p["contains_holder_dob"], key)
            self.assertFalse(p["contains_exercise_id"], key)

    def test_door_retained_only_its_own_date(self):
        dates = self.s["door_privacy"]["dates_present"]
        self.assertEqual(len(dates), 1)

    def test_cross_shop_records_not_correlatable(self):
        self.assertFalse(self.s["cross_shop"]["correlatable"])
        self.assertEqual(self.s["cross_shop"]["shared"], [])

    def test_handles_stable_within_and_differ_across_programmes(self):
        a = self.result.assertions
        self.assertTrue(a["handle_stable_within_program"])
        self.assertTrue(a["handle_differs_across_programs"])
        self.assertNotEqual(a["erin_handle_p1"], a["erin_handle_p2"])


class AssertionPathStoryTest(StoryTestBase, unittest.TestCase):
    mode = "assertion"


class PreissuePathStoryTest(StoryTestBase, unittest.TestCase):
    mode = "preissue"


# --------------------------------------------------------------------------- #
# Path-specific behaviour
# --------------------------------------------------------------------------- #
class PathChoiceTest(unittest.TestCase):
    def test_choices_describe_known_date_property(self):
        self.assertFalse(PATH_A.late_date_ok)
        self.assertTrue(PATH_B.late_date_ok)
        self.assertEqual(for_mode("preissue"), PATH_A)
        self.assertEqual(for_mode("assertion"), PATH_B)

    def _setup(self, mode):
        issuer = LocalCredentialIssuer()
        program = default_program()
        service = LocalProofService()
        authority = PreIssueAuthority("auth", program.program_id,
                                      program.dates, sorted(set(program.tiers)))
        return issuer, program, service, authority

    def test_plan_reports_late_date_feasibility_under_both_paths(self):
        # A programme with a brand-new date that the pre-issue grid never saw.
        issuer, program, service, authority = self._setup("preissue")
        w = Wallet(issuer.mint(date(1990, 1, 1)), "preissue")
        # Pre-issue grid was built from the ORIGINAL dates only.
        extra = Program("late-added", [
            Screening("Added show", days(40), 12),
        ])
        plan = plan_program(extra, w, "preissue", authority=authority)
        self.assertFalse(plan[0]["feasible"])  # path A: no token for the new date
        plan_b = plan_program(extra, w, "assertion", service=service)
        self.assertTrue(plan_b[0]["feasible"])  # path B: service answers live

    def test_preissue_missing_date_raises(self):
        issuer, program, service, authority = self._setup("preissue")
        w = Wallet(issuer.mint(date(1990, 1, 1)), "preissue")
        new_day = days(50)
        with self.assertRaises(EligibilityUnavailable):
            w.obtain_preissued(authority, program.program_id, new_day, 12)

    def test_preissue_cannot_issue_offline_for_new_date(self):
        """Path A's weakness: a new date is unsellable even if the holder qualifies."""
        issuer, program, service, authority = self._setup("preissue")
        dob = date(1990, 1, 1)
        w = Wallet(issuer.mint(dob), "preissue")
        self.assertTrue(w.cred.meets(12, days(60)))
        with self.assertRaises(EligibilityUnavailable):
            w.obtain_preissued(authority, program.program_id, days(60), 12)

    def test_assertion_service_offline_falls_back_to_held_proof(self):
        issuer, program, service, authority = self._setup("assertion")
        w = Wallet(issuer.mint(date(1990, 1, 1)), "assertion")
        s = program.screening(1)
        w.prefetch_assertion(service, program.program_id, s.date, s.age_tier)
        service.go_offline()
        # We already hold the assertion for exactly this screening -> still admittable.
        proof = w.eligibility_for(program, 1, service=service)
        self.assertEqual(proof["kind"], "assertion")

    def test_assertion_service_offline_blocks_new_date(self):
        issuer, program, service, authority = self._setup("assertion")
        w = Wallet(issuer.mint(date(1990, 1, 1)), "assertion")
        service.go_offline()
        with self.assertRaises(EligibilityUnavailable):
            w.obtain_assertion(service, program.program_id, days(60), 12)


# --------------------------------------------------------------------------- #
# Non-transferability and tampering
# --------------------------------------------------------------------------- #
class NonTransferabilityTest(unittest.TestCase):
    def test_another_wallet_cannot_use_bought_stub(self):
        from festival.wallet import Wallet as W

        issuer = LocalCredentialIssuer()
        program = default_program()
        service = LocalProofService()
        desk = TicketDesk("d", program, {"preissue": crypto.PrivateKey.generate().public,
                                         "assertion": service.public})
        alice = W(issuer.mint(date(1990, 1, 1)), "assertion")
        mallory = W(issuer.mint(date(1990, 1, 1)), "assertion")
        for w in (alice, mallory):
            for i in range(2):
                s = program.screening(i)
                w.prefetch_assertion(service, program.program_id, s.date, s.age_tier)
        proofs = {i: alice.eligibility_for(program, i, service=service) for i in range(2)}
        sale = desk.sell(proofs)
        alice_ticket = IssuedTicket(sale, alice.binding_key(program.program_id), proofs)

        door = DoorTerminal("u", TicketView(program.program_id, program.fingerprint(), 0,
                                            program.screening(0).date, program.screening(0).age_tier),
                            desk.public, trusted={"assertion": service.public})
        # Mallory steals the stub and secret but can't sign with Alice's key.
        stolen = alice_ticket.present(0, "n1")
        stolen["binding_sig"] = mallory.binding_key(program.program_id).sign(
            "festival/stub-binding/v1", {"nonce": "n1", "ticket_id": sale.ticket_id, "index": 0})
        res = door.admit(stolen, "n1")
        self.assertFalse(res["accepted"])
        self.assertIn("binding", res["reason"])

    def test_tampered_stub_signature_rejected(self):
        issuer = LocalCredentialIssuer()
        program = default_program()
        service = LocalProofService()
        desk = TicketDesk("d", program, {"assertion": service.public})
        w = Wallet(issuer.mint(date(1990, 1, 1)), "assertion")
        for i in range(2):
            s = program.screening(i)
            w.prefetch_assertion(service, program.program_id, s.date, s.age_tier)
        proofs = {i: w.eligibility_for(program, i, service=service) for i in range(2)}
        sale = desk.sell(proofs)
        ticket = IssuedTicket(sale, w.binding_key(program.program_id), proofs)
        pres = ticket.present(0, "n")
        pres["stub"]["tier"] = 99  # tamper
        door = DoorTerminal("u", TicketView(program.program_id, program.fingerprint(), 0,
                                           program.screening(0).date, program.screening(0).age_tier),
                            desk.public, trusted={"assertion": service.public})
        self.assertFalse(door.admit(pres, "n")["accepted"])

    def test_desk_rejects_token_for_wrong_binding(self):
        """A pre-issued token minted for one wallet must not sell to another."""
        issuer = LocalCredentialIssuer()
        program = default_program()
        authority = PreIssueAuthority("a", program.program_id, program.dates,
                                      sorted(set(program.tiers)))
        desk = TicketDesk("d", program, {"preissue": authority.public})
        alice = Wallet(issuer.mint(date(1990, 1, 1)), "preissue")
        mallory = Wallet(issuer.mint(date(1990, 1, 1)), "preissue")
        s0 = program.screening(0)
        alice.obtain_preissued(authority, program.program_id, s0.date, s0.age_tier)
        good = alice.eligibility_for(program, 0)
        # Mallory swaps in her own binding key but keeps Alice's token.
        forged = dict(good)
        forged["binding_pub"] = mallory.binding_key(program.program_id).public
        forged["nullifier"] = mallory.nullifier(program.program_id)
        with self.assertRaises(DeskRejection):
            desk.sell({0: forged})


# --------------------------------------------------------------------------- #
# Desk reconciliation and signatures
# --------------------------------------------------------------------------- #
class DeskTest(unittest.TestCase):
    def _desk(self):
        program = default_program()
        service = LocalProofService()
        desk = TicketDesk("d", program, {"assertion": service.public})
        return program, service, desk

    def test_reconcile_signed_and_balanced(self):
        program, service, desk = self._desk()
        issuer = LocalCredentialIssuer()
        w = Wallet(issuer.mint(date(1990, 1, 1)), "assertion")
        for i in range(2):
            s = program.screening(i)
            w.prefetch_assertion(service, program.program_id, s.date, s.age_tier)
        desk.sell({i: w.eligibility_for(program, i, service=service) for i in range(2)})
        rec = desk.reconcile()
        self.assertTrue(rec["ok"])
        self.assertEqual(rec["seats"], 2)
        body = {k: v for k, v in rec.items() if k != "sig"}
        self.assertTrue(crypto.verify(desk.public, crypto.DOMAIN["desk_reconcile"], body, rec["sig"]))

    def test_empty_sale_rejected(self):
        _, _, desk = self._desk()
        with self.assertRaises(DeskRejection):
            desk.sell({})


# --------------------------------------------------------------------------- #
# Audit helpers
# --------------------------------------------------------------------------- #
class AuditTest(unittest.TestCase):
    def test_correlation_detects_shared_handle(self):
        a = audit.RetainedMaterial("a").add(
            {"holder": {"nullifier": "X", "binding_pub": "K"}})
        b = audit.RetainedMaterial("b").add(
            {"holder": {"nullifier": "X", "binding_pub": "K"}})
        self.assertTrue(audit.try_cross_desk_correlation(a, b)["correlatable"])

    def test_audit_flags_dob_leak(self):
        m = audit.RetainedMaterial("m").add({"dob": "1990-05-01"})
        result = audit.audit_privacy(m, "1990-05-01")
        self.assertTrue(result["contains_holder_dob"])
        self.assertFalse(result["searches_clean"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
