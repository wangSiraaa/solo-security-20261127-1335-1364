"""Small Sokoban-style board used by every party.

Design notes that the rest of the system relies on:

* A ``Map`` is a *spatial* description: three room **regions**, the static
  ``walls`` inside/between them, ``doors`` that close behind the player, and
  the initial boxes/goals/player.  A cell is passable iff it lies in a region
  and is not a wall and is not a door that has already closed.  Keeping
  regions independent of what is currently a wall is what lets a test *move a
  wall* (a cell stays in its room, only its wall-ness changes) and recompute a
  genuinely different puzzle.
* Replay stops at the first impossible action.  The prompt asks the referee to
  "point out where it actually stops", so a blocked move terminates the
  playable prefix instead of being silently skipped.
* Everything that gets signed is produced by ``to_dict`` and is therefore
  deterministic under :mod:`puzzle.canon`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

Cell = Tuple[int, int]
DELTAS = {"U": (0, -1), "D": (0, 1), "L": (-1, 0), "R": (1, 0)}
VALID_ACTIONS = frozenset(DELTAS)


def _cell_list(cells: Iterable[Cell]) -> List[List[int]]:
    return [[x, y] for x, y in sorted(cells)]


def _cells(raw: Iterable[Sequence[int]]) -> frozenset:
    return frozenset((int(x), int(y)) for x, y in raw)


@dataclass(frozen=True)
class Map:
    """A puzzle map with room regions, walls, doors, boxes and goals."""

    width: int
    height: int
    regions: Dict[str, frozenset]   # room_id -> all cells in that region
    walls: frozenset                # impassable cells (subset of regions or void)
    doors: frozenset                # cells that close after being left
    boxes: frozenset                # initial box cells
    goals: frozenset
    player_start: Cell

    # -- geometry ---------------------------------------------------------
    def region_of(self, cell: Cell) -> Optional[str]:
        for room, cells in self.regions.items():
            if cell in cells:
                return room
        return None

    def in_region(self, cell: Cell) -> bool:
        return self.region_of(cell) is not None

    def passable(self, cell: Cell, closed_doors: frozenset) -> bool:
        return (
            self.in_region(cell)
            and cell not in self.walls
            and cell not in closed_doors
        )

    # -- serialization ----------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "width": self.width,
            "height": self.height,
            "regions": {k: _cell_list(v) for k, v in sorted(self.regions.items())},
            "walls": _cell_list(self.walls),
            "doors": _cell_list(self.doors),
            "boxes": _cell_list(self.boxes),
            "goals": _cell_list(self.goals),
            "player_start": None if self.player_start is None
            else list(self.player_start),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Map":
        ps = d.get("player_start")
        return cls(
            width=d["width"],
            height=d["height"],
            regions={k: _cells(v) for k, v in d["regions"].items()},
            walls=_cells(d["walls"]),
            doors=_cells(d["doors"]),
            boxes=_cells(d["boxes"]),
            goals=_cells(d["goals"]),
            player_start=None if ps is None else (int(ps[0]), int(ps[1])),
        )

    # -- puzzle identity --------------------------------------------------
    @property
    def puzzle_commit(self) -> str:
        from .canon import hx_obj

        return hx_obj(self.to_dict())

    def _unlocked_cells(self, unlocked_rooms: Iterable[str]) -> frozenset:
        cells = set()
        for room in unlocked_rooms:
            cells |= set(self.regions.get(room, ()))
        return frozenset(cells)

    def restricted_map(self, unlocked_rooms: Iterable[str],
                        player_start: Optional[Cell] = None) -> "Map":
        """A Map containing *only* the cells of ``unlocked_rooms``.

        Walls, doors, boxes and goals of other rooms are dropped entirely, and
        since inter-room dividers / void cells belong to no region they remain
        impassable automatically.  ``player_start`` overrides the start so a
        demo segment can begin at an arbitrary (visible) cell.
        """
        rooms = set(unlocked_rooms)
        allowed = self._unlocked_cells(rooms)
        regions = {
            room: frozenset(c for c in cells if c in allowed)
            for room, cells in self.regions.items()
            if room in rooms
        }
        ps = player_start if player_start is not None else (
            self.player_start if self.player_start in allowed else None
        )
        return Map(
            width=self.width,
            height=self.height,
            regions=regions,
            walls=frozenset(c for c in self.walls if c in allowed),
            doors=frozenset(c for c in self.doors if c in allowed),
            boxes=frozenset(c for c in self.boxes if c in allowed),
            goals=frozenset(c for c in self.goals if c in allowed),
            player_start=ps,  # type: ignore[arg-type]
        )

    def restricted_view(self, unlocked_rooms: Iterable[str]) -> dict:
        """Map dict containing *only* cells inside unlocked rooms.

        Cells of locked rooms are absent entirely (not merely hidden), so a
        player's file cannot carry the geometry, boxes, goals or doors of a
        room they have not opened.
        """
        rooms = set(unlocked_rooms)
        rm = self.restricted_map(rooms)
        d = rm.to_dict()
        if rm.player_start is None:
            d["player_start"] = None
        return d

    def moved_wall(self, src: Cell, dst: Cell) -> "Map":
        """Return a copy with the wall at ``src`` relocated to ``dst``.

        Both cells keep their room membership; only wall-ness moves.  That is
        enough to make a chokepoint impassable (or to reopen one) while
        producing a new ``puzzle_commit`` so old tracks cannot be reused.
        """
        walls = set(self.walls)
        if src not in walls:
            raise ValueError(f"no wall at {src} to move")
        walls.discard(src)
        walls.add(dst)
        return Map(
            width=self.width,
            height=self.height,
            regions=self.regions,
            walls=frozenset(walls),
            doors=self.doors,
            boxes=self.boxes,
            goals=self.goals,
            player_start=self.player_start,
        )

    def initial_state(self) -> "State":
        return State(
            player=self.player_start,
            boxes=self.boxes,
            closed_doors=frozenset(),
            step=0,
        )

    def render(self, unlocked_rooms: Optional[Iterable[str]] = None,
               state: Optional["State"] = None) -> List[str]:
        """ASCII rendering, optionally restricted to ``unlocked_rooms``.

        Any cell outside the unlocked set renders as wall, so a restricted
        room's floor, boxes, goals and doors never appear in the output.
        """
        unlocked = set(self.regions) if unlocked_rooms is None else set(unlocked_rooms)
        boxes = self.boxes if state is None else state.boxes
        player = self.player_start if state is None else state.player
        closed = frozenset() if state is None else state.closed_doors
        rows = []
        for y in range(self.height):
            row = []
            for x in range(self.width):
                c = (x, y)
                room = self.region_of(c)
                if room is None or room not in unlocked:
                    row.append("#")
                elif c in self.walls:
                    row.append("#")
                elif self.doors and c in self.doors and c in closed:
                    row.append("#")
                elif c == player:
                    row.append("+" if c in self.goals else "P")
                elif c in boxes:
                    row.append("*" if c in self.goals else "B")
                elif c in self.goals:
                    row.append("G")
                else:
                    row.append(".")
            rows.append("".join(row))
        return rows


@dataclass(frozen=True)
class State:
    player: Cell
    boxes: frozenset
    closed_doors: frozenset
    step: int

    def to_dict(self) -> dict:
        return {
            "player": list(self.player),
            "boxes": _cell_list(self.boxes),
            "closed_doors": _cell_list(self.closed_doors),
            "step": self.step,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "State":
        return cls(
            player=(int(d["player"][0]), int(d["player"][1])),
            boxes=_cells(d["boxes"]),
            closed_doors=_cells(d["closed_doors"]),
            step=int(d["step"]),
        )


@dataclass(frozen=True)
class StepResult:
    ok: bool
    state: State                    # state after the (attempted) move
    push: Optional[Tuple[Cell, Cell]] = None  # (box_from, box_to) if pushed


def apply_action(game_map: Map, state: State, action: str) -> StepResult:
    """Apply one action. A blocked move returns ``ok=False`` with *unchanged*
    player/boxes but a bumped step counter (the run stops on it)."""
    if action not in VALID_ACTIONS:
        raise ValueError(f"invalid action {action!r}")
    dx, dy = DELTAS[action]
    target = (state.player[0] + dx, state.player[1] + dy)
    blocked = StepResult(False, State(state.player, state.boxes,
                                      state.closed_doors, state.step + 1))

    if not game_map.passable(target, state.closed_doors):
        return blocked

    boxes = set(state.boxes)
    push = None
    if target in boxes:
        beyond = (target[0] + dx, target[1] + dy)
        if (not game_map.passable(beyond, state.closed_doors)
                or beyond in boxes):
            return blocked
        boxes.discard(target)
        boxes.add(beyond)
        push = (target, beyond)

    closed = set(state.closed_doors)
    # Leaving a door closes it behind the player.
    if state.player in game_map.doors:
        closed.add(state.player)

    new_state = State(target, frozenset(boxes), frozenset(closed), state.step + 1)
    return StepResult(True, new_state, push)


def boxes_on_goal(game_map: Map, state: State) -> int:
    return len(state.boxes & game_map.goals)


def compute_score(game_map: Map, visited_unique: Sequence[Cell],
                  state: State) -> int:
    """Verified score: reward solved boxes and exploration.

    Redaction never touches this; it is a property of the run, so revoking a
    room's demo eligibility cannot move it.
    """
    return 50 * boxes_on_goal(game_map, state) + len(visited_unique)
