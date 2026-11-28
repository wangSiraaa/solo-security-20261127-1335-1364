"""Intake and run orchestration for the curator.

Two jobs:

1. **Intake a new library.**  ``Curator.admit`` validates the schema and reports
   whether the library *can* fulfil the two-useful-pickups promise at all; a
   library that cannot is admitted only with a reviewable refusal reason, never
   silently misrepresented.

2. **Run a round** (per route).  ``run_round`` drives a reader through two
   pickups on either route and returns a ``RoundOutcome`` recording, per pickup,
   whether it was *useful* (eliminated >= 1 previously plausible answer) and
   *safe* (>= 2 answers survive) -- and, if the round cannot complete, a
   reviewable reason.

The curator never certifies usefulness or safety from a label: it recomputes
both with the ``Engine`` and cross-checks with the independent ``Verifier`` at
issue time, so a "useful" claim is always backed by a strictly smaller plausible
set.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable

from .booklet import Booklet, CannotFulfil
from .content import Library
from .desk import InnerOffice
from .engine import PICKUP, Engine
from .receipts import Receipt
from .station import BookletRouteAdapter, OfficeRouteAdapter, Station
from .verify import Verifier


@dataclass
class PickupCheck:
    wording: str
    status: str
    useful: bool
    safe: bool
    eliminated: list[tuple[str, ...]]
    survivors: list[tuple[str, ...]]
    note: str = ""


@dataclass
class RoundOutcome:
    route: str
    ticket: str
    receipts: list[Receipt]
    pickups: list[PickupCheck]
    ok: bool
    reason: str = ""
    detail: str = ""

    def summary(self) -> dict:
        return {
            "route": self.route,
            "ticket": self.ticket,
            "ok": self.ok,
            "reason": self.reason,
            "detail": self.detail,
            "pickups": [
                {"wording": p.wording, "status": p.status, "useful": p.useful,
                 "safe": p.safe,
                 "eliminated": [list(a) for a in p.eliminated],
                 "survivors": [list(a) for a in p.survivors], "note": p.note}
                for p in self.pickups
            ],
        }


@dataclass
class FulfilmentReport:
    puzzle_id: str
    admissible: bool
    two_useful: bool
    reason: str = ""
    detail: str = ""
    witness: list[dict] = field(default_factory=list)

    def summary(self) -> dict:
        return {"puzzle_id": self.puzzle_id, "admissible": self.admissible,
                "two_useful": self.two_useful, "reason": self.reason,
                "detail": self.detail, "witness": self.witness}


class Curator:
    def __init__(self, lib: Library, *, material: Iterable[str] | None = None):
        self.lib = lib
        self.material = material
        self.engine = Engine(lib, material=material)
        self.verifier = Verifier(lib, material=material)

    # -- intake -------------------------------------------------------------
    def admit(self) -> FulfilmentReport:
        """Can this library back two useful, safe pickups somewhere?"""
        base_rep = Receipt.build(ticket="witness", library=self.lib.digest,
                                 puzzle_id=self.lib.puzzle_id, transcript=())
        base = self.engine.candidate_answers(base_rep.transcript)
        if len(base) < 3:
            return FulfilmentReport(
                puzzle_id=self.lib.puzzle_id, admissible=True, two_useful=False,
                reason="too_few_answers",
                detail=f"only {len(base)} distinct answers; two useful pickups "
                       "need at least three to keep two after two eliminations",
            )
        for first in self.engine.question_texts():
            step1 = self.engine.decide(self.lib.truth, base_rep.transcript, first)
            if step1.status != "spent":
                continue
            rep1 = base_rep.extend(step1)
            survivors1 = self.engine.candidate_answers(rep1.transcript)
            if not (len(survivors1) < len(base) and len(survivors1) >= 2):
                continue
            for second in self.engine.question_texts():
                if second == first:
                    continue
                step2 = self.engine.decide(self.lib.truth, rep1.transcript, second)
                if step2.status != "spent":
                    continue
                rep2 = rep1.extend(step2)
                survivors2 = self.engine.candidate_answers(rep2.transcript)
                if len(survivors2) < len(survivors1) and len(survivors2) >= 2:
                    return FulfilmentReport(
                        puzzle_id=self.lib.puzzle_id,
                        admissible=True, two_useful=True,
                        witness=[step1.to_json(), step2.to_json()],
                    )
        return FulfilmentReport(
            puzzle_id=self.lib.puzzle_id, admissible=True, two_useful=False,
            reason="no_two_useful_pickups",
            detail=("no pair of drawable pickups both eliminates a plausible "
                    "answer and leaves two answers under this library/material"),
        )

    # -- running a round ----------------------------------------------------
    def _search_pair(self, truth) -> list[str] | None:
        """Find an ordered pair of pickups that are each useful and safe under
        ``truth``, so the round provably completes.  Returns the wordings."""
        base = Receipt.build(ticket="s", library=self.lib.digest,
                             puzzle_id=self.lib.puzzle_id, transcript=())
        start = base.transcript
        before = self.engine.candidate_answers(start)
        for first in self.engine.question_texts():
            s1 = self.engine.decide(truth, start, first)
            if not s1.delivered_flag:
                continue
            after1 = self.engine.candidate_answers(start + (s1,))
            if not (len(after1) < len(before) and len(after1) >= 2):
                continue
            for second in self.engine.question_texts():
                if second == first:
                    continue
                s2 = self.engine.decide(truth, start + (s1,), second)
                if not s2.delivered_flag:
                    continue
                after2 = self.engine.candidate_answers(start + (s1, s2))
                if len(after2) < len(after1) and len(after2) >= 2:
                    return [first, second]
        return None

    def run_round(self, route: str, *,
                  choose: Callable[[Receipt, list[dict]], str] | None = None,
                  truth=None) -> RoundOutcome:
        """Drive two useful pickups on ``route`` ('booklet' or 'office').

        ``choose`` is the reader's policy: given the receipt and the live menu,
        pick the next wording.  When omitted, the curator searches for a pair of
        useful, safe pickups and replays it, so any round it admits completes.
        Returns a fully-checked ``RoundOutcome``.
        """
        truth = truth if truth is not None else self.lib.truth
        plan: list[str] | None = None
        if choose is None:
            plan = self._search_pair(truth)
            if plan is None:
                return RoundOutcome(route, f"{route}-ticket", [], [], False,
                                    reason="no_two_useful_pickups",
                                    detail="no ordered pair of useful, safe "
                                           "pickups exists under this truth")
            it = iter(plan)
            choose = lambda r, m: next(it)   # noqa: E731
        station, route_tag = self._make_station(route, truth)
        ticket = f"{route}-ticket"
        receipt = station.open(ticket)
        receipts = [receipt]
        pickups: list[PickupCheck] = []
        prev = self.engine.candidate_answers(receipt.transcript)

        while len(pickups) < 2:
            menu = [m for m in station.menu(receipt) if m["kind"] == PICKUP
                    and m["status"] == "live"]
            if not menu:
                return RoundOutcome(route_tag, ticket, receipts, pickups, False,
                                    reason="menu_exhausted",
                                    detail="no drawable pickup remains before two")
            try:
                wording = choose(receipt, menu)
            except CannotFulfil as exc:
                return RoundOutcome(route_tag, ticket, receipts, pickups, False,
                                    exc.reason, exc.detail)
            if wording not in {m["text"] for m in menu}:
                return RoundOutcome(route_tag, ticket, receipts, pickups, False,
                                    "not_on_menu", f"{wording!r} is not drawable")
            try:
                receipt, step, note = station.ask(receipt, wording)
            except CannotFulfil as exc:
                return RoundOutcome(route_tag, ticket, receipts, pickups, False,
                                    exc.reason, exc.detail)
            receipts.append(receipt)
            if step is None:
                return RoundOutcome(route_tag, ticket, receipts, pickups, False,
                                    "pickup_refused",
                                    f"{wording!r} delivered nothing ({note})")
            after = self.engine.candidate_answers(receipt.transcript)
            eliminated = sorted(prev - after)
            useful = len(after) < len(prev)
            safe = len(after) >= 2
            rep = self.verifier.replay(receipt)
            if set(rep.survivors) != after:
                return RoundOutcome(route_tag, ticket, receipts, pickups, False,
                                    "replay_disagreement",
                                    "engine and verifier disagree on survivors")
            pickups.append(PickupCheck(wording, step.status, useful, safe,
                                       eliminated, sorted(after)))
            if not useful or not safe:
                return RoundOutcome(
                    route_tag, ticket, receipts, pickups, False,
                    "pickup_not_useful" if not useful else "pickup_over_reveal",
                    f"{wording!r}: useful={useful} safe={safe}")
            prev = after
        return RoundOutcome(route_tag, ticket, receipts, pickups, True)

    def _make_station(self, route: str, truth) -> tuple[Station, str]:
        if route == "booklet":
            booklet = Booklet.compile(self.lib, material=self.material)
            adapter = BookletRouteAdapter(booklet, truth,
                                          puzzle_id=self.lib.puzzle_id)
            return Station(adapter), "booklet"
        if route == "office":
            office = InnerOffice(self.lib, withheld=self.material or ())
            return Station(OfficeRouteAdapter(office)), "office"
        raise ValueError(f"unknown route {route!r}")
