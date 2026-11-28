"""Independent replay verifier.

The verifier holds **no truth of its own** -- only the public library, the
round's public material ledger, and a receipt.  It replays the receipt under
each library world as a *hypothesis* and keeps a world only if it reproduces the
entire observed record: every step's wording, offered set, delivered
content/text, status and reason.

What it reports
---------------
``survivors``
    Distinct canonical answers whose worlds reproduce the whole record.  Aliases
    collapse (same answer); genuinely different answers stay separate.

``candidates``
    The survivors merged the way a reader can *still* distinguish them: two
    survivors are one candidate iff they share the answer and would draw the
    same content for every question still on the menu.  Two different answers
    are never merged.  This is the count a curator should compare against the
    promised "two useful pickups".

``replay_many``
    For several receipts, groups them by ``Receipt.record_signature`` (what the
    reader can tell apart, independent of *which* question produced it) and
    checks that receipts sharing a signature also share a survivor set -- the
    "different question paths, same record in the reader's hands" case.

The verifier never trusts the emitted labels as ground truth: it recomputes
each step from the hypothesis with the shared ``Engine`` and requires an exact
match, so a receipt that merely *labels* itself consistent is rejected.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .engine import Engine, Step, content_of
from .receipts import Receipt


@dataclass(frozen=True)
class ReplayReport:
    receipt: Receipt
    survivors: tuple[tuple[str, ...], ...]
    survivor_worlds: tuple[dict, ...]
    mismatched_worlds: tuple[tuple[dict, str], ...]
    per_hypothesis: tuple[tuple[tuple[str, ...], bool, str], ...]

    @property
    def determinate(self) -> bool:
        return len(self.survivors) == 1

    @property
    def count(self) -> int:
        return len(self.survivors)

    def summary(self) -> dict:
        return {
            "ticket": self.receipt.ticket,
            "stage": self.receipt.stage,
            "survivors": [list(a) for a in self.survivors],
            "count": self.count,
            "mismatches": [{"world": w, "why": why}
                           for w, why in self.mismatched_worlds],
        }


class Verifier:
    def __init__(self, lib, *, material: Iterable[str] | None = None):
        self.lib = lib
        self.engine = Engine(lib, material=material)

    # -- replay -------------------------------------------------------------
    def replay(self, receipt: Receipt) -> ReplayReport:
        if receipt.library != self.lib.digest:
            raise ValueError("receipt belongs to a different library")
        survivors: list[tuple[str, ...]] = []
        survivor_worlds: list[dict] = []
        mismatched: list[tuple[dict, str]] = []
        per: list[tuple[tuple[str, ...], bool, str]] = []

        for world in self.engine.worlds():
            ok, why = self._reproduces(world, receipt)
            answer = self.engine.answer(world)
            per.append((answer, ok, why))
            if ok:
                if answer not in survivors:
                    survivors.append(answer)
                survivor_worlds.append(world)
            else:
                mismatched.append((world, why))

        survivors.sort()
        return ReplayReport(
            receipt=receipt,
            survivors=tuple(survivors),
            survivor_worlds=tuple(survivor_worlds),
            mismatched_worlds=tuple(mismatched),
            per_hypothesis=tuple(per),
        )

    def _reproduces(self, world: dict, receipt: Receipt) -> tuple[bool, str]:
        prefix: tuple[Step, ...] = ()
        for i, observed in enumerate(receipt.transcript):
            q = self.engine._by_text.get(observed.wording)
            if q is None:
                return False, f"step {i}: unknown wording {observed.wording!r}"
            emitted = self.engine._step(world, prefix, q)
            if emitted != observed:
                return False, self._explain(i, emitted, observed)
            prefix = prefix + (emitted,)
        return True, ""

    @staticmethod
    def _explain(i: int, emitted: Step, observed: Step) -> str:
        if emitted.status != observed.status:
            return f"step {i}: status {emitted.status!r} != {observed.status!r}"
        if emitted.offered != observed.offered:
            return f"step {i}: offered set differs"
        if tuple(d.content for d in emitted.delivered) != \
                tuple(d.content for d in observed.delivered):
            return f"step {i}: delivered content differs"
        if emitted.reason != observed.reason:
            return f"step {i}: reason {emitted.reason!r} != {observed.reason!r}"
        return f"step {i}: exact mismatch"

    # -- candidate view -----------------------------------------------------
    def candidates(self, receipt: Receipt) -> list[tuple[str, ...]]:
        rep = self.replay(receipt)
        used = {s.wording for s in receipt.transcript if s.status in (
            "spent", "answered", "withdrawn")}
        stage = receipt.stage
        still = self.engine.still_askable(used, stage)
        seen: dict[tuple, tuple[str, ...]] = {}
        for w in rep.survivor_worlds:
            sig = tuple(content_of(self.lib, q.select(w)) for q in still)
            key = (self.engine.answer(w), sig)
            seen[key] = self.engine.answer(w)
        return sorted(set(seen.values()))

    # -- cross-path agreement ----------------------------------------------
    def replay_many(self, receipts: Iterable[Receipt]) -> dict:
        by_sig: dict[tuple, set] = {}
        details: dict[tuple, list] = {}
        for r in receipts:
            rep = self.replay(r)
            sig = r.record_signature()
            by_sig.setdefault(sig, set()).update(
                frozenset(a) for a in rep.survivors)
            details.setdefault(sig, []).append((r.ticket, rep.survivors))
        violations = {sig: det for sig, det in details.items()
                      if len({frozenset(a) for _, a in det}) > 1}
        return {
            "by_signature": {sig: [sorted(list(x)) for x in surv]
                             for sig, surv in by_sig.items()},
            "violations": violations,
        }
