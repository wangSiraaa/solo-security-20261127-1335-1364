"""Concrete fixtures: one shared race through two closing doors.

Layout (16 x 7).  Three rooms in a row, joined only by single door cells; the
doors slam shut behind the player, so the route is strictly A -> B -> C.

    x: 0  1 2 3 4  5  6 7 8 9  10 11 12 13 14 15

    room A: x 1..4 (door A|B at x=5,y=3)
    room B: x 6..9 (door B|C at x=10,y=3)
    room C: x 11..14

The canonical 15-action race solves one box per room and is <= 20 steps:

    D D R R R R U R R D R R R R R
     1 2 3 4 5 6 7 8 9 10 11 12 13 14 15

    step 6  : first frame inside room B (door A|B closes behind)
    step 13 : first frame inside room C (door B|C closes behind)

``altered_map`` moves the interior pillar wall (4,1) to the floor cell (8,2).
Same action string then jams on the room-B box push and stops after step 7.
"""
from __future__ import annotations

from typing import Dict, List

from .model import Map

WIDTH, HEIGHT = 16, 7

ROOM_RECTS = {
    "A": (1, 4, 1, 5),   # x0, x1, y0, y1 inclusive
    "B": (6, 9, 1, 5),
    "C": (11, 14, 1, 5),
}
DOOR_AB = (5, 3)
DOOR_BC = (10, 3)

PILLAR = (4, 1)         # interior wall used by the "move one wall" variant
ALTERED_WALL_DST = (8, 2)

RACE_ACTIONS: List[str] = list("DDRRRRURRDRRRRR")
# dwells[j] = time spent on the cell at absolute step j; len == actions + 1.
RACE_DWELLS: List[int] = [
    200, 180, 150, 150, 150, 220,   # steps 0-5: room A, onto door A|B
    260,                            # step 6: first cell in room B
    170, 180, 170,                  # steps 7-9: room B box onto goal
    150, 150, 210,                  # steps 10-12: onto door B|C
    240,                            # step 13: first cell in room C
    170, 200,                       # steps 14-15: room C box onto goal
]
ROOM_ENTRY_STEPS = {"A": 0, "B": 6, "C": 13}


def _rect(x0: int, x1: int, y0: int, y1: int) -> set:
    return {(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)}


def build_map() -> Map:
    regions: Dict[str, frozenset] = {}
    for room, (x0, x1, y0, y1) in ROOM_RECTS.items():
        regions[room] = frozenset(_rect(x0, x1, y0, y1))
    # Door cells belong to the room on their near side so they are passable.
    regions["A"] = regions["A"] | {DOOR_AB}
    regions["B"] = regions["B"] | {DOOR_BC}

    walls = set()
    # Outer border.
    for x in range(WIDTH):
        walls.add((x, 0))
        walls.add((x, HEIGHT - 1))
    for y in range(HEIGHT):
        walls.add((0, y))
        walls.add((WIDTH - 1, y))
    # Dividers between rooms, everywhere except the door cells.
    for y in range(1, 6):
        if (5, y) != DOOR_AB:
            walls.add((5, y))
        if (10, y) != DOOR_BC:
            walls.add((10, y))
    # Interior pillar in room A (a non-border wall we are allowed to relocate).
    walls.add(PILLAR)

    return Map(
        width=WIDTH,
        height=HEIGHT,
        regions=regions,
        walls=frozenset(walls),
        doors=frozenset({DOOR_AB, DOOR_BC}),
        boxes=frozenset({(2, 2), (7, 2), (12, 3)}),
        goals=frozenset({(2, 4), (9, 2), (14, 3)}),
        player_start=(2, 1),
    )


def altered_map() -> Map:
    """Base map with exactly one wall relocated: pillar (4,1) -> (8,2).

    (8,2) sits on the room-B box lane, so the reused action string now stops
    after step 7 instead of completing the race.
    """
    return build_map().moved_wall(PILLAR, ALTERED_WALL_DST)


# One room per player, per the brief: player1 sees A, player2 sees A and B.
PLAYER_UNLOCKS: Dict[str, List[str]] = {
    "player1": ["A"],
    "player2": ["A", "B"],
}
