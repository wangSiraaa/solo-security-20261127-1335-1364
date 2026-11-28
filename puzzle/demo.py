"""Demo publication and player-side validation.

A demo station hands each player a playable guide track built from a certified
receipt, restricted to the rooms that player has actually unlocked:

* The embedded map is a ``restricted_map`` — locked rooms' floor, boxes, goals
  and doors are absent, not merely hidden.
* The trace is cut at the *last frame before* the player would first enter a
  locked room.  That frame is included; the entering action is not.  So the
  track never reveals a resident of a room the player cannot see.
* The file is self-describing enough that the acceptance person can play it
  step by step on the player's own restricted map and compare the final cell
  and the exact handed-over bytes.

Everything is canonicalized, so ``demo_bytes`` is byte-reproducible and
``validate_demo`` re-derives and compares the actual bytes it was given.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence

from .canon import canon_bytes, hx_obj
from .model import Map, State
from .referee import Receipt, RaceStart, verify_receipt
from .signing import Key
from . import replay as replay_mod

ARTIFACT_DEMO = "demo"

# Sentinel: no locked-room entry found (whole run stays in unlocked rooms).
NO_CUT = None


@dataclass
class DemoFile:
    """A published demo track for one player and one race."""

    envelope: Dict[str, Any]

    @property
    def body(self) -> dict:
        return self.envelope["body"]

    def to_bytes(self) -> bytes:
        return canon_bytes(self.envelope)

    def to_dict(self) -> dict:
        return self.envelope


def _first_locked_entry(receipt_body: dict, game_map: Map,
                        unlocked: set) -> Optional[int]:
    """Absolute step of the first frame that lands in a locked room.

    Returns None if the whole visited path stays within unlocked rooms.
    """
    for entry in receipt_body["trace"]:
        cell = (entry["cell"][0], entry["cell"][1])
        room = game_map.region_of(cell)
        if room is not None and room not in unlocked:
            return int(entry["step"])
    return None


def build_demo(
    publisher_key: Key,
    receipt: Receipt,
    referee_key: Key,
    race: RaceStart,
    player_id: str,
    unlocked_rooms: Sequence[str],
) -> DemoFile:
    """Assemble and sign a player-specific demo track.

    ``race`` must be the :class:`RaceStart` the receipt chains to.  The receipt
    is re-verified first, so a demo can only be built from a genuine, race-bound
    certified run.
    """
    assert isinstance(race, RaceStart)
    receipt_body = verify_receipt(receipt, referee_key, race)
    game_map = race.game_map
    unlocked = set(unlocked_rooms)

    cut_step = _first_locked_entry(receipt_body, game_map, unlocked)

    # Frames to publish: everything strictly before the first locked-room frame.
    if cut_step is None:
        frames = list(receipt_body["trace"])
        stop_reason = "complete"
    else:
        frames = [e for e in receipt_body["trace"] if e["step"] < cut_step]
        stop_reason = "locked_room"

    # Trace restricted to unlocked rooms is required by construction; assert it.
    for e in frames:
        cell = (e["cell"][0], e["cell"][1])
        room = game_map.region_of(cell)
        if room is not None and room not in unlocked:
            raise AssertionError("demo frame leaked into a locked room")

    # Starting cell of the demo is the first published frame's cell.
    start_cell = (frames[0]["cell"][0], frames[0]["cell"][1]) if frames else None
    restricted = game_map.restricted_map(unlocked, player_start=start_cell)

    actions = [e["action"] for e in frames if e["action"] is not None]
    dwells = [e["dwell_ms"] for e in frames]

    body = {
        "kind": ARTIFACT_DEMO,
        "race_id": receipt_body["race_id"],
        "map_id": receipt_body["map_id"],
        "player_id": player_id,
        "unlocked_rooms": sorted(unlocked),
        "source_receipt_commit": receipt.commit,
        "source_start_commit": receipt_body["start_commit"],
        "puzzle_commit_full": receipt_body["puzzle_commit"],
        "puzzle_commit_visible": hx_obj(restricted.to_dict()),
        "map": restricted.to_dict(),
        "start_cell": list(start_cell) if start_cell else None,
        "actions": actions,
        "dwells": dwells,
        "frames": frames,
        "stop_reason": stop_reason,
        "cut_before_step": cut_step,
        "final_cell": frames[-1]["cell"] if frames else None,
        "verified_score": receipt_body["verified_score"],
    }
    return DemoFile(publisher_key.sign_body(body))


# ---------------------------------------------------------------------------
# Player-side playback / acceptance check
# ---------------------------------------------------------------------------
@dataclass
class PlaybackResult:
    ok: bool
    final_cell: Optional[tuple]
    final_step: Optional[int]
    applied_actions: int
    frame_match: bool
    blocked_action: Optional[str]


def playback(demo: DemoFile, publisher_key: Key,
             expected_bytes: Optional[bytes] = None) -> PlaybackResult:
    """Replay the demo on its own embedded restricted map, step by step.

    If ``expected_bytes`` is supplied (the bytes the player actually received),
    the demo is first canonicalized and compared to them exactly — proving the
    handed-over file is the same object.  Then each frame must reproduce: the
    replayed cell at each step equals the published frame's cell, and the final
    cell equals ``body["final_cell"]``.
    """
    try:
        body = publisher_key.verify_envelope(demo.envelope)
    except Exception as exc:  # noqa: BLE001 - surfaced as a failed playback
        return PlaybackResult(False, None, None, 0, False, f"signature: {exc}")

    if expected_bytes is not None:
        if canon_bytes(demo.to_dict()) != expected_bytes:
            return PlaybackResult(False, None, None, 0, False,
                                  "handed-over bytes differ from demo")

    game_map = Map.from_dict(body["map"])
    start_cell = body["start_cell"]
    if start_cell is None:
        return PlaybackResult(True, None, None, 0, True, None)

    start = game_map.initial_state()
    # initial_state uses player_start; demo start_cell overrides it.
    start = State(player=(start_cell[0], start_cell[1]),
                  boxes=start.boxes, closed_doors=frozenset(), step=0)
    res = replay_mod.replay(game_map, start, body["actions"], body["dwells"],
                            include_start_entry=True)

    # Compare the replay against the published frames (cell + step + action).
    pub = body["frames"]
    replay_frames = res.trace_dicts()
    frame_match = replay_frames == pub

    if not res.ok:
        return PlaybackResult(False, tuple(res.final_state.player),
                              res.stopped_after_step, res.applied_actions,
                              frame_match, res.blocked_action)
    final_cell = res.final_state.player
    expected_final = body["final_cell"]
    final_ok = (expected_final is None or
                [final_cell[0], final_cell[1]] == list(expected_final))
    return PlaybackResult(final_ok and frame_match, tuple(final_cell),
                          res.stopped_after_step, res.applied_actions,
                          frame_match, None)


def validate_demo_bytes(raw: bytes, publisher_key: Key) -> PlaybackResult:
    """Acceptance entry point: validate a demo from the exact received bytes."""
    import json

    try:
        env = json.loads(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        return PlaybackResult(False, None, None, 0, False, f"parse: {exc}")
    return playback(DemoFile(env), publisher_key, expected_bytes=raw)
