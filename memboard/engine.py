"""Executing a maintenance plan against the simulated board.

The engine moves data in the stub order (*begin clone -> finalise -> retire
source*) so that a source row is never removed while its replacement is
unreadable.  It supports:

* a maintenance window: no classroom traffic is served while the board is
  offline, and the plan must fit inside the window;
* mid-migration evidence arrival: a hook fires after ``at_move`` relocations,
  letting a scenario inject a fresh correction receipt or a firmware
  generation change while data is in flight.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from .geometry import Geometry, RowAddr
from .planner import MaintenancePlan
from .sim import BoardSim, MigrationError


class WindowExceeded(MigrationError):
    pass


@dataclass
class MigrationEvent:
    step: int
    kind: str
    detail: str


@dataclass
class MigrationResult:
    events: list[MigrationEvent] = field(default_factory=list)
    completed_moves: list[str] = field(default_factory=list)
    abandoned_moves: list[str] = field(default_factory=list)
    offline_start_tick: int = 0
    offline_end_tick: int = 0
    window: int = 0
    completed: bool = False


def execute_plan(
    plan: MaintenancePlan,
    board: BoardSim,
    window: int,
    mid_hook: Optional[Callable[[BoardSim, MigrationResult, int], None]] = None,
    hook_at_move: int | None = None,
) -> MigrationResult:
    """Run ``plan`` move-by-move on ``board``.

    ``mid_hook(board, result, step)`` fires once, after ``hook_at_move`` moves
    have finished (a correction arriving while the migration is underway).
    """

    if window < len(plan.moves):
        raise WindowExceeded(
            f"plan needs {len(plan.moves)} moves, maintenance window is {window}"
        )

    result = MigrationResult(window=window, offline_start_tick=0)
    board_was = board.generation

    # Guard: the plan was computed for a generation; if firmware changed since,
    # the moves are stale.
    if plan.computed_for_generation != board.generation:
        raise MigrationError(
            "plan was computed for generation "
            f"{plan.computed_for_generation} but board is at {board.generation}; re-plan"
        )

    for step, move in enumerate(plan.moves):
        try:
            board.begin_clone(move)
            result.events.append(MigrationEvent(step, "clone-staged", move.label()))
            board.finalise_clone(move)
            result.events.append(MigrationEvent(step, "clone-finalised", move.label()))
            board.retire_source(move)
            result.events.append(MigrationEvent(step, "source-retired", move.label()))
            result.completed_moves.append(move.label())
        except MigrationError as exc:
            board.abandon_clone(move)
            result.abandoned_moves.append(f"{move.label()} ({exc})")
            result.events.append(MigrationEvent(step, "move-abandoned", str(exc)))

        if mid_hook is not None and hook_at_move is not None and (step + 1) == hook_at_move:
            mid_hook(board, result, step + 1)
            result.events.append(MigrationEvent(step, "mid-hook-fired", "external evidence arrived"))

    result.offline_end_tick = window
    result.completed = (
        len(result.abandoned_moves) == 0 and len(result.completed_moves) == len(plan.moves)
    )
    return result


def verify_spare_allocation(board: BoardSim) -> list[str]:
    """Audit that no spare row was promised to two slices.

    Checks the live board state: each spare-owning slice must have a distinct
    spare row, and no pending clone may share a target with an established
    owner.
    """

    problems: list[str] = []
    seen: dict[RowAddr, tuple[int, int]] = {}
    for spare, owner in board.spare_owners.items():
        if spare in seen:
            problems.append(
                f"spare {spare.label()} double-allocated to {seen[spare]} and {owner}"
            )
        seen[spare] = owner
    for spare, owner in board.pending_clones.items():
        if spare in seen and seen[spare] != owner:
            problems.append(
                f"spare {spare.label()} pending clone for {owner} collides with active owner {seen[spare]}"
            )
    return problems


def verify_plan_spares(plan: MaintenancePlan) -> list[str]:
    """Audit the plan itself for duplicate spare targets."""

    problems: list[str] = []
    targets: dict[RowAddr, str] = {}
    for move in plan.moves:
        if move.target in targets:
            problems.append(
                f"plan allocates {move.target.label()} to both {targets[move.target]} and "
                f"pg{move.page}/ch{move.channel}"
            )
        targets[move.target] = f"pg{move.page}/ch{move.channel}"
    return problems
