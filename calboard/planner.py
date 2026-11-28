"""Control planners for calibration-board runs.

Two publication strategies
==========================

Mode A -- synthesized fixed-tick control sequence
    Plan the reset actions, an optional clear/refresh, the calibration writes,
    and the window-open tick.  The plan is *proved* against every allowed
    normal reset interleaving (given order, bounded stagger, bounded latency):
    a window placed on a fixed tick is advertised only if, in *every*
    interleaving, the read closure is present, settled, carries one generation
    and contains no raw cell.  This is conservative: it must budget for the
    slowest dependent domain and must inspect the *actual* generation held by
    retained cells (which a reset never clears).

Mode B -- cross-domain generation handshake
    Domains carry this round's generation token over finite control lines and
    answer one another as they finish initialisation.  The window publishes at
    the first tick its parts all answer with the same generation.  It needs the
    closure's domains to be link-connected, and it must refuse when a domain
    that did not reset still holds an older generation while its neighbour has
    regenerated.

Both strategies share the same coherence rules from ``sim``; the planner only
decides *when* (Mode A, fixed and proved) or *whether* (Mode B, at the spot) a
window may publish.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product
from typing import Optional

from .events import Event, ReadRequest
from .model import Board, SpecError
from .sim import Simulator

MAX_INTERLEAVINGS = 200_000


# ---------------------------------------------------------------------------
# Reset policy / interleavings
# ---------------------------------------------------------------------------
@dataclass
class ResetSpec:
    kind: str
    order: list[str]
    max_stagger: int = 0
    latency: int = 0

    @property
    def domains(self) -> list[str]:
        return list(self.order)

    @classmethod
    def from_board(cls, board: Board, kind: str) -> "ResetSpec":
        policies = getattr(board, "reset_policy", {}) or {}
        if kind in policies:
            p = policies[kind]
            order = list(p.get("order") or p.get("domains") or [])
            return cls(
                kind=kind,
                order=order,
                max_stagger=int(p.get("max_stagger", 0)),
                latency=int(p.get("latency", 0)),
            )
        if kind in ("power_on", "warm") and kind not in policies:
            # default: every domain, simultaneous
            return cls(kind=kind, order=list(board.domains), max_stagger=0, latency=0)
        raise SpecError(f"board {board.name!r} has no reset policy for {kind!r}")


def allowed_interleavings(order: list[str], max_stagger: int) -> list[dict[str, int]]:
    """Every nondecreasing start-time assignment within the bounded stagger.

    The first domain in ``order`` is anchored at tick 0; later domains may start
    at or after the previous one, never exceeding ``max_stagger`` after the
    first.  This is the set of "normal reset orderings" a fixed-window proof
    must survive.
    """
    n = len(order)
    if n == 0:
        return [{}]
    out: list[dict[str, int]] = []
    if max_stagger < 0:
        raise SpecError("max_stagger must be >= 0")

    def rec(i: int, assign: dict[str, int], starts: list[int]) -> None:
        if len(out) > MAX_INTERLEAVINGS:
            raise SpecError(
                f"too many reset interleavings (> {MAX_INTERLEAVINGS}); "
                "reduce max_stagger or domains"
            )
        if i == n:
            out.append(dict(assign))
            return
        if not starts:
            # the first domain in the order is anchored at tick 0; stagger is
            # measured relative to it.
            assign[order[i]] = 0
            starts.append(0)
            rec(i + 1, assign, starts)
            starts.pop()
            del assign[order[i]]
            return
        lo = starts[-1]
        for s in range(lo, max_stagger + 1):
            if s - starts[0] > max_stagger:
                break
            assign[order[i]] = s
            starts.append(s)
            rec(i + 1, assign, starts)
            starts.pop()
            del assign[order[i]]

    rec(0, {}, [])
    return out


# ---------------------------------------------------------------------------
# Mode A planning
# ---------------------------------------------------------------------------
@dataclass
class CellSettlement:
    name: str
    domain: str
    kind: str
    cleared: bool
    written: bool
    old_gen: Optional[str]
    settle_min: int = 0
    settle_max: int = 0
    problem: str = ""


@dataclass
class Plan:
    board: str
    reset_kind: str
    round_gen: str
    want: list[str]
    ok: bool
    window_tick: Optional[int]
    write_tick: Optional[int]
    events: list[Event] = field(default_factory=list)
    cells: list[CellSettlement] = field(default_factory=list)
    retained: list[dict] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    interleavings: int = 0

    def to_dict(self) -> dict:
        return {
            "board": self.board,
            "reset_kind": self.reset_kind,
            "round_gen": self.round_gen,
            "want": list(self.want),
            "ok": self.ok,
            "window_tick": self.window_tick,
            "write_tick": self.write_tick,
            "interleavings": self.interleavings,
            "cells": [
                {
                    "name": c.name,
                    "domain": c.domain,
                    "kind": c.kind,
                    "cleared": c.cleared,
                    "written": c.written,
                    "old_gen": c.old_gen,
                    "settle_min": c.settle_min,
                    "settle_max": c.settle_max,
                    "problem": c.problem,
                }
                for c in self.cells
            ],
            "retained": list(self.retained),
            "reasons": list(self.reasons),
            "events": [
                {
                    "at": e.at,
                    "kind": e.kind,
                    "domains": e.domains,
                    "domain": e.domain,
                    "cell": e.cell,
                    "value": e.value,
                    "gen": e.gen,
                    "window": e.window,
                    "latency": e.latency,
                }
                for e in self.events
            ],
        }


def _cell_settle(interleaving: dict[str, int], latency: int, board: Board,
                 name: str) -> int:
    """Tick by which ``name`` is settled (and, if cleared) emptied."""
    cell = board.cells[name]
    clearing = [d for d in cell.clear_scope if d in interleaving]
    if not clearing:
        return 0
    return max(interleaving[d] for d in clearing) + latency


def synthesize_plan(
    board: Board,
    reset: ResetSpec | str,
    round_gen: str,
    program_writes: dict[str, object],
    want: list[str],
) -> Plan:
    """Build a fixed-tick Mode A plan, proving it over all interleavings.

    ``program_writes`` are the cells the calibration program genuinely
    (re)measures this round; they are written with ``round_gen``.  A cell that
    the program does not measure is never relabelled -- a retained cell that
    still holds an older generation makes the plan fail rather than silently
    advertising stale data as current.
    """
    if isinstance(reset, str):
        reset = ResetSpec.from_board(board, reset)
    spec = reset
    if not want:
        raise SpecError("plan needs at least one window output in 'want'")

    interleavings = allowed_interleavings(spec.order, spec.max_stagger)
    resetting = set(spec.order)
    closure = board.requirement_closure(want)

    plan = Plan(
        board=board.name,
        reset_kind=spec.kind,
        round_gen=round_gen,
        want=list(want),
        ok=True,
        window_tick=None,
        write_tick=None,
        interleavings=len(interleavings),
    )

    # Determine, per closure cell, what this reset does to it and whether the
    # program rewrites it.
    write_tick_candidates: list[int] = []
    for name in closure:
        cell = board.cells[name]
        cleared = bool(cell.clear_scope & resetting)
        written = name in program_writes
        settle_min = min(_cell_settle(iv, spec.latency, board, name)
                         for iv in interleavings)
        settle_max = max(_cell_settle(iv, spec.latency, board, name)
                         for iv in interleavings)
        cs = CellSettlement(
            name=name,
            domain=cell.domain,
            kind=cell.kind,
            cleared=cleared,
            written=written,
            old_gen=cell.gen,
            settle_min=settle_min,
            settle_max=settle_max,
        )

        if cell.kind == "raw":
            plan.ok = False
            cs.problem = "raw tenant cell cannot be published"
            plan.reasons.append(
                f"cell {name!r} is raw tenant data and is in the read set of "
                f"{want!r}; it must never reach a public window"
            )
        elif cleared and not written:
            plan.ok = False
            cs.problem = "cleared by this reset but not recalibrated -> empty"
            plan.reasons.append(
                f"cell {name!r} is cleared by the {spec.kind} reset and the "
                f"calibration program does not rewrite it; the window would "
                f"read an empty register"
            )
        elif not cleared and not written:
            # retained across this reset and not remeasured: only usable if its
            # actual surviving generation already matches this round.
            plan.retained.append(
                {
                    "cell": name,
                    "domain": cell.domain,
                    "surviving_gen": cell.gen,
                    "round_gen": round_gen,
                    "matches": cell.gen == round_gen,
                }
            )
            if cell.gen != round_gen:
                plan.ok = False
                cs.problem = (
                    f"retained cell holds gen {cell.gen!r}, round is "
                    f"{round_gen!r}; it must be genuinely recalibrated or "
                    f"excluded, never relabelled"
                )
                plan.reasons.append(
                    f"retained cell {name!r} survives the reset holding "
                    f"gen {cell.gen!r}, which differs from this round "
                    f"{round_gen!r}; including it would publish stale data "
                    f"as current"
                )
        if written:
            write_tick_candidates.append(settle_max)
        plan.cells.append(cs)

    if not plan.ok:
        return plan

    # The write must land after the slowest possible settle of every written
    # closure cell, in every interleaving, otherwise it is lost to a reset
    # still in flight.  Booking the maximum is exactly the wait Mode A owes to
    # late dependent domains.
    write_tick = max(write_tick_candidates) if write_tick_candidates else 0
    window_tick = write_tick
    plan.write_tick = write_tick
    plan.window_tick = window_tick

    # Build the canonical concrete control sequence: simultaneous resets (the
    # fastest interleaving), then writes and the window at the proved tick.
    # Because the tick is valid for *every* interleaving, the fastest one is
    # safe too; the plan deliberately idles until the worst-case settle has
    # passed.
    events: list[Event] = []
    for d in spec.order:
        events.append(Event(at=0, kind="reset", domains=[d], latency=spec.latency))
    for cell_name in sorted(program_writes):
        events.append(
            Event(
                at=write_tick,
                kind="write",
                cell=cell_name,
                value=program_writes[cell_name],
                gen=round_gen,
            )
        )
    plan.events = events

    # Verify the chosen tick against *every* allowed interleaving rather than
    # trusting the arithmetic.
    proof = prove_fixed_tick(
        board, spec, round_gen, program_writes, want, window_tick
    )
    plan.interleavings = proof["interleavings"]
    if not proof["safe_in_all"]:
        plan.ok = False
        bad = [r for r in proof["rows"] if not r["published"]]
        plan.reasons.append(
            f"chosen tick {window_tick} is not safe in all "
            f"{proof['interleavings']} interleavings ({len(bad)} unsafe)"
        )
        for r in bad[:3]:
            plan.reasons.append(
                f"  starts {r['starts']}: {'; '.join(r['reasons']) or 'not published'}"
            )
    return plan


def prove_fixed_tick(
    board: Board,
    reset: ResetSpec | str,
    round_gen: str,
    program_writes: dict[str, object],
    want: list[str],
    tick: int,
) -> dict:
    """Check a fixed window tick against every allowed reset interleaving.

    The calibration writes are placed at ``tick`` as well (write-then-read in
    the same tick).  Returns a per-interleaving verdict plus an overall flag.
    """
    if isinstance(reset, str):
        reset = ResetSpec.from_board(board, reset)
    spec = reset
    interleavings = allowed_interleavings(spec.order, spec.max_stagger)
    rows = []
    all_safe = True
    for iv in interleavings:
        events: list[Event] = []
        for d in spec.order:
            events.append(
                Event(at=iv[d], kind="reset", domains=[d], latency=spec.latency)
            )
        for cell_name in sorted(program_writes):
            events.append(
                Event(
                    at=tick,
                    kind="write",
                    cell=cell_name,
                    value=program_writes[cell_name],
                    gen=round_gen,
                )
            )
        req = ReadRequest(at=tick, window="prove", want=list(want), generation=round_gen)
        res = Simulator(board.clone(), events, [req]).run()
        rec = res.records[0]
        safe = rec.published
        all_safe = all_safe and safe
        rows.append(
            {
                "starts": dict(iv),
                "published": safe,
                "gen": rec.gen,
                "reasons": list(rec.reasons),
                "unsettled": list(rec.unsettled),
                "missing": list(rec.missing),
                "generation_mismatch": rec.generation_mismatch,
            }
        )
    return {
        "board": board.name,
        "reset_kind": spec.kind,
        "round_gen": round_gen,
        "want": list(want),
        "tick": tick,
        "interleavings": len(interleavings),
        "safe_in_all": all_safe,
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# Mode B handshake feasibility
# ---------------------------------------------------------------------------
def handshake_plan(board: Board, want: list[str]) -> dict:
    """Report whether a closure can be sealed by a cross-domain handshake.

    The closure's domains must be connected through control lines so the parts
    can exchange this round's token.  We return one concrete spanning set of
    links (the finite control-line budget the handshake consumes) and the
    domains involved.
    """
    closure = board.requirement_closure(want)
    domains = sorted({board.cells[c].domain for c in closure})
    adj = board.link_adjacency()

    # BFS over the closure's domains, collecting a spanning link set.
    if not domains:
        return {"ok": False, "domains": [], "links": [], "reasons": ["empty closure"]}
    start = domains[0]
    seen = {start}
    parent: dict[str, str] = {}
    queue = [start]
    while queue:
        cur = queue.pop(0)
        for nxt in sorted(adj[cur]):
            if nxt in domains and nxt not in seen:
                seen.add(nxt)
                parent[nxt] = cur
                queue.append(nxt)
    connected = seen == set(domains)
    links = sorted(
        tuple(sorted((nxt, p))) for nxt, p in parent.items()
    )
    raw_cells = [c for c in closure if board.cells[c].kind == "raw"]
    reasons = []
    if not connected:
        reasons.append(
            "closure domains are not connected by control lines: "
            + ", ".join(sorted(set(domains) - seen))
        )
    if raw_cells:
        reasons.append("raw tenant cell(s) in closure: " + ", ".join(raw_cells))
    return {
        "ok": connected and not raw_cells,
        "domains": domains,
        "links": [{"a": a, "b": b} for a, b in links],
        "link_budget": len(links),
        "closure": closure,
        "reasons": reasons,
    }


# ---------------------------------------------------------------------------
# Concrete-run helper: turn an explicit run description into events+requests
# ---------------------------------------------------------------------------
def build_run(
    board: Board,
    reset: ResetSpec | str | None,
    interleaving_starts: dict[str, int] | None,
    round_gen: str,
    program_writes: dict[str, object],
    write_tick: int,
    windows: list[ReadRequest],
    extra_events: list[Event] | None = None,
) -> tuple[list[Event], list[ReadRequest]]:
    """Assemble a concrete run from a chosen normal reset order and writes."""
    events: list[Event] = []
    if reset is not None:
        if isinstance(reset, str):
            reset = ResetSpec.from_board(board, reset)
        starts = interleaving_starts or {d: 0 for d in reset.order}
        missing = set(reset.order) - set(starts)
        if missing:
            raise SpecError(f"interleaving missing starts for {sorted(missing)}")
        for d in reset.order:
            events.append(
                Event(at=starts[d], kind="reset", domains=[d], latency=reset.latency)
            )
    for cell_name in sorted(program_writes):
        events.append(
            Event(
                at=write_tick,
                kind="write",
                cell=cell_name,
                value=program_writes[cell_name],
                gen=round_gen,
            )
        )
    if extra_events:
        events.extend(extra_events)
    return events, list(windows)
