"""Referee: pre-race starts, receipt re-derivation, fork detection,
midpoint hand-off, and the "no invented start" guarantee."""
import unittest

from puzzle import fixtures as F
from puzzle.referee import (
    RaceStart,
    VerificationError,
    attest_midpoint,
    certify,
    continue_from,
    detect_fork,
    issue_start,
    verify_continuation,
    verify_midpoint,
    verify_receipt,
)
from puzzle.signing import Key


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ref = Key.from_label("referee")
        cls.base = F.build_map()
        cls.race = issue_start(cls.ref, "race-1", cls.base)

    def good_receipt(self):
        return certify(self.ref, self.race, F.RACE_ACTIONS, F.RACE_DWELLS,
                       self_reported_score=999)


class TestStart(Base):
    def test_start_roundtrip_and_verify(self):
        body = self.race.verify(self.ref)
        self.assertEqual(body["kind"], "race-start")
        self.assertEqual(body["race_id"], "race-1")
        self.assertEqual(Map_commit(self.race), self.base.puzzle_commit)

    def test_wrong_key_rejected(self):
        other = Key.from_label("nope")
        with self.assertRaises(VerificationError):
            self.race.verify(other)


class TestReceipt(Base):
    def test_verified_not_self_reported(self):
        r = self.good_receipt()
        body = verify_receipt(r, self.ref, self.race)
        self.assertEqual(body["verified_score"], 166)
        self.assertEqual(body["self_reported_score"], 999)
        self.assertTrue(body["reachable"])

    def test_receipt_replay_fields(self):
        r = self.good_receipt()
        body = verify_receipt(r, self.ref, self.race)
        self.assertEqual(body["pushes"], r.body["pushes"])
        self.assertEqual(len(body["visited"]),
                         len(set(map(tuple, body["visited"]))))

    def test_receipt_for_wrong_start_rejected(self):
        r = self.good_receipt()
        other_race = issue_start(self.ref, "race-1", F.altered_map())
        with self.assertRaises(VerificationError):
            verify_receipt(r, self.ref, other_race)

    def test_tampered_receipt_rejected(self):
        r = self.good_receipt()
        r.envelope["body"]["verified_score"] = 10000
        with self.assertRaises(VerificationError):
            verify_receipt(r, self.ref, self.race)

    def test_signature_from_wrong_key_rejected(self):
        from puzzle.referee import Receipt

        r = self.good_receipt()
        imposter = Key.from_label("imposter")
        bad = Receipt(imposter.sign_body(dict(r.body)))
        with self.assertRaises(VerificationError):
            verify_receipt(bad, self.ref, self.race)


class TestFork(unittest.TestCase):
    def test_prefix_is_not_fork(self):
        report = detect_fork(F.RACE_ACTIONS, F.RACE_ACTIONS[:5])
        self.assertFalse(report.forked)

    def test_equal_is_not_fork(self):
        report = detect_fork(F.RACE_ACTIONS, list(F.RACE_ACTIONS))
        self.assertFalse(report.forked)

    def test_divergence_is_fork_at_step(self):
        claim = list(F.RACE_ACTIONS)
        claim[3] = "U" if claim[3] != "U" else "R"
        report = detect_fork(F.RACE_ACTIONS, claim)
        self.assertTrue(report.forked)
        self.assertEqual(report.first_divergent_step, 4)

    def test_extension_is_fork(self):
        report = detect_fork(F.RACE_ACTIONS, list(F.RACE_ACTIONS) + ["R"])
        self.assertTrue(report.forked)


class TestMidpoint(Base):
    def test_attest_and_continue_matches_full(self):
        mid = attest_midpoint(self.ref, self.race,
                              F.RACE_ACTIONS[:10], F.RACE_DWELLS[:11])
        verify_midpoint(mid, self.ref, self.race)
        cont = continue_from(self.ref, self.race, mid,
                             F.RACE_ACTIONS[10:], F.RACE_DWELLS[10:])
        verify_continuation(cont, self.ref, self.race, mid)
        full = verify_receipt(self.good_receipt(), self.ref, self.race)
        self.assertEqual(cont.body["final_state"]["player"],
                         full["final_state"]["player"])
        self.assertEqual(cont.body["resume_from_step"], 10)
        self.assertEqual(mid.body["covered_range"], [0, 10])

    def test_midpoint_state_re_derives(self):
        mid = attest_midpoint(self.ref, self.race,
                              F.RACE_ACTIONS[:7], F.RACE_DWELLS[:8])
        body = verify_midpoint(mid, self.ref, self.race)
        self.assertEqual(body["state_at_midpoint"]["step"], 7)
        self.assertEqual(body["state_at_midpoint"]["player"], [6, 2])

    def test_forged_midpoint_state_rejected(self):
        mid = attest_midpoint(self.ref, self.race,
                              F.RACE_ACTIONS[:10], F.RACE_DWELLS[:11])
        mid.envelope["body"]["state_at_midpoint"]["player"] = [99, 99]
        with self.assertRaises(VerificationError):
            verify_midpoint(mid, self.ref, self.race)

    def test_cannot_attest_unreachable_segment(self):
        with self.assertRaises(VerificationError):
            attest_midpoint(self.ref, self.race, ["U", "D"], [0, 0, 0])

    def test_continuation_cannot_point_at_other_midpoint(self):
        mid = attest_midpoint(self.ref, self.race,
                              F.RACE_ACTIONS[:10], F.RACE_DWELLS[:11])
        mid2 = attest_midpoint(self.ref, self.race,
                               F.RACE_ACTIONS[:5], F.RACE_DWELLS[:6])
        cont = continue_from(self.ref, self.race, mid,
                             F.RACE_ACTIONS[10:], F.RACE_DWELLS[10:])
        with self.assertRaises(VerificationError):
            verify_continuation(cont, self.ref, self.race, mid2)

    def test_continuation_resume_step_must_match_midpoint(self):
        mid = attest_midpoint(self.ref, self.race,
                              F.RACE_ACTIONS[:10], F.RACE_DWELLS[:11])
        cont = continue_from(self.ref, self.race, mid,
                             F.RACE_ACTIONS[10:], F.RACE_DWELLS[10:])
        cont.envelope["body"]["resume_from_step"] = 99
        with self.assertRaises(VerificationError):
            verify_continuation(cont, self.ref, self.race, mid)

    def test_midpoint_cannot_be_built_without_signed_start(self):
        # A fabricated "midpoint" with an arbitrary body fails signature check.
        bad = type(attest_midpoint(self.ref, self.race, [], [0]))(
            {"v": 1, "body": {"kind": "midpoint", "race_id": "race-1",
                              "start_commit": "00", "puzzle_commit": "00",
                              "witnessed_actions": [], "witnessed_dwells": [0],
                              "covered_range": [0, 0],
                              "state_at_midpoint": {"player": [1, 1],
                                                    "boxes": [], "closed_doors": [],
                                                    "step": 0},
                              "pushes": [], "visited": [], "trace": [],
                              "signer": self.ref.pub_hex},
             "sig": "00" * 64})
        with self.assertRaises(VerificationError):
            verify_midpoint(bad, self.ref, self.race)


def Map_commit(start: RaceStart) -> str:
    return start.puzzle_commit


if __name__ == "__main__":
    unittest.main()
