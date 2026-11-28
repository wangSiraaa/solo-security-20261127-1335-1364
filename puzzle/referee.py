"""Referee: pre-race signed starts, verified race-bound receipts, and
mid-run hand-off attestations.

Two participating models, per the brief, both supported here:

* **Full replay** — :func:`issue_start` signs the map and initial state before
  the race; :func:`certify` replays the submitted actions from that signed
  start.  A lost opening cannot be "filled in" later because the receipt must
  chain to the pre-race start commitment and reproduce it exactly.

* **Mid-run hand-off** — :func:`attest_midpoint` runs (and signs) an actually
  executed middle segment, fixing the state, step count and covered range.
  :func:`continue_from` then takes a *later* segment and validates it against
  that attested midpoint without ever re-inventing earlier actions.

A *fork* is any claimed action sequence whose bytes diverge from the certified
history while claiming the same race; :func:`detect_fork` pins where.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence

from .canon import hx_obj
from .signing import commit_of
from .model import Map, State, compute_score
from .replay import replay
from .signing import Key, SignatureError

ARTIFACT_START = "race-start"
ARTIFACT_RECEIPT = "receipt"
ARTIFACT_MIDPOINT = "midpoint"
ARTIFACT_CONT = "continuation"


class VerificationError(Exception):
    """Raised when a claim cannot be authenticated against its anchors."""


# ---------------------------------------------------------------------------
# Pre-race start
# ---------------------------------------------------------------------------
@dataclass
class RaceStart:
    """A signed race start: the map commitment plus the initial state."""

    envelope: Dict[str, Any]

    @property
    def race_id(self) -> str:
        return self.envelope["body"]["race_id"]

    @property
    def map_dict(self) -> dict:
        return self.envelope["body"]["map"]

    @property
    def start_state(self) -> State:
        return State.from_dict(self.envelope["body"]["initial_state"])

    @property
    def game_map(self) -> Map:
        return Map.from_dict(self.map_dict)

    @property
    def puzzle_commit(self) -> str:
        return hx_obj(self.map_dict)

    @property
    def map_id(self) -> str:
        return self.envelope["body"]["map_id"]

    @property
    def start_commit(self) -> str:
        """Commitment binding race id, map, and initial state."""
        return hx_obj(self.envelope["body"])

    def verify(self, key: Key) -> dict:
        try:
            body = key.verify_envelope(self.envelope)
        except SignatureError as exc:
            raise VerificationError(f"race start not authentic: {exc}") from exc
        if body.get("kind") != ARTIFACT_START:
            raise VerificationError("not a race-start artifact")
        return body

    def to_bytes(self) -> bytes:
        from .signing import envelope_bytes

        return envelope_bytes(self.envelope)


def issue_start(referee_key: Key, race_id: str, game_map: Map,
                map_id: Optional[str] = None) -> RaceStart:
    """Sign a pre-race start binding a map number, the map and its initial state."""
    state = game_map.initial_state()
    body = {
        "kind": ARTIFACT_START,
        "race_id": race_id,
        "map_id": map_id if map_id is not None else game_map.puzzle_commit[:16],
        "map": game_map.to_dict(),
        "initial_state": state.to_dict(),
    }
    return RaceStart(referee_key.sign_body(body))

# ---------------------------------------------------------------------------
# Receipt
# ---------------------------------------------------------------------------
@dataclass
class Receipt:
    envelope: Dict[str, Any]

    @property
    def race_id(self) -> str:
        return self.envelope["body"]["race_id"]

    @property
    def body(self) -> dict:
        return self.envelope["body"]

    @property
    def commit(self) -> str:
        return commit_of(self.envelope)

    def to_bytes(self) -> bytes:
        from .signing import envelope_bytes

        return envelope_bytes(self.envelope)


def certify(
    referee_key: Key,
    race: RaceStart,
    actions: Sequence[str],
    dwells: Sequence[int],
    self_reported_score: Optional[int] = None,
) -> Receipt:
    """Replay ``actions`` from the signed start and sign a race-bound receipt.

    The receipt records which actions were reachable, which boxes were pushed
    (identified by initial cell), which cells were actually visited, the
    verified score, and the exact starting point it chains to.
    """
    race.verify(referee_key)  # ensure the start is ours and intact
    game_map = race.game_map
    state = race.start_state
    res = replay(game_map, state, actions, dwells, include_start_entry=True)

    verified_score = compute_score(game_map, res.visited, res.final_state)
    body = {
        "kind": ARTIFACT_RECEIPT,
        "race_id": race.race_id,
        "map_id": race.map_id,
        "start_commit": race.start_commit,
        "puzzle_commit": race.puzzle_commit,
        "actions": list(actions),
        "dwells": list(dwells),
        "reachable": res.ok,
        "applied_actions": res.applied_actions,
        "blocked_action": res.blocked_action,
        "stopped_after_step": res.stopped_after_step,
        "final_state": res.final_state.to_dict(),
        "pushes": res.pushes,
        "visited": [[c[0], c[1]] for c in res.visited],
        "trace": res.trace_dicts(),
        "verified_score": verified_score,
        "self_reported_score": self_reported_score,
        # Server-side truth about the full run: what a *full* demo needs.
        "full_trace": res.trace_dicts(),
    }
    return Receipt(referee_key.sign_body(body))


def verify_receipt(receipt: Receipt, referee_key: Key,
                   race: RaceStart) -> dict:
    """Authenticate a receipt and prove it re-derives from the signed start.

    This is the acceptance-path check: the claimed actions are replayed
    independently and must reproduce, byte-for-byte, the signed fields.  It
    refuses receipts that chain to a different start.
    """
    race_body = race.verify(referee_key)
    try:
        body = referee_key.verify_envelope(receipt.envelope)
    except SignatureError as exc:
        raise VerificationError(f"receipt not authentic: {exc}") from exc
    if body.get("kind") != ARTIFACT_RECEIPT:
        raise VerificationError("not a receipt")
    if body["race_id"] != race_body["race_id"]:
        raise VerificationError("receipt belongs to a different race")
    if body["start_commit"] != race.start_commit:
        raise VerificationError("receipt chains to a different start")
    if body.get("map_id") != race_body.get("map_id"):
        raise VerificationError("receipt map id mismatch")

    game_map = race.game_map
    res = replay(game_map, race.start_state, body["actions"], body["dwells"])
    expected = {
        "puzzle_commit": race.puzzle_commit,
        "reachable": res.ok,
        "applied_actions": res.applied_actions,
        "blocked_action": res.blocked_action,
        "stopped_after_step": res.stopped_after_step,
        "final_state": res.final_state.to_dict(),
        "pushes": res.pushes,
        "visited": [[c[0], c[1]] for c in res.visited],
        "trace": res.trace_dicts(),
        "full_trace": res.trace_dicts(),
        "verified_score": compute_score(game_map, res.visited, res.final_state),
    }
    for field, want in expected.items():
        if body.get(field) != want:
            raise VerificationError(f"receipt field {field!r} does not re-derive")
    return body


# ---------------------------------------------------------------------------
# Mid-run hand-off
# ---------------------------------------------------------------------------
@dataclass
class Midpoint:
    envelope: Dict[str, Any]

    @property
    def body(self) -> dict:
        return self.envelope["body"]

    @property
    def commit(self) -> str:
        return commit_of(self.envelope)

    def to_bytes(self) -> bytes:
        from .signing import envelope_bytes

        return envelope_bytes(self.envelope)


def attest_midpoint(
    referee_key: Key,
    race: RaceStart,
    witnessed_actions: Sequence[str],
    witnessed_dwells: Sequence[int],
) -> Midpoint:
    """Execute a middle segment locally and sign the resulting state.

    ``witnessed_actions`` is the segment the trusted local referee actually ran
    *from the race start*.  The attestation fixes: the state reached, the
    absolute step reached, and the covered range.  It does not claim anything
    outside what it executed, so a later verifier can resume from it without
    fabricating earlier actions.
    """
    race.verify(referee_key)
    res = replay(race.game_map, race.start_state,
                 witnessed_actions, witnessed_dwells, include_start_entry=True)
    if not res.ok:
        raise VerificationError(
            f"witnessed segment blocks at {res.blocked_action!r}; "
            "cannot attest an unreachable state")
    body = {
        "kind": ARTIFACT_MIDPOINT,
        "race_id": race.race_id,
        "map_id": race.map_id,
        "start_commit": race.start_commit,
        "puzzle_commit": race.puzzle_commit,
        "witnessed_actions": list(witnessed_actions),
        "witnessed_dwells": list(witnessed_dwells),
        "covered_range": [0, res.stopped_after_step],
        "state_at_midpoint": res.final_state.to_dict(),
        "pushes": res.pushes,
        "visited": [[c[0], c[1]] for c in res.visited],
        "trace": res.trace_dicts(),
    }
    return Midpoint(referee_key.sign_body(body))


@dataclass
class Continuation:
    envelope: Dict[str, Any]

    @property
    def body(self) -> dict:
        return self.envelope["body"]

    @property
    def commit(self) -> str:
        return commit_of(self.envelope)

    def to_bytes(self) -> bytes:
        from .signing import envelope_bytes

        return envelope_bytes(self.envelope)


def continue_from(
    referee_key: Key,
    race: RaceStart,
    midpoint: Midpoint,
    tail_actions: Sequence[str],
    tail_dwells: Sequence[int],
) -> Continuation:
    """Validate a later segment against an attested midpoint.

    The tail must start exactly at the attested state/step.  Earlier actions are
    never reconstructed here — they are whatever the midpoint's witnessed run
    already covered — so a lost beginning cannot be papered over.
    """
    race.verify(referee_key)
    mid = verify_midpoint(midpoint, referee_key, race)
    start = State.from_dict(mid["state_at_midpoint"])
    if len(tail_dwells) != len(tail_actions) + 1:
        raise VerificationError("tail dwells must have len(tail_actions)+1 entries")

    res = replay(race.game_map, start, tail_actions, tail_dwells,
                 include_start_entry=False)

    body = {
        "kind": ARTIFACT_CONT,
        "race_id": race.race_id,
        "map_id": race.map_id,
        "start_commit": race.start_commit,
        "midpoint_commit": midpoint.commit,
        "resume_from_step": start.step,
        "tail_actions": list(tail_actions),
        "tail_dwells": list(tail_dwells),
        "reachable": res.ok,
        "applied_actions": res.applied_actions,
        "blocked_action": res.blocked_action,
        "stopped_after_step": res.stopped_after_step,
        "final_state": res.final_state.to_dict(),
        "pushes": res.pushes,
        "visited": [[c[0], c[1]] for c in res.visited],
        "trace": res.trace_dicts(),
    }
    return Continuation(referee_key.sign_body(body))


def verify_midpoint(midpoint: Midpoint, referee_key: Key,
                    race: RaceStart) -> dict:
    race_body = race.verify(referee_key)
    try:
        body = referee_key.verify_envelope(midpoint.envelope)
    except SignatureError as exc:
        raise VerificationError(f"midpoint not authentic: {exc}") from exc
    if body.get("kind") != ARTIFACT_MIDPOINT:
        raise VerificationError("not a midpoint attestation")
    if body["race_id"] != race_body["race_id"]:
        raise VerificationError("midpoint belongs to a different race")
    if body["start_commit"] != race.start_commit:
        raise VerificationError("midpoint chains to a different start")
    # Re-derive the witnessed segment independently.
    res = replay(race.game_map, race.start_state,
                 body["witnessed_actions"], body["witnessed_dwells"])
    if not res.ok:
        raise VerificationError("witnessed segment does not replay cleanly")
    if res.final_state.to_dict() != body["state_at_midpoint"]:
        raise VerificationError("midpoint state does not re-derive")
    if [0, res.stopped_after_step] != body["covered_range"]:
        raise VerificationError("midpoint covered_range mismatch")
    return body


def verify_continuation(cont: Continuation, referee_key: Key, race: RaceStart,
                        midpoint: Midpoint) -> dict:
    race.verify(referee_key)
    mid = verify_midpoint(midpoint, referee_key, race)
    try:
        body = referee_key.verify_envelope(cont.envelope)
    except SignatureError as exc:
        raise VerificationError(f"continuation not authentic: {exc}") from exc
    if body.get("kind") != ARTIFACT_CONT:
        raise VerificationError("not a continuation")
    if body["midpoint_commit"] != midpoint.commit:
        raise VerificationError("continuation references a different midpoint")
    if body["resume_from_step"] != mid["state_at_midpoint"]["step"]:
        raise VerificationError("continuation does not resume at the midpoint")
    start = State.from_dict(mid["state_at_midpoint"])
    res = replay(race.game_map, start, body["tail_actions"], body["tail_dwells"],
                 include_start_entry=False)
    expected = {
        "reachable": res.ok,
        "applied_actions": res.applied_actions,
        "blocked_action": res.blocked_action,
        "stopped_after_step": res.stopped_after_step,
        "final_state": res.final_state.to_dict(),
        "pushes": res.pushes,
        "visited": [[c[0], c[1]] for c in res.visited],
        "trace": res.trace_dicts(),
    }
    for field, want in expected.items():
        if body.get(field) != want:
            raise VerificationError(f"continuation field {field!r} mismatch")
    return body


# ---------------------------------------------------------------------------
# Fork detection
# ---------------------------------------------------------------------------
@dataclass
class ForkReport:
    forked: bool
    first_divergent_step: Optional[int]
    reason: str

    def to_dict(self) -> dict:
        return {
            "forked": self.forked,
            "first_divergent_step": self.first_divergent_step,
            "reason": self.reason,
        }


def detect_fork(certified_actions: Sequence[str],
                claimed_actions: Sequence[str]) -> ForkReport:
    """Compare a fresh claim against certified history for the same race.

    Any byte-level divergence in the action string, at a step that *should* be
    identical, is a fork.  A claim that is a genuine prefix (player stopped
    early) is not a fork.
    """
    n = min(len(certified_actions), len(claimed_actions))
    for i in range(n):
        if certified_actions[i] != claimed_actions[i]:
            return ForkReport(True, i + 1,
                              f"action mismatch at step {i + 1}: "
                              f"certified {certified_actions[i]!r} vs "
                              f"claimed {claimed_actions[i]!r}")
    if len(claimed_actions) > len(certified_actions):
        return ForkReport(True, len(certified_actions) + 1,
                          "claim extends beyond certified history with new actions")
    return ForkReport(False, None, "no divergence")
