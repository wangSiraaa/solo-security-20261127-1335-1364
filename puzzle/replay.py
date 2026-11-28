"""Replay claimed actions from a starting state and record what really happened.

This is the single source of truth for "did the actions reach?", "which boxes
were pushed?", "which cells did the player actually stand on?", and "where did
the run stop?".  Both the referee's receipt and the acceptance person's
step-by-step playback go through here, so their numbers cannot drift.

Step numbers are *absolute*: they come from ``state.step`` inside the starting
state and increment on every applied action.  That makes continuation from an
attested midpoint use the same numbering as a full replay from the start.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from .model import (
    Cell,
    Map,
    State,
    VALID_ACTIONS,
    apply_action,
)

__all__ = [
    "TraceEntry",
    "ReplayResult",
    "replay",
]


@dataclass(frozen=True)
class TraceEntry:
    step: int
    cell: Cell
    action: Optional[str]      # action that *arrived* at this cell (None for a start frame)
    dwell_ms: int              # time spent on this cell before the next action

    def to_dict(self) -> dict:
        return {
            "step": self.step,
            "cell": [self.cell[0], self.cell[1]],
            "action": self.action,
            "dwell_ms": self.dwell_ms,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TraceEntry":
        return cls(
            step=int(d["step"]),
            cell=(int(d["cell"][0]), int(d["cell"][1])),
            action=d["action"],
            dwell_ms=int(d["dwell_ms"]),
        )


@dataclass
class ReplayResult:
    ok: bool                       # every claimed action was reachable
    applied_actions: int           # successful actions (the playable prefix length)
    blocked_action: Optional[str]  # the first unreachable action, if any
    stopped_after_step: Optional[int]  # absolute step number of the last good state
    final_state: State
    trace: List[TraceEntry]        # start frame + one frame per applied action
    pushes: List[dict]             # {step, action, box_from, box_to}
    visited: List[Cell]            # unique player cells, first-visit order

    def trace_dicts(self) -> List[dict]:
        return [e.to_dict() for e in self.trace]


def replay(
    game_map: Map,
    start_state: State,
    actions: Sequence[str],
    dwells: Optional[Sequence[int]] = None,
    include_start_entry: bool = True,
) -> ReplayResult:
    """Replay ``actions``.

    ``dwells`` has length ``len(actions) + 1``; ``dwells[j]`` is the time spent
    on the cell reached at absolute step ``start_state.step + j``.  Missing
    dwells default to zero.

    ``include_start_entry`` is False when continuing from an attested midpoint:
    the attested cell is already present in the prefix trace, so we must not
    emit a duplicate start frame.
    """
    n = len(actions)
    if any(a not in VALID_ACTIONS for a in actions):
        bad = next(a for a in actions if a not in VALID_ACTIONS)
        raise ValueError(f"invalid action token {bad!r}")
    if dwells is None:
        dwells = [0] * (n + 1)
    if len(dwells) != n + 1:
        raise ValueError("dwells must have len(actions)+1 entries")
    if any((not isinstance(d, int)) or d < 0 for d in dwells):
        raise ValueError("dwell_ms values must be non-negative integers")

    trace: List[TraceEntry] = []
    if include_start_entry:
        trace.append(TraceEntry(start_state.step, start_state.player, None, dwells[0]))

    state = start_state
    visited: List[Cell] = [state.player]
    seen = {state.player}
    pushes: List[dict] = []
    # Identify each box by the cell it started on, and follow it as it moves.
    box_id_at: Dict[Cell, Cell] = {c: c for c in start_state.boxes}
    blocked_action: Optional[str] = None
    applied = 0

    for i, action in enumerate(actions):
        res = apply_action(game_map, state, action)
        if not res.ok:
            blocked_action = action
            break
        applied += 1
        state = res.state
        if res.push is not None:
            box_from, box_to = res.push
            box_id = box_id_at.pop(box_from)
            box_id_at[box_to] = box_id
            pushes.append({
                "step": state.step,
                "action": action,
                "box": [box_id[0], box_id[1]],      # initial cell = identity
                "box_from": [box_from[0], box_from[1]],
                "box_to": [box_to[0], box_to[1]],
            })
        if state.player not in seen:
            seen.add(state.player)
            visited.append(state.player)
        trace.append(TraceEntry(
            state.step,
            state.player,
            action,
            dwells[i + 1] if (i + 1) < len(dwells) else 0,
        ))

    return ReplayResult(
        ok=blocked_action is None,
        applied_actions=applied,
        blocked_action=blocked_action,
        stopped_after_step=state.step,
        final_state=state,
        trace=trace,
        pushes=pushes,
        visited=visited,
    )
