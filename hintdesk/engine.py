"""The public issuance rules -- one deterministic engine for every route.

Callers
-------
* the **inner office** (route B), which knows the round's truth and answers
  live, honouring only material it can still redeem;
* the per-round **field booklet** (route A), compiled ahead of time for a
  trusted outside hint-giver who may replay branches but never invent a page;
* the independent **verifier**, which replays a receipt under each hypothesised
  truth while holding no truth of its own.

All three construct the SAME ``Engine`` over the same library and the same
material ledger, so a replay cannot drift from what was handed over.

Transcript
----------
The ordered, reader-visible record of one ticket.  For each request it keeps the
wording, the drawable-page set the menu showed (``offered``), the page(s)
actually handed over (content digest + verbatim text), the status and the
reason.  A reader sees *content*, never a page id or the answer, so two internal
routes that put the same pages in the reader's hands yield equal transcripts.

Candidate accounting
--------------------
A *candidate* is a **distinct answer** (谜底).  Aliases (同义写法) are the only
thing that collapses: they denote the same answer, so they are one world and one
candidate.  Genuinely different answers are never pooled -- if two answers can be
told apart by a question still on the menu, the pages it can draw, or how it
refuses, they stay separate.  At any state the candidates are exactly the
distinct canonical answers among the worlds still consistent with the transcript.

Promises, on that count:

* **useful** pickup -- the page actually drawn leaves strictly fewer distinct
  answers than before (eliminates >= 1 previously plausible answer);
* **safe** -- at least two distinct answers remain (the answer is never handed
  over);
* **re-issue** -- when the primary page would break a promise or its material is
  gone, a truthful substitute page is really handed over (status ``answered``);
  if none can, the request is withdrawn (``withdrawn``) with a reviewable reason.

A *final* page is only required to be safe: it is the last word and need not
eliminate anything.

Material
--------
``material`` is the set of page *contents* the desk can still honour.  A page
whose content is withheld is never drawn: its worlds fall through to a truthful
substitute, else are withdrawn with reason ``no_material``.  Withholding is part
of the round's public configuration, so the verifier replays it exactly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from .content import Library, Question, World

# -- statuses a reader can observe ----------------------------------------- #
LIVE = "live"
SPENT = "spent"
CHANGED = "answered"          # re-issued with a truthful substitute page
WITHDRAWN = "withdrawn"
REFUSED = "refused"

PICKUP = "pickup"
FINAL = "final"
DELIVERING = (SPENT, CHANGED)
BLOCKING = (SPENT, CHANGED, WITHDRAWN)

REASONS = {
    "already_used": "this question has already been used on this ticket",
    "need_two_pickups": "the end page opens only after two pickups",
    "no_more_pickups": "both pickups on this ticket are already spent",
    "over_reveal": "drawing this would leave fewer than two answers",
    "useless": "drawing this would eliminate no distinguishable answer",
    "substituted": "re-issued with a truthful substitute page",
    "no_material": "the material needed to honour this is no longer available",
}


@dataclass(frozen=True)
class Delivered:
    content: str
    text: str

    def to_json(self) -> dict:
        return {"content": self.content, "text": self.text}

    @staticmethod
    def from_json(d: Mapping) -> "Delivered":
        return Delivered(d["content"], d["text"])


@dataclass(frozen=True)
class Step:
    """One reader-visible request/response, exactly as the reader can tell it."""

    wording: str
    kind: str
    stage: int
    status: str
    offered: tuple[str, ...]
    delivered: tuple[Delivered, ...] = ()
    reason: str = ""

    def to_json(self) -> dict:
        out = {
            "wording": self.wording,
            "kind": self.kind,
            "stage": self.stage,
            "status": self.status,
            "offered": list(self.offered),
        }
        if self.delivered:
            out["delivered"] = [d.to_json() for d in self.delivered]
        if self.reason:
            out["reason"] = self.reason
        return out

    @staticmethod
    def from_json(d: Mapping) -> "Step":
        return Step(
            wording=d["wording"],
            kind=d["kind"],
            stage=int(d["stage"]),
            status=d["status"],
            offered=tuple(d.get("offered", ())),
            delivered=tuple(Delivered.from_json(x) for x in d.get("delivered", ())),
            reason=d.get("reason", ""),
        )

    @property
    def delivered_flag(self) -> bool:
        return self.status in DELIVERING

    @property
    def content(self) -> str | None:
        return self.delivered[0].content if self.delivered else None


@dataclass(frozen=True)
class Decision:
    """What one request resolves to for one world (page id stays internal)."""

    status: str
    page_id: str | None
    reason: str = ""
    useful: bool = False


def content_of(lib: Library, page_id: str) -> str:
    return lib.pages[page_id].content_digest()


def _wkey(world: World) -> tuple:
    return tuple(sorted(world.items()))


class Engine:
    def __init__(self, lib: Library, material: Iterable[str] | None = None):
        self.lib = lib
        self._worlds = lib.worlds()
        self._withheld: set[str] = set(material or ())
        self._by_text: dict[str, Question] = {}
        self._final_texts: set[str] = set()
        for q in lib.questions.values():
            if q.text in self._by_text:
                raise ValueError(f"{lib.puzzle_id}: duplicate question wording {q.text!r}")
            self._by_text[q.text] = q
        for q in lib.final_questions.values():
            if q.text in self._by_text:
                raise ValueError(f"{lib.puzzle_id}: wording {q.text!r} collides")
            self._by_text[q.text] = q
            self._final_texts.add(q.text)
        self._residual_cache: dict[tuple, tuple[World, ...]] = {}
        self._map_cache: dict[tuple, dict] = {}

    # -- material -----------------------------------------------------------
    def withhold(self, content: str) -> None:
        self._withheld.add(content)
        self._map_cache.clear()

    def restore(self, content: str) -> None:
        self._withheld.discard(content)
        self._map_cache.clear()

    def held(self, content: str | None) -> bool:
        return content is None or content not in self._withheld

    def material(self) -> frozenset:
        return frozenset(self._withheld)

    # -- basics -------------------------------------------------------------
    def worlds(self) -> list[World]:
        return list(self._worlds)

    def answer(self, world: World) -> tuple[str, ...]:
        return self.lib.answer_of(world)

    def answers_in(self, worlds: Iterable[World]) -> set[tuple[str, ...]]:
        return {self.answer(w) for w in worlds}

    def question(self, wording: str) -> Question:
        return self._by_text[wording]

    def kind_of(self, wording: str) -> str:
        return FINAL if wording in self._final_texts else PICKUP

    def question_texts(self) -> list[str]:
        return [q.text for q in self.lib.questions.values()]

    def final_texts(self) -> list[str]:
        return [q.text for q in self.lib.final_questions.values()]

    # -- state helpers ------------------------------------------------------
    def asked_blocking(self, prefix: Iterable[Step]) -> set[str]:
        return {s.wording for s in prefix if s.status in BLOCKING}

    def stage(self, prefix: Iterable[Step]) -> int:
        return sum(1 for s in prefix
                   if s.kind == PICKUP and s.status in DELIVERING)

    def _used_stage(self, prefix: Iterable[Step]) -> tuple[set[str], int]:
        prefix = list(prefix)
        return self.asked_blocking(prefix), self.stage(prefix)

    def still_askable(self, blocking: set[str], stage: int) -> list[Question]:
        out: list[Question] = []
        if stage < 2:
            out += [q for q in self.lib.questions.values() if q.text not in blocking]
        if stage >= 2:
            out += [q for q in self.lib.final_questions.values() if q.text not in blocking]
        return out

    # -- the rule -----------------------------------------------------------
    def delivery_map(self, q: Question, W: list[World], blocking: set[str],
                     stage: int) -> dict[tuple, Decision]:
        """Partition ``W`` into what each world receives for ``q``.

        Depends only on the public state (residual ``W`` and menu) plus the
        material ledger, so the desk, the booklet and the verifier compute it
        identically.  Content is unique per question across primaries and
        alternatives (validated in ``content``).
        """
        cache_key = (q.text, frozenset(_wkey(w) for w in W),
                     frozenset(blocking), stage, frozenset(self._withheld))
        cached = self._map_cache.get(cache_key)
        if cached is not None:
            return cached

        pickup = q.text not in self._final_texts
        before = len(self.answers_in(W))
        result: dict[tuple, Decision] = {}
        pc = {_wkey(w): content_of(self.lib, q.select(w)) for w in W}

        # 1. group by primary page; serve the group if it qualifies and is held
        groups: dict[str, list[World]] = {}
        for w in W:
            groups.setdefault(pc[_wkey(w)], []).append(w)

        flow: list[World] = []
        withdrawn_reason: dict[tuple, str] = {}
        for c in sorted(groups):
            ws = groups[c]
            after = len(self.answers_in(ws))
            qualifies = (after >= 2 and after < before) if pickup else (after >= 2)
            if qualifies and self.held(c):
                for w in ws:
                    result[_wkey(w)] = Decision(SPENT, q.select(w), "", pickup)
            else:
                if not self.held(c):
                    for w in ws:
                        withdrawn_reason[_wkey(w)] = "no_material"
                else:
                    reason = "over_reveal" if after < 2 else "useless"
                    for w in ws:
                        withdrawn_reason[_wkey(w)] = reason
                flow.extend(ws)

        # 2. truthful, held substitutes for worlds that cannot take their primary
        remaining = {_wkey(w): w for w in flow}
        for alt in q.alternatives:
            page = self.lib.pages[alt]
            c2 = content_of(self.lib, alt)
            if not self.held(c2):
                continue
            cand = [w for w in remaining.values()
                    if self.lib.give_true_for(page.give, w)]
            if not cand:
                continue
            after = len(self.answers_in(cand))
            ok = (after >= 2 and after < before) if pickup else (after >= 2)
            if ok:
                for w in cand:
                    result[_wkey(w)] = Decision(CHANGED, alt, "substituted", pickup)
                    remaining.pop(_wkey(w), None)
                    withdrawn_reason.pop(_wkey(w), None)

        # 3. the rest is withdrawn with the most specific reviewable reason
        for w in remaining.values():
            c = pc[_wkey(w)]
            reason = withdrawn_reason.get(_wkey(w))
            if reason is None:
                reason = "over_reveal" if len(self.answers_in(groups[c])) < 2 else "useless"
            result[_wkey(w)] = Decision(WITHDRAWN, None, reason)

        self._map_cache[cache_key] = result
        return result

    def offered(self, q: Question, W: list[World], blocking: set[str],
                stage: int) -> tuple[str, ...]:
        dm = self.delivery_map(q, W, blocking, stage)
        return tuple(sorted({
            content_of(self.lib, d.page_id)
            for d in dm.values() if d.page_id is not None
        }))

    # -- guards -------------------------------------------------------------
    def _guard(self, q: Question, blocking: set[str], stage: int) -> str | None:
        if q.text in blocking:
            return "already_used"
        if q.text in self._final_texts and stage < 2:
            return "need_two_pickups"
        if q.text not in self._final_texts and stage >= 2:
            return "no_more_pickups"
        return None

    # -- resolution ---------------------------------------------------------
    def decide(self, world: World, prefix: Iterable[Step], wording: str) -> Step:
        q = self._by_text.get(wording)
        if q is None:
            raise KeyError(f"unknown question wording {wording!r}")
        return self._step(world, tuple(prefix), q)

    def resolve(self, world: World, prefix: Iterable[Step],
                wording: str) -> tuple[Step, Decision]:
        q = self._by_text.get(wording)
        if q is None:
            raise KeyError(f"unknown question wording {wording!r}")
        prefix = tuple(prefix)
        return self._step(world, prefix, q), self._decision(world, prefix, q)

    def _step(self, world: World, prefix: tuple[Step, ...], q: Question) -> Step:
        kind = self.kind_of(q.text)
        blocking, stage = self._used_stage(prefix)
        guard = self._guard(q, blocking, stage)
        if guard is not None:
            return Step(q.text, kind, stage, REFUSED, (), (), guard)
        W = self.residual(prefix)
        offered = self.offered(q, W, blocking, stage)
        d = self.delivery_map(q, W, blocking, stage)[_wkey(world)]
        delivered = ()
        if d.page_id is not None:
            page = self.lib.pages[d.page_id]
            delivered = (Delivered(content_of(self.lib, d.page_id), page.text),)
        return Step(q.text, kind, stage, d.status, offered, delivered, d.reason)

    def _decision(self, world: World, prefix: tuple[Step, ...], q: Question) -> Decision:
        blocking, stage = self._used_stage(prefix)
        guard = self._guard(q, blocking, stage)
        if guard is not None:
            return Decision(REFUSED, None, guard)
        W = self.residual(prefix)
        return self.delivery_map(q, W, blocking, stage)[_wkey(world)]

    # -- residual by exact replay -----------------------------------------
    def residual(self, prefix: Iterable[Step]) -> tuple[World, ...]:
        prefix = tuple(prefix)
        cached = self._residual_cache.get(prefix)
        if cached is not None:
            return cached
        if not prefix:
            res = tuple(self._worlds)
        else:
            prev = self.residual(prefix[:-1])
            step = prefix[-1]
            q = self._by_text.get(step.wording)
            if q is None:
                res = ()
            else:
                res = tuple(w for w in prev if self._step(w, prefix[:-1], q) == step)
        self._residual_cache[prefix] = res
        return res

    def consistent(self, transcript: Iterable[Step]) -> tuple[World, ...]:
        return self.residual(transcript)

    def candidate_answers(self, transcript: Iterable[Step]) -> set[tuple[str, ...]]:
        return self.answers_in(self.residual(transcript))

    def classes(self, transcript: Iterable[Step]) -> int:
        """Number of behaviourally distinguishable classes among survivors.

        Two survivors are one class iff they share the canonical answer AND would
        draw identical content for every still-askable question.  This is the
        reader's own notion of "can't tell them apart"; it is *not* used to pool
        distinct answers -- two different answers are always different classes
        because the tuple starts with the answer.
        """
        prefix = tuple(transcript)
        blocking, stage = self._used_stage(prefix)
        still = self.still_askable(blocking, stage)
        return len({
            (self.answer(w),
             tuple(content_of(self.lib, q.select(w)) for q in still))
            for w in self.residual(prefix)
        })

    # -- menu view ----------------------------------------------------------
    def menu(self, prefix: Iterable[Step]) -> list[dict]:
        prefix = tuple(prefix)
        blocking, stage = self._used_stage(prefix)
        W = self.residual(prefix)
        rows = []
        for q in self.still_askable(blocking, stage):
            dm = self.delivery_map(q, W, blocking, stage)
            draws = sorted({content_of(self.lib, d.page_id)
                            for d in dm.values() if d.page_id is not None})
            withdrawn = [d for d in dm.values() if d.status == WITHDRAWN]
            status = LIVE if draws else WITHDRAWN
            reason = "" if draws else (withdrawn[0].reason if withdrawn else "useless")
            rows.append({
                "text": q.text,
                "kind": self.kind_of(q.text),
                "status": status,
                "offered": draws,
                "reason": reason,
            })
        return rows

    def status_board(self, prefix: Iterable[Step]) -> list[dict]:
        """Everything the reader can see: used (with outcome) plus available."""
        prefix = tuple(prefix)
        board = []
        for s in prefix:
            board.append({"text": s.wording, "status": s.status,
                          "reason": s.reason, "stage": s.stage,
                          "content": s.content})
        for row in self.menu(prefix):
            board.append({"text": row["text"], "status": row["status"],
                          "reason": row["reason"], "stage": None,
                          "content": None})
        return board

    # -- review -------------------------------------------------------------
    def delivery_table(self, q: Question, prefix: Iterable[Step]) -> dict:
        prefix = tuple(prefix)
        blocking, stage = self._used_stage(prefix)
        W = self.residual(prefix)
        dm = self.delivery_map(q, W, blocking, stage)
        rows = {}
        for w in W:
            d = dm[_wkey(w)]
            rows[self.answer(w)] = {
                "status": d.status,
                "content": content_of(self.lib, d.page_id) if d.page_id else None,
                "reason": d.reason,
            }
        return rows
