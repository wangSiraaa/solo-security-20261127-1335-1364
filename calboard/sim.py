"""Tick-by-tick simulator for calibration-board runs.

The simulator applies reset / clear / write / window events to an in-memory
register file and records, for every window read, exactly which register cells
were read and what was (or could safely be) published.

Correctness rules encoded here
------------------------------
* A reset of a domain clears exactly the cells whose ``clear_scope`` contains
  that domain.  Other cells are untouched (retained cells have empty scope and
  therefore survive every reset).
* A reset has latency: cells it will clear are *unsettled* for ``latency``
  ticks.  Reading an unsettled cell is refused; a write during the settle
  window is lost (the pending clear wins).
* A window may publish only when every cell in the read closure is
  public (never a raw tenant cell), present, settled, and carries the *same*
  generation token.  Numeric equality is never enough -- two calibration rounds
  with identical values still have distinct generations.
* Each window is evaluated independently.  A window that already published is
  never retracted because a different window later misses its deadline.

Window modes
------------
``fixed``
    The window commits to the single tick it is scheduled on (Mode A).  It
    publishes if and only if the closure is coherent at that exact tick.
``handshake``
    The window opens at tick ``at`` and may publish on the *first* tick in
    ``[at, deadline]`` at which the closure is coherent (Mode B).  This models
    domains that carry this round's token across control lines and answer each
    other before permitting publication.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .events import Event, ReadRequest
from .model import Board


@dataclass
class CellState:
    value: Any = None
    gen: Optional[str] = None
    unsettled_until: int = 0

    def readable(self, tick: int) -> bool:
        return self.value is not None and tick >= self.unsettled_until


@dataclass
class ReadRecord:
    tick: int
    window: str
    want: list[str]
    mode: str = "fixed"
    generation_required: Optional[str] = None
    published: bool = False
    gen: Optional[str] = None
    outputs: dict[str, Any] = field(default_factory=dict)
    registers: list[dict] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    leaked_raw: bool = False
    missing: list[str] = field(default_factory=list)
    unsettled: list[str] = field(default_factory=list)
    generation_mismatch: bool = False
    raw_in_readset: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "tick": self.tick,
            "window": self.window,
            "mode": self.mode,
            "want": list(self.want),
            "generation_required": self.generation_required,
            "published": self.published,
            "gen": self.gen,
            "outputs": dict(self.outputs),
            "registers": list(self.registers),
            "reasons": list(self.reasons),
            "leaked_raw": self.leaked_raw,
            "missing": list(self.missing),
            "unsettled": list(self.unsettled),
            "generation_mismatch": self.generation_mismatch,
            "raw_in_readset": list(self.raw_in_readset),
        }


@dataclass
class RunResult:
    records: list[ReadRecord] = field(default_factory=list)
    final_cells: dict[str, dict] = field(default_factory=dict)
    timeline: list[dict] = field(default_factory=list)

    @property
    def leaks(self) -> list[ReadRecord]:
        return [r for r in self.records if r.leaked_raw]

    @property
    def published(self) -> list[ReadRecord]:
        return [r for r in self.records if r.published]

    def by_window(self) -> dict[str, list[ReadRecord]]:
        out: dict[str, list[ReadRecord]] = {}
        for r in self.records:
            out.setdefault(r.window, []).append(r)
        return out

    def to_dict(self) -> dict:
        return {
            "records": [r.to_dict() for r in self.records],
            "final_cells": dict(self.final_cells),
            "timeline": list(self.timeline),
        }


class Simulator:
    def __init__(
        self,
        board: Board,
        events: list[Event],
        requests: list[ReadRequest],
        snapshot_at: Optional[set[int]] = None,
    ):
        self.board = board
        self.events = events
        self.requests = requests
        self.snapshot_at = set(snapshot_at or ())
        self.snapshots: dict[int, dict[str, dict]] = {}
        self.cells: dict[str, CellState] = {
            name: CellState(value=cell.value, gen=cell.gen)
            for name, cell in board.cells.items()
        }
        self.result = RunResult()

    # -- helpers --------------------------------------------------------
    def _affected(self, domains: set[str]) -> list[str]:
        return [
            name
            for name, cell in self.board.cells.items()
            if cell.clear_scope & domains
        ]

    def _horizon(self) -> int:
        ticks = [e.at for e in self.events]
        ticks += [r.at for r in self.requests]
        ticks += [getattr(r, "deadline", 0) or 0 for r in self.requests]
        ticks += [e.at + e.latency for e in self.events]
        return (max(ticks) if ticks else 0) + 2

    # -- evaluation ------------------------------------------------------
    def _evaluate(self, req: ReadRequest, tick: int) -> ReadRecord:
        rec = ReadRecord(
            tick=tick,
            window=req.window,
            want=list(req.want),
            mode=getattr(req, "mode", "fixed"),
            generation_required=req.generation,
        )
        closure = self.board.requirement_closure(req.want)
        gens: set[str] = set()
        no_gen: list[str] = []
        registers: list[dict] = []
        for name in closure:
            cell = self.board.cells[name]
            st = self.cells[name]
            settled = tick >= st.unsettled_until
            readable = st.readable(tick)
            registers.append(
                {
                    "cell": name,
                    "domain": cell.domain,
                    "kind": cell.kind,
                    "value": st.value,
                    "gen": st.gen,
                    "settled": settled,
                    "present": st.value is not None,
                }
            )
            if cell.kind == "raw":
                rec.raw_in_readset.append(name)
            if st.value is None:
                rec.missing.append(name)
            elif not settled:
                rec.unsettled.append(name)
            elif readable:
                if st.gen is None:
                    no_gen.append(name)
                else:
                    gens.add(st.gen)
        rec.registers = registers

        if rec.raw_in_readset:
            rec.leaked_raw = True
            rec.reasons.append(
                "raw tenant cell in read set: " + ", ".join(rec.raw_in_readset)
            )
        if rec.missing:
            rec.reasons.append("missing register(s): " + ", ".join(rec.missing))
        if rec.unsettled:
            rec.reasons.append("unsettled register(s): " + ", ".join(rec.unsettled))
        if no_gen:
            rec.reasons.append(
                "register(s) with no calibration generation: " + ", ".join(no_gen)
            )
        if len(gens) > 1:
            rec.generation_mismatch = True
            rec.reasons.append(
                "cells span multiple generations: "
                + ", ".join(sorted(map(str, gens)))
            )
        elif len(gens) == 1:
            rec.gen = next(iter(gens))
            if req.generation is not None and rec.gen != req.generation:
                rec.reasons.append(
                    f"generation {rec.gen!r} != required {req.generation!r}"
                )
        if not req.want:
            rec.reasons.append("window names no output to publish")

        ok = (
            bool(req.want)
            and not rec.leaked_raw
            and not rec.missing
            and not rec.unsettled
            and not no_gen
            and not rec.generation_mismatch
            and rec.gen is not None
            and (req.generation is None or rec.gen == req.generation)
        )
        rec.published = ok
        if ok:
            rec.outputs = {name: self.cells[name].value for name in req.want}
        return rec

    # -- main loop -------------------------------------------------------
    def run(self) -> RunResult:
        horizon = self._horizon()
        by_tick_events: dict[int, list[Event]] = {}
        for e in self.events:
            by_tick_events.setdefault(e.at, []).append(e)

        # Active handshake windows: list of ReadRequest not yet resolved.
        active_handshake: list[ReadRequest] = [
            r for r in self.requests if getattr(r, "mode", "fixed") == "handshake"
        ]
        # Fixed windows bucketed by their single tick.
        fixed_at: dict[int, list[ReadRequest]] = {}
        for r in self.requests:
            if getattr(r, "mode", "fixed") != "handshake":
                fixed_at.setdefault(r.at, []).append(r)
        # Resolved handshake requests.
        resolved: set[int] = set()

        pending_clears: dict[int, list[str]] = {}

        for tick in range(horizon + 1):
            # 1) resets at this tick
            for ev in sorted(
                by_tick_events.get(tick, []),
                key=lambda e: 0 if e.kind in ("reset", "local_power_loss") else 1,
            ):
                if ev.kind in ("reset", "local_power_loss"):
                    self._apply_reset(ev, pending_clears)

            # 2) settle sweep: a pending clear wins over writes made while
            #    the reset was still settling.
            for cell_name in pending_clears.pop(tick, []):
                st = self.cells[cell_name]
                st.value = None
                st.gen = None
                self.result.timeline.append(
                    {"tick": tick, "op": "clear_settled", "cell": cell_name}
                )

            # 3) writes / clears at this tick
            for ev in by_tick_events.get(tick, []):
                if ev.kind == "write":
                    self._apply_write(ev, tick)
                elif ev.kind == "clear":
                    self._apply_clear(ev, tick)

            # 4a) fixed windows committed to this tick
            for req in fixed_at.get(tick, []):
                self.result.records.append(self._evaluate(req, tick))

            # 4b) handshake windows: may publish at first coherent tick.
            for req in active_handshake:
                if id(req) in resolved:
                    continue
                if tick < req.at:
                    continue
                deadline = getattr(req, "deadline", req.at)
                if tick > deadline:
                    continue
                cand = self._evaluate(req, tick)
                if cand.published:
                    self.result.records.append(cand)
                    resolved.add(id(req))
                elif tick == deadline:
                    cand.reasons.append(
                        f"handshake not satisfied by deadline {deadline}"
                    )
                    cand.published = False
                    cand.outputs = {}
                    self.result.records.append(cand)
                    resolved.add(id(req))

            # 5) optional snapshot for external analysis
            if tick in self.snapshot_at:
                self.snapshots[tick] = {
                    name: {
                        "value": st.value,
                        "gen": st.gen,
                        "settled": tick >= st.unsettled_until,
                    }
                    for name, st in self.cells.items()
                }

        self.result.final_cells = {
            name: {"value": st.value, "gen": st.gen}
            for name, st in self.cells.items()
        }
        return self.result

    def _apply_reset(self, ev: Event, pending_clears: dict[int, list[str]]) -> None:
        settle = ev.at + ev.latency
        domains = set(ev.reset_domains)
        affected = self._affected(domains)
        for name in affected:
            st = self.cells[name]
            st.unsettled_until = max(st.unsettled_until, settle)
            pending_clears.setdefault(settle, []).append(name)
        self.result.timeline.append(
            {
                "tick": ev.at,
                "op": ev.kind,
                "domains": sorted(domains),
                "settle": settle,
                "cleared": sorted(affected),
            }
        )

    def _apply_write(self, ev: Event, tick: int) -> None:
        assert ev.cell is not None
        if ev.cell not in self.cells:
            return
        st = self.cells[ev.cell]
        if tick < st.unsettled_until:
            # Lost: a clearing reset is still in flight for this cell.
            self.result.timeline.append(
                {
                    "tick": tick,
                    "op": "write_lost",
                    "cell": ev.cell,
                    "reason": f"unsettled until {st.unsettled_until}",
                }
            )
            return
        st.value = ev.value
        st.gen = ev.gen
        self.result.timeline.append(
            {
                "tick": tick,
                "op": "write",
                "cell": ev.cell,
                "value": ev.value,
                "gen": ev.gen,
            }
        )

    def _apply_clear(self, ev: Event, tick: int) -> None:
        assert ev.cell is not None
        if ev.cell in self.cells:
            self.cells[ev.cell].value = None
            self.cells[ev.cell].gen = None
            self.result.timeline.append(
                {"tick": tick, "op": "clear", "cell": ev.cell}
            )
