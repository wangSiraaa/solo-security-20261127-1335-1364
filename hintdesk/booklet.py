"""Route A -- a precompiled field booklet for a trusted outside hint-giver.

The booklet is compiled ahead of time from a library (and the round's material
ledger).  At read time the hint-giver holds the booklet, knows *this round's
truth*, and has the reader's receipt -- but has **no question bank** and may
never invent a page.  It follows only compiled branches; if a receipt reaches a
state the booklet was not compiled for (a different/newer library, or a truth
the receipt cannot admit) it raises ``CannotFulfil`` with a reviewable reason.

The booklet is a plain data artifact.  It keeps, per reachable public state and
question, the menu (with offered sets) and -- keyed by the round's truth -- the
exact step to hand over, carrying the page's *verbatim text*.  It keeps no
``Engine`` or library at read time.

Because those branches are memoised straight from ``Engine``, a booklet delivers
byte-identical steps to the inner office, so a ticket continues between stations
from the receipt alone.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Iterable, Mapping

from .engine import PICKUP, Engine, Step
from .receipts import Receipt


class CannotFulfil(Exception):
    """The route cannot serve this request honestly."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(reason if not detail else f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


def state_key(transcript: Iterable[Step]) -> tuple:
    """Freeze the transcript into a public-state key.

    Refused steps are dropped: a refusal changes neither the consistent worlds
    nor the stage nor which questions are blocked (the many ways a reader can
    *see* a refusal), so it must not hide a state the booklet has compiled.  The
    remaining steps still carry everything a branch depends on.
    """
    return tuple(
        _freeze(s.to_json()) for s in transcript if s.status != "refused"
    )


def _freeze(obj):
    if isinstance(obj, dict):
        return tuple(sorted((k, _freeze(v)) for k, v in obj.items()))
    if isinstance(obj, list):
        return tuple(_freeze(v) for v in obj)
    return obj


def _wkey(world) -> tuple:
    return tuple(sorted(world.items()))


@dataclass(frozen=True)
class Branch:
    status: str
    content: str | None
    text: str | None
    reason: str


class Booklet:
    """Immutable precompiled branches; safe to ship to an outside hint-giver."""

    def __init__(self, lib_digest: str, puzzle_id: str,
                 menus: Mapping[tuple, list],
                 branches: Mapping[tuple, Branch],
                 offered: Mapping[tuple, tuple],
                 states: frozenset,
                 finals: frozenset,
                 material: frozenset,
                 known_wordings: frozenset = frozenset()):
        self.library = lib_digest
        self.puzzle_id = puzzle_id
        self._menus = menus
        self._branches = branches
        self._offered = offered
        self._states = states
        self._finals = finals
        self.material = material
        self._known = known_wordings

    # -- compilation --------------------------------------------------------
    @staticmethod
    def compile(lib, *, material: Iterable[str] | None = None) -> "Booklet":
        engine = Engine(lib, material=material)
        menus: dict = {}
        branches: dict = {}
        offered: dict = {}
        states: set = set()

        start: tuple[Step, ...] = ()
        seen = {state_key(start)}
        queue: deque = deque([start])
        while queue:
            prefix = queue.popleft()
            sk = state_key(prefix)
            states.add(sk)
            W = engine.residual(prefix)
            if not W:
                continue
            blocking, stage = engine._used_stage(prefix)
            still = engine.still_askable(blocking, stage)
            menus[sk] = [
                {"text": row["text"], "kind": row["kind"],
                 "status": row["status"], "offered": row["offered"],
                 "reason": row["reason"]}
                for row in engine.menu(prefix)
            ]
            for q in still:
                off = engine.offered(q, list(W), blocking, stage)
                offered[(sk, q.text)] = off
                next_steps: dict = {}
                for w in W:
                    step = engine._step(w, prefix, q)
                    text = step.delivered[0].text if step.delivered else None
                    branches[(sk, q.text, _wkey(w))] = Branch(
                        step.status, step.content, text, step.reason)
                    next_steps.setdefault(state_key(prefix + (step,)), step)
                for nsk, step in next_steps.items():
                    if nsk not in seen:
                        seen.add(nsk)
                        queue.append(prefix + (step,))
        return Booklet(lib.digest, lib.puzzle_id, menus, branches, offered,
                       frozenset(states), frozenset(engine._final_texts),
                       frozenset(engine.material()),
                       frozenset(engine._by_text))

    # -- read-time ----------------------------------------------------------
    def check(self, receipt: Receipt) -> None:
        if receipt.library != self.library:
            raise CannotFulfil("wrong_library",
                               f"receipt is for {receipt.library!r}, "
                               f"booklet is {self.library!r}")

    def _skey(self, receipt: Receipt) -> tuple:
        sk = state_key(receipt.transcript)
        if sk not in self._states:
            raise CannotFulfil("state_not_compiled",
                               "receipt state is outside this booklet")
        return sk

    def menu(self, receipt: Receipt) -> list:
        self.check(receipt)
        return [dict(row) for row in self._menus.get(self._skey(receipt), [])]

    def serve(self, receipt: Receipt, wording: str, truth) -> Step:
        """Hand over exactly the compiled page for (state, wording, truth).

        The booklet refuses fixtures the office would refuse (unknown wording,
        re-use, ordering) using only the receipt, so the two routes agree even
        for requests that are not on the live menu.
        """
        self.check(receipt)
        sk = self._skey(receipt)
        if wording not in self._known:
            raise KeyError(f"unknown question wording {wording!r}")
        stage = sum(1 for s in receipt.transcript
                    if s.kind == PICKUP and s.delivered_flag)
        kind = "final" if wording in self._finals else PICKUP
        blocking = {s.wording for s in receipt.transcript
                    if s.status in ("spent", "answered", "withdrawn")}

        guard = None
        if wording in blocking:
            guard = "already_used"
        elif kind == "final" and stage < 2:
            guard = "need_two_pickups"
        elif kind == PICKUP and stage >= 2:
            guard = "no_more_pickups"
        if guard is not None:
            # match the engine: a refusal carries an empty offered set
            return Step(wording, kind, stage, "refused", (), (), guard)

        branch = self._branches.get((sk, wording, _wkey(truth)))
        if branch is None:
            raise CannotFulfil(
                "truth_not_at_state",
                "no compiled branch for this truth; the receipt cannot admit it",
            )
        from .engine import Delivered
        delivered = ()
        if branch.content is not None:
            delivered = (Delivered(branch.content, branch.text),)
        return Step(wording, kind, stage, branch.status,
                    self._offered_for(sk, wording), delivered, branch.reason)

    def _offered_for(self, sk: tuple, wording: str) -> tuple:
        return self._offered.get((sk, wording), ())

    # -- introspection ------------------------------------------------------
    def compiled_states(self) -> int:
        return len(self._states)

    def describe(self, receipt: Receipt, wording: str) -> dict:
        self.check(receipt)
        sk = self._skey(receipt)
        truths = sorted(
            d for (s, w, d), br in self._branches.items()
            if s == sk and w == wording and br.content is not None
        )
        return {"wording": wording,
                "offered": list(self._offered.get((sk, wording), ())),
                "drawing_truths": [dict(t) for t in truths]}
