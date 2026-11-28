"""Full-brief acceptance test: the whole story, start to downloaded bytes.

Each test method pins one clause of the brief; ``test_full_narrative`` walks
the entire sequence in order on two local directories.
"""
import os
import tempfile
import unittest

from puzzle import fixtures as F
from puzzle.demo import build_demo, validate_demo_bytes
from puzzle.referee import (
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
from puzzle.store import TwoDirStore


class NarrativeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ref = Key.from_label("referee")
        cls.pub = Key.from_label("publisher")
        cls.base = F.build_map()
        cls.race = issue_start(cls.ref, "race-1", cls.base, map_id="MAP-7")
        cls.receipt = certify(cls.ref, cls.race,
                              F.RACE_ACTIONS, F.RACE_DWELLS,
                              self_reported_score=999)

    def test_verified_score_not_self_report(self):
        body = verify_receipt(self.receipt, self.ref, self.race)
        self.assertEqual(body["verified_score"], 166)
        self.assertEqual(body["self_reported_score"], 999)

    def test_reachable_and_pushes_reported(self):
        body = self.receipt.body
        self.assertTrue(body["reachable"])
        self.assertEqual(
            {tuple(p["box"]) for p in body["pushes"]},
            {(2, 2), (7, 2), (12, 3)},
        )
        self.assertEqual(body["stopped_after_step"], len(F.RACE_ACTIONS))

    def test_two_players_one_and_two_rooms(self):
        with tempfile.TemporaryDirectory() as root:
            store = TwoDirStore(os.path.join(root, "ref"),
                                os.path.join(root, "demo"))
            store.save_start(self.race)
            store.save_receipt(self.receipt)
            for pid, rooms in F.PLAYER_UNLOCKS.items():
                d = build_demo(self.pub, self.receipt, self.ref,
                               self.race, pid, rooms)
                store.publish_demo(d)
                raw = store.download_demo("race-1", pid)
                pb = validate_demo_bytes(raw, self.pub)
                self.assertTrue(pb.ok)
                # player1 (A) stops before B; player2 (A,B) stops before C
                expected_cut = (F.ROOM_ENTRY_STEPS["B"] if pid == "player1"
                                else F.ROOM_ENTRY_STEPS["C"])
                self.assertEqual(d.body["cut_before_step"], expected_cut)
                self.assertEqual(pb.final_cell, tuple(d.body["final_cell"]))
                self.assertEqual(set(d.body["map"]["regions"]), set(rooms))

    def test_alter_map_and_reuse_actions(self):
        altered = F.altered_map()
        race2 = issue_start(self.ref, "race-1-alt", altered)
        r2 = certify(self.ref, race2, F.RACE_ACTIONS, F.RACE_DWELLS)
        self.assertNotEqual(altered.puzzle_commit, self.base.puzzle_commit)
        self.assertFalse(r2.body["reachable"])
        self.assertEqual(r2.body["stopped_after_step"], 7)
        self.assertEqual(r2.body["final_state"]["player"], [6, 2])
        # The old demo (base puzzle) cannot be passed off for the altered map.
        old = build_demo(self.pub, self.receipt, self.ref, self.race,
                         "player2", ["A", "B"])
        self.assertNotEqual(old.body["puzzle_commit_full"],
                            r2.body["puzzle_commit"])
        with self.assertRaises(VerificationError):
            verify_receipt(r2, self.ref, self.race)  # wrong race binding

    def test_fork_identified(self):
        claim = list(F.RACE_ACTIONS)
        claim[2] = "U"
        fork = detect_fork(self.receipt.body["actions"], claim)
        self.assertTrue(fork.forked)
        self.assertEqual(fork.first_divergent_step, 3)

    def test_revoke_room_keeps_score_moves_cut(self):
        before = build_demo(self.pub, self.receipt, self.ref, self.race,
                            "player2", ["A", "B"])
        after = build_demo(self.pub, self.receipt, self.ref, self.race,
                           "player2", ["A"])
        self.assertEqual(before.body["verified_score"],
                         after.body["verified_score"])
        self.assertEqual(after.body["cut_before_step"], F.ROOM_ENTRY_STEPS["B"])
        pb = validate_demo_bytes(after.to_bytes(), self.pub)
        self.assertLessEqual(pb.final_cell[0], 5)  # did not enter room B

    def test_midpoint_handoff_no_invented_start(self):
        mid = attest_midpoint(self.ref, self.race,
                              F.RACE_ACTIONS[:10], F.RACE_DWELLS[:11])
        verify_midpoint(mid, self.ref, self.race)
        cont = continue_from(self.ref, self.race, mid,
                             F.RACE_ACTIONS[10:], F.RACE_DWELLS[10:])
        verify_continuation(cont, self.ref, self.race, mid)
        self.assertEqual(cont.body["resume_from_step"], 10)
        self.assertEqual(mid.body["covered_range"], [0, 10])
        # Combined result matches the full certified replay.
        self.assertEqual(cont.body["final_state"]["player"],
                         self.receipt.body["final_state"]["player"])
        # A self-asserted bogus midpoint is rejected.
        mid.envelope["body"]["state_at_midpoint"]["player"] = [99, 99]
        with self.assertRaises(VerificationError):
            verify_midpoint(mid, self.ref, self.race)

    def test_acceptance_new_race_download_play_bytes_and_last_cell(self):
        with tempfile.TemporaryDirectory() as root:
            store = TwoDirStore(os.path.join(root, "ref"),
                                os.path.join(root, "demo"))
            fresh = issue_start(self.ref, "race-2", self.base, map_id="MAP-7")
            r = certify(self.ref, fresh, F.RACE_ACTIONS, F.RACE_DWELLS)
            d = build_demo(self.pub, r, self.ref, fresh, "player2", ["A", "B"])
            store.publish_demo(d)
            raw = store.download_demo("race-2", "player2")
            self.assertEqual(raw, d.to_bytes())      # exact handed-over bytes
            pb = validate_demo_bytes(raw, self.pub)   # step-by-step replay
            self.assertTrue(pb.ok)
            self.assertEqual(pb.final_cell, tuple(d.body["final_cell"]))


if __name__ == "__main__":
    unittest.main()
