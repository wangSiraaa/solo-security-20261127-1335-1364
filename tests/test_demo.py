"""Demo publication: per-player redaction, stop-before-locked-room,
byte-exact playback, and revocation invariance."""
import json
import unittest

from puzzle import fixtures as F
from puzzle.demo import build_demo, validate_demo_bytes
from puzzle.referee import certify, issue_start
from puzzle.signing import Key
from puzzle.store import TwoDirStore
import tempfile
import os


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ref = Key.from_label("referee")
        cls.pub = Key.from_label("publisher")
        cls.base = F.build_map()
        cls.race = issue_start(cls.ref, "race-1", cls.base)
        cls.receipt = certify(cls.ref, cls.race, F.RACE_ACTIONS, F.RACE_DWELLS)

    def demo(self, player, rooms, receipt=None, race=None):
        return build_demo(self.pub, receipt or self.receipt, self.ref,
                          race or self.race, player, rooms)


class TestPerPlayerCut(Base):
    def test_player1_stops_before_room_b(self):
        d = self.demo("player1", ["A"])
        body = d.body
        self.assertEqual(body["stop_reason"], "locked_room")
        self.assertEqual(body["cut_before_step"], F.ROOM_ENTRY_STEPS["B"])
        # last frame is still room A (on the A|B door cell), never in B
        for frame in body["frames"]:
            self.assertEqual(self.base.region_of(tuple(frame["cell"])) is not None,
                             True)
            room = self.base.region_of(tuple(frame["cell"]))
            self.assertIn(room, {"A"})
        self.assertNotEqual(tuple(body["final_cell"]), (6, 3))

    def test_player2_stops_before_room_c(self):
        d = self.demo("player2", ["A", "B"])
        body = d.body
        self.assertEqual(body["stop_reason"], "locked_room")
        self.assertEqual(body["cut_before_step"], F.ROOM_ENTRY_STEPS["C"])
        for frame in body["frames"]:
            room = self.base.region_of(tuple(frame["cell"]))
            self.assertIn(room, {"A", "B"})

    def test_full_access_player_gets_whole_run(self):
        d = self.demo("vip", ["A", "B", "C"])
        body = d.body
        self.assertEqual(body["stop_reason"], "complete")
        self.assertIsNone(body["cut_before_step"])
        self.assertEqual(len(body["frames"]), len(F.RACE_ACTIONS) + 1)
        self.assertEqual(body["final_cell"], [13, 3])

    def test_empty_access_gets_no_frames(self):
        d = self.demo("nobody", [])
        self.assertEqual(d.body["frames"], [])
        self.assertEqual(d.body["final_cell"], None)


class TestNoLeak(Base):
    def test_map_contains_only_unlocked_regions(self):
        d = self.demo("player1", ["A"])
        self.assertEqual(set(d.body["map"]["regions"]), {"A"})
        # no coordinates of locked cells appear anywhere in the map block
        raw = json.dumps(d.body["map"])
        for room in ("B", "C"):
            self.assertNotIn(f'"{room}"', raw)

    def test_boxes_goals_doors_of_locked_rooms_absent(self):
        d = self.demo("player2", ["A", "B"])
        m = d.body["map"]
        # Room-A box (2,2) and Room-B box (7,2) present; C's box (12,3) absent.
        for b in m["boxes"]:
            self.assertIn(tuple(b), ((2, 2), (7, 2)))
        for g in m["goals"]:
            self.assertIn(tuple(g), ((2, 4), (9, 2)))
        # Both doors sit on the *unlocked* near-side room cells (A|B belongs
        # to A, B|C belongs to B), so both are legitimately present; C's
        # interior box/goal never is.
        self.assertIn([5, 3], m["doors"])
        self.assertIn([10, 3], m["doors"])
        self.assertNotIn([14, 3], m["goals"])  # C goal never present
        self.assertNotIn([12, 3], m["boxes"])  # C box never present

    def test_dwell_and_actions_never_reach_locked_room(self):
        d = self.demo("player1", ["A"])
        # actions count == applied actions that stayed in A
        self.assertEqual(len(d.body["actions"]), 5)
        self.assertEqual(len(d.body["dwells"]), 6)
        self.assertLess(d.body["cut_before_step"], F.ROOM_ENTRY_STEPS["B"] + 1)

    def test_no_locked_cell_or_action_leaks_anywhere_in_file(self):
        d = self.demo("player1", ["A"])
        body = d.body
        # Every published frame's step is before the room-B entry.
        for fr in body["frames"]:
            self.assertLess(fr["step"], F.ROOM_ENTRY_STEPS["B"])
        # The number of dwells equals frames (no tail dwells from room B/C).
        self.assertEqual(len(body["dwells"]), len(body["frames"]))
        # The full locked-room cells never appear in the published trace bytes.
        raw = json.dumps(body)
        for locked_cell in ("[12, 3]", "[14, 3]", "[11, 3]", "[13, 3]"):
            self.assertNotIn(locked_cell, raw)

    def test_demo_binds_map_id_and_full_puzzle(self):
        d = self.demo("player1", ["A"])
        self.assertEqual(d.body["map_id"], self.race.map_id)
        self.assertEqual(d.body["puzzle_commit_full"], self.race.puzzle_commit)


class TestPlayback(Base):
    def test_playback_on_own_map_hits_final_cell(self):
        for pid, rooms in F.PLAYER_UNLOCKS.items():
            d = self.demo(pid, rooms)
            pb = validate_demo_bytes(d.to_bytes(), self.pub)
            self.assertTrue(pb.ok)
            self.assertTrue(pb.frame_match)
            self.assertEqual(pb.final_cell, tuple(d.body["final_cell"]))

    def test_tampered_bytes_rejected(self):
        d = self.demo("player1", ["A"])
        raw = bytearray(d.to_bytes())
        raw[-2] ^= 1
        pb = validate_demo_bytes(bytes(raw), self.pub)
        self.assertFalse(pb.ok)

    def test_different_signer_rejected(self):
        imposter = Key.from_label("imposter")
        d = build_demo(imposter, self.receipt, self.ref, self.race,
                       "player1", ["A"])
        self.assertFalse(validate_demo_bytes(d.to_bytes(), self.pub).ok)

    def test_cannot_build_demo_from_unverified_receipt(self):
        from puzzle.referee import Receipt, VerificationError

        imposter = Key.from_label("imposter")
        # Re-sign a structurally valid receipt body with the wrong key.
        bad = Receipt(imposter.sign_body(dict(self.receipt.body)))
        with self.assertRaises(VerificationError):
            build_demo(self.pub, bad, self.ref, self.race, "player1", ["A"])


class TestRevocation(Base):
    def test_score_invariant_under_revocation(self):
        before = self.demo("player2", ["A", "B"])
        after = self.demo("player2", ["A"])
        self.assertEqual(before.body["verified_score"],
                         after.body["verified_score"])

    def test_download_stops_before_restricted_room(self):
        with tempfile.TemporaryDirectory() as root:
            store = TwoDirStore(os.path.join(root, "ref"),
                                os.path.join(root, "demo"))
            d = self.demo("player2", ["A", "B"])
            store.publish_demo(d)
            raw_full = store.download_demo("race-1", "player2")
            # revoke B
            d2 = self.demo("player2", ["A"])
            store.publish_demo(d2)
            raw_revoked = store.download_demo("race-1", "player2")
            self.assertNotEqual(raw_full, raw_revoked)
            pb = validate_demo_bytes(raw_revoked, self.pub)
            self.assertTrue(pb.ok)
            self.assertLessEqual(pb.final_cell[0], 5)  # never entered room B


class TestStore(Base):
    def test_two_dirs_write_and_read(self):
        with tempfile.TemporaryDirectory() as root:
            store = TwoDirStore(os.path.join(root, "ref"),
                                os.path.join(root, "demo"))
            store.save_start(self.race)
            store.save_receipt(self.receipt)
            loaded = store.load_receipt("race-1")
            # bytes are canonical and equal on re-read
            self.assertEqual(loaded.to_bytes(), self.receipt.to_bytes())
            d = self.demo("player1", ["A"])
            store.publish_demo(d)
            self.assertEqual(store.download_demo("race-1", "player1"),
                             d.to_bytes())


if __name__ == "__main__":
    unittest.main()
