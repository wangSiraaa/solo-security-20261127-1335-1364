"""Scenario planner for the "one free cell, two recipients" decision.

The exercise::

    仅剩一个空的安全格。甲有两张均须本人收取的附件，第一张已在自己的格，
    须收齐才肯同时领走；乙有一张需要立即领走的密件，乙马上到、甲或早或晚。
    设备每次只能把一张已查明纸面的新纸送进最后一格。

Modelled facts:

* Exactly **one** cell is free *right now*.  A cell frees again the instant its
  paper is handed over, so the planner reasons about a *sequence*: place one
  face-confirmed sheet -> hand it over -> the cell frees -> place the next.
  One placement per beat.
* 甲 wants both attachments and will only take them together; page 1 is already
  in 甲's own cell, page 2 is not printed yet.  While page 2 sits in the last
  free cell and 甲 is late, 乙 has nowhere to go -> 乙 may leave empty-handed.
* 乙 has one secret sheet wanted *immediately* and 乙 comes right away.  If 乙
  is served first but then reschedules, 乙's sheet holds the last cell, so 甲's
  set still cannot complete -> 甲 waits.
* The planner simulates both priority orderings from the *same* starting state,
  reports each one's downside, and recommends one.  It never signs one sheet or
  one cell to two people: that is enforced by :class:`ReleaseService`.

Each party has an *arrival* (when they actually show up) and a *presence
window* ``[arrival, leaves_at]``.  A party not served before ``leaves_at``
leaves empty-handed.  "临时到场变化" is modelled by perturbing ``arrival``.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence

from .errors import NoFreeCell, SecurityError
from .service import ReleaseService


@dataclass
class Party:
    """A recipient with an arrival range and a collection rule."""

    name: str
    job_id: str
    comes_from: float  # earliest arrival
    comes_to: float  # latest arrival
    window: float = 1.0  # how long they wait after arriving (presence length)
    must_collect_all: bool = False  # refuses a partial set (甲)

    def leaves_at(self, arrival: float) -> float:
        return arrival + self.window

    def may_be_late(self, arrival: float) -> bool:
        return self.comes_to > arrival


@dataclass
class PlanStep:
    action: str  # "place" | "release" | "advance" | "blocked" | "empty"
    party: Optional[str]
    serial: Optional[str] = None
    page: Optional[int] = None
    cell_id: Optional[str] = None
    at: Optional[float] = None
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "party": self.party,
            "serial": self.serial,
            "page": self.page,
            "cell_id": self.cell_id,
            "at": self.at,
            "note": self.note,
        }


@dataclass
class Candidate:
    name: str
    order: List[str]
    steps: List[PlanStep] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    completes: List[str] = field(default_factory=list)
    empty_handed: List[str] = field(default_factory=list)
    blocked: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "order": list(self.order),
            "completes": list(self.completes),
            "empty_handed": list(self.empty_handed),
            "blocked": list(self.blocked),
            "risks": list(self.risks),
            "steps": [s.to_dict() for s in self.steps],
        }


class Planner:
    def __init__(
        self,
        service: ReleaseService,
        parties: Sequence[Party],
        serial_seq: Optional[Iterable[str]] = None,
    ):
        self.svc = service
        self.parties = {p.name: p for p in parties}
        self._serial_pool = list(serial_seq) if serial_seq is not None else None
        self._serial_n = 0

    # -- helpers -----------------------------------------------------------
    def _next_serial(self) -> str:
        if self._serial_pool is not None:
            try:
                return self._serial_pool.pop(0)
            except IndexError:
                pass
        self._serial_n += 1
        return f"R{self._serial_n:03d}"

    def _next_missing_page(self, party: str) -> Optional[int]:
        missing = self.svc.missing_pages(party)
        if missing:
            return missing[0]
        not_in_cell = self.svc.pages_not_in_cell(party)
        if not_in_cell:
            return not_in_cell[0]
        return None

    def _place_next(self, party: str, at: float) -> PlanStep:
        page = self._next_missing_page(party)
        if page is None:
            raise SecurityError(f"{party} has no outstanding page to place")
        cells = self.svc.free_cells_for(party)
        if not cells:
            raise NoFreeCell("no usable free cell available")
        cell = cells[0]
        serial = self._next_serial()
        p = self.parties[party]
        self.svc.place_confirmed_sheet(serial, p.job_id, page, party, cell)
        return PlanStep("place", party, serial=serial, page=page, cell_id=cell, at=at)

    def _try_release(self, party: str, at: float) -> Optional[PlanStep]:
        p = self.parties[party]
        if not self.svc.can_release(party):
            return None
        if p.must_collect_all and (
            self.svc.missing_pages(party) or self.svc.pages_not_in_cell(party)
        ):
            return None
        slip = self.svc.sign_claim(party)
        outcome = self.svc.deliver(slip.ticket_id, present=True)
        return PlanStep(
            "release",
            party,
            serial=",".join(outcome.sheets),
            cell_id=",".join(outcome.cell_ids),
            at=at,
            note=f"ticket {outcome.ticket_id}",
        )

    def _needs_work(self, party: str) -> bool:
        return self._next_missing_page(party) is not None

    # -- simulation --------------------------------------------------------
    def simulate(
        self,
        order: Sequence[str],
        *,
        arrival: Dict[str, float],
        name: str,
    ) -> Candidate:
        """Simulate one priority ordering on the *current* service state.

        ``order`` is the placement priority (who gets the scarce cell first).
        Choices modelled explicitly:

        * the device may *print ahead* for an absent party (that is what "先补甲"
          means): placing a sheet does not require the party to be present;
        * a hand-over requires the party to be present inside
          ``[arrival[p], leaves_at]`` and the whole set verified & in cells;
        * a cell frees the moment its paper is handed over.

        Mutates the service; snapshot/restore around it.
        """
        cand = Candidate(name=name, order=list(order))
        t = 0.0
        completed: set = set()
        empty: set = set()
        blocked_logged: set = set()
        MAX_BEATS = 500
        for _ in range(MAX_BEATS):
            progressed = False
            # 1) hand over to every present party whose set is ready
            for party in order:
                if party in completed or party in empty:
                    continue
                if self._present(party, arrival, t):
                    rel = self._try_release(party, t)
                    if rel is not None:
                        cand.steps.append(rel)
                        completed.add(party)
                        progressed = True
            if progressed:
                continue
            # 2) one placement per beat, in priority order (print-ahead allowed,
            #    but never for a party whose window has already closed)
            placed = None
            for party in order:
                if party in completed or party in empty:
                    continue
                if t > self.parties[party].leaves_at(arrival[party]) + 1e-9:
                    continue
                if self._needs_work(party) and self.svc.free_cells_for(party):
                    placed = self._place_next(party, t)
                    cand.steps.append(placed)
                    break
            if placed is not None:
                continue
            # 3) no progress: close out parties whose window has passed, and
            #    note present parties blocked by a full box.
            for party in order:
                if party in completed or party in empty:
                    continue
                leave = self.parties[party].leaves_at(arrival[party])
                if t > leave + 1e-9:
                    empty.add(party)
                    cand.steps.append(
                        PlanStep("empty", party, at=t, note="未在到场窗口内领取")
                    )
            for party in order:
                if party in completed or party in empty:
                    continue
                if (
                    self._present(party, arrival, t)
                    and self._needs_work(party)
                    and not self.svc.free_cells_for(party)
                ):
                    key = (party, round(t, 6))
                    if key not in blocked_logged:
                        blocked_logged.add(key)
                        msg = (
                            f"{party} 在 t={t:g} 需要放纸但无空格"
                            f"（被 {self._cell_holder()} 占用）"
                        )
                        cand.blocked.append(msg)
                        cand.steps.append(PlanStep("blocked", party, at=t, note=msg))
            # 4) advance time to the next arrival / departure
            future = self._next_event_times(order, arrival, t)
            if not future:
                break
            t = min(future)
            cand.steps.append(PlanStep("advance", None, at=t))

        for party in order:
            if party not in completed:
                empty.add(party)
        cand.completes = [p for p in order if p in completed]
        cand.empty_handed = [p for p in order if p in empty]
        cand.risks.extend(self._late_risks(order, arrival))
        return cand

    def _present(self, party: str, arrival: Dict[str, float], t: float) -> bool:
        a = arrival[party]
        return a - 1e-9 <= t <= self.parties[party].leaves_at(a) + 1e-9

    def _next_event_times(
        self, order: Sequence[str], arrival: Dict[str, float], t: float
    ) -> List[float]:
        times: List[float] = []
        for party in order:
            a = arrival[party]
            leave = self.parties[party].leaves_at(a)
            if a > t + 1e-9:
                times.append(a)
            if leave > t + 1e-9:
                times.append(leave)
        return times

    def _cell_holder(self) -> str:
        for c in self.svc.cells():
            if c["sheet_serial"]:
                return f"{c['cell_id']}:{c['sheet_serial']}"
        return "none"

    # -- decision ----------------------------------------------------------
    def choose(self, arrival: Dict[str, float]) -> dict:
        snap = self.svc._snapshot()
        results: List[Candidate] = []
        self.svc.suspend_persistence()
        try:
            for order in (("甲", "乙"), ("乙", "甲")):
                self.svc._restore_snapshot(copy.deepcopy(snap))
                self._reset_serials()
                cand = self.simulate(order, arrival=arrival, name="->".join(order))
                results.append(cand)
        finally:
            self.svc._restore_snapshot(snap)
            self.svc.resume_persistence()

        def score(c: Candidate):
            # Complete as many sets as possible; then honour the urgent 乙
            # pickup; then fewer empty-handed; then fewer risks.
            return (
                len(c.completes),
                1 if "乙" in c.completes else 0,
                -len(c.empty_handed),
                -len(c.risks),
            )

        best = max(results, key=score)
        return {
            "recommended_order": best.order,
            "candidates": {c.name: c.to_dict() for c in results},
        }

    def execute(self, order: Sequence[str], arrival: Dict[str, float]) -> Candidate:
        """Drive the live service through one order, keeping side effects."""
        self._reset_serials()
        return self.simulate(order, arrival=arrival, name="->".join(order))

    def _reset_serials(self) -> None:
        self._serial_n = 0
        if self._serial_pool is not None:
            # pool was possibly consumed during simulation; don't reuse.
            pass

    def _late_risks(self, order: Sequence[str], arrival: Dict[str, float]) -> List[str]:
        """Structural downside of an order, stated the way the brief states it."""
        risks: List[str] = []
        first = order[0]
        if first == "甲":
            risks.append(
                "先补甲：甲的第二张附件占着最后一格；若甲晚于乙的到场窗口，"
                "乙可能空手，须等甲到场、腾格后再补乙"
            )
        elif first == "乙":
            risks.append(
                "先办乙：乙的单据占着最后一格；乙若临时改期，甲的附件仍不能"
                "交齐，须等乙到场或柜员核实后退格"
            )
        return risks


def plan_two_pickups(
    service: ReleaseService,
    jia: Party,
    yi: Party,
    *,
    arrival: Dict[str, float],
    serial_seq: Optional[Iterable[str]] = None,
) -> dict:
    planner = Planner(service, [jia, yi], serial_seq=serial_seq)
    return planner.choose(arrival)
