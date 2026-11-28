"""Replay, board mechanics, and the altered-map stop."""
import unittest

from puzzle import fixtures as F
from puzzle.model import Map, State, apply_action, boxes_on_goal
from puzzle.replay import replay


class TestBoard(unittest.TestCase):
    def test_render_hides_locked_rooms(self):
        m = F.build_map()
        full = m.render()
        only_a = m.render(unlocked_rooms=["A"])
        # Every non-wall cell outside room A renders as wall.
        allowed = set(m.regions["A"])
        for y, row in enumerate(only_a):
            for x, ch in enumerate(row):
                if (x, y) not in allowed:
                    self.assertEqual(ch, "#", f"leak at {(x, y)}")
        self.assertEqual(len(full), len(only_a))

    def test_door_closes_behind_player(self):
        m = F.build_map()
        st = m.initial_state()
        r = replay(m, st, list("RRR"), [0, 0, 0, 0])  # (2,1)->(3,1)->(4,1)
        # Move onto/through the A|B door and confirm it is closed after.
        res = replay(m, st, ["D", "D"] + ["R"] * 4, [0] * 7)
        self.assertIn(F.DOOR_AB, res.final_state.closed_doors)
        # The door cell is now impassable backwards.
        blocked = apply_action(m, res.final_state, "L")
        self.assertFalse(blocked.ok)

    def test_wall_blocks_move(self):
        m = F.build_map()
        st = m.initial_state()
        self.assertFalse(apply_action(m, st, "U").ok)  # border wall above


class TestRace(unittest.TestCase):
    def setUp(self):
        self.m = F.build_map()

    def test_shared_race_completes(self):
        res = replay(self.m, self.m.initial_state(),
                     F.RACE_ACTIONS, F.RACE_DWELLS)
        self.assertTrue(res.ok)
        self.assertEqual(res.applied_actions, len(F.RACE_ACTIONS))
        self.assertEqual(res.stopped_after_step, len(F.RACE_ACTIONS))
        self.assertEqual(boxes_on_goal(self.m, res.final_state), len(self.m.goals))
        # exactly the two doors closed, in order
        self.assertEqual(res.final_state.closed_doors, {F.DOOR_AB, F.DOOR_BC})
        # trace has a start frame + one per action
        self.assertEqual(len(res.trace), len(F.RACE_ACTIONS) + 1)

    def test_box_identity_follows_pushes(self):
        res = replay(self.m, self.m.initial_state(),
                     F.RACE_ACTIONS, F.RACE_DWELLS)
        # three distinct boxes pushed (identified by their start cells)
        ids = {tuple(p["box"]) for p in res.pushes}
        self.assertEqual(ids, {(2, 2), (7, 2), (12, 3)})

    def test_room_entry_steps(self):
        res = replay(self.m, self.m.initial_state(),
                     F.RACE_ACTIONS, F.RACE_DWELLS)
        for room, step in F.ROOM_ENTRY_STEPS.items():
            entry = next(t for t in res.trace if t.step == step)
            self.assertEqual(self.m.region_of(entry.cell), room)

    def test_replay_stops_at_first_block(self):
        # A wall-bumping action stops the playable prefix.
        res = replay(self.m, self.m.initial_state(), ["U", "D"], [0, 0, 0])
        self.assertFalse(res.ok)
        self.assertEqual(res.applied_actions, 0)
        self.assertEqual(res.blocked_action, "U")

    def test_visited_is_unique_first_order(self):
        res = replay(self.m, self.m.initial_state(),
                     F.RACE_ACTIONS, F.RACE_DWELLS)
        self.assertEqual(len(res.visited), len(set(res.visited)))
        self.assertEqual(res.visited[0], self.m.player_start)

    def test_dwell_validation(self):
        with self.assertRaises(ValueError):
            replay(self.m, self.m.initial_state(), ["D"], [0])
        with self.assertRaises(ValueError):
            replay(self.m, self.m.initial_state(), ["D"], [0, -5])


class TestAlteredMap(unittest.TestCase):
    def test_one_wall_moved_changes_puzzle_and_stops_reuse(self):
        base = F.build_map()
        altered = F.altered_map()
        self.assertNotEqual(base.puzzle_commit, altered.puzzle_commit)
        self.assertEqual(base.walls - altered.walls, {F.PILLAR})
        self.assertEqual(altered.walls - base.walls, {F.ALTERED_WALL_DST})

        res = replay(altered, altered.initial_state(),
                     F.RACE_ACTIONS, F.RACE_DWELLS)
        self.assertFalse(res.ok)
        self.assertEqual(res.stopped_after_step, 7)
        self.assertEqual(res.final_state.player, (6, 2))
        self.assertEqual(res.blocked_action, "R")


class TestModelSerialization(unittest.TestCase):
    def test_map_roundtrip_stable_commit(self):
        m = F.build_map()
        d = m.to_dict()
        m2 = Map.from_dict(d)
        self.assertEqual(m.puzzle_commit, m2.puzzle_commit)
        self.assertEqual(State.from_dict(m.initial_state().to_dict()),
                         m.initial_state())

    def test_restricted_map_drops_locked_content(self):
        m = F.build_map()
        r = m.restricted_map(["A"])
        self.assertEqual(set(r.regions), {"A"})
        for c in r.boxes | r.goals | r.doors:
            self.assertEqual(m.region_of(c), "A")


if __name__ == "__main__":
    unittest.main()
