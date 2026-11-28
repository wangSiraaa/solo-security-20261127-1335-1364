"""Mode B runtime: cross-domain generation handshake.

In handshake mode each domain, when it finishes (re)initialising, broadcasts
*this round's* generation token on the finite control lines it shares with the
other closure domains.  A window may publish only once every domain in its read
closure has answered with the *same* generation, and each answered generation
must equal the content generation the closure cells actually carry.

The subtle failure this module refuses: after a *partial* reset, a healthy
domain that was not reset still holds an older generation while its reset
neighbour has already regenerated.  The handshake must not let the window seal
on the strength of acks alone -- it cross-checks the actual register content,
so a stale surviving cell blocks publication until it is genuinely refreshed.

In this offline tool the handshake is *simulated*: we compute, for a given run,
the earliest tick at which every closure domain can truthfully ack the same
generation and the registers agree, honouring link connectivity and the finite
line budget.  No hardware or real control line is touched.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .events import Event, ReadRequest
from .model import Board
from .sim import Simulator


@dataclass
class DomainAck:
    domain: str
    gen: Optional[str]
    tick: Optional[int]  # first tick this domain could truthfully ack ``gen``
    reset_in_run: bool
    acked: bool = False
    problem: str = ""


@dataclass
class HandshakeOutcome:
    window: str
    want: list[str]
    closure: list[str]
    domains: list[str]
    links: list[dict]
    link_budget: int
    ok: bool
    publish_tick: Optional[int]
    gen: Optional[str]
    acks: list[DomainAck] = field(default_factory=list)
    outputs: dict[str, Any] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    deadline: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "window": self.window,
            "want": list(self.want),
            "closure": list(self.closure),
            "domains": list(self.domains),
            "links": list(self.links),
            "link_budget": self.link_budget,
            "ok": self.ok,
            "publish_tick": self.publish_tick,
            "gen": self.gen,
            "deadline": self.deadline,
            "acks": [
                {
                    "domain": a.domain,
                    "gen": a.gen,
                    "tick": a.tick,
                    "reset_in_run": a.reset_in_run,
                    "acked": a.acked,
                    "problem": a.problem,
                }
                for a in self.acks
            ],
            "outputs": dict(self.outputs),
            "reasons": list(self.reasons),
        }


def _spanning_links(board: Board, domains: list[str]) -> tuple[bool, list[tuple[str, str]]]:
    adj = board.link_adjacency()
    if not domains:
        return False, []
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
    links = sorted(tuple(sorted((nxt, p))) for nxt, p in parent.items())
    return seen == set(domains), links


def handshake_windows(
    board: Board,
    events: list[Event],
    requests: list[ReadRequest],
) -> list[HandshakeOutcome]:
    """Resolve every handshake-mode request against the run and report acks.

    We run the plain simulator to get the per-tick register state, then for
    each handshake window find the earliest coherent tick and build the
    cross-domain ack story that would have permitted (or refused) it.
    """
    hs = [r for r in requests if r.mode == "handshake"]
    if not hs:
        return []
    # The simulator already publishes handshake windows at the right tick; we
    # re-run to obtain the trace *and* reconstruct acks for reporting.
    deadlines = {r.deadline for r in hs if r.deadline is not None}
    sim = Simulator(
        board.clone(),
        list(events),
        [r for r in requests if r.mode == "fixed"] + hs,
        snapshot_at=deadlines,
    )
    result = sim.run()
    recs = {r.window: r for r in result.records}

    outcomes: list[HandshakeOutcome] = []
    reset_domains = set()
    for ev in events:
        if ev.kind in ("reset", "local_power_loss"):
            reset_domains.update(ev.reset_domains)

    for req in hs:
        closure = board.requirement_closure(req.want)
        domains = sorted({board.cells[c].domain for c in closure})
        connected, links = _spanning_links(board, domains)
        rec = recs.get(req.window)
        out = HandshakeOutcome(
            window=req.window,
            want=list(req.want),
            closure=closure,
            domains=domains,
            links=[{"a": a, "b": b} for a, b in links],
            link_budget=len(links),
            ok=False,
            publish_tick=None,
            gen=None,
            deadline=req.deadline,
        )
        if not connected:
            out.reasons.append(
                "closure domains not connected by control lines"
            )
        if any(board.cells[c].kind == "raw" for c in closure):
            out.reasons.append("raw tenant cell in closure")

        # A domain can ack generation g at tick t if *all* its closure cells
        # are present, settled and on g.  We surface the reconstructed acks
        # from the published record; on refusal we model domain-level answers.
        if rec is not None and rec.published:
            out.ok = True
            out.publish_tick = rec.tick
            out.gen = rec.gen
            out.outputs = dict(rec.outputs)
            for d in domains:
                cells = [c for c in closure if board.cells[c].domain == d]
                agreed = all(
                    reg.get("gen") == rec.gen
                    for reg in rec.registers
                    if reg["cell"] in cells
                )
                out.acks.append(
                    DomainAck(
                        domain=d,
                        gen=rec.gen,
                        tick=rec.tick,
                        reset_in_run=d in reset_domains,
                        acked=agreed,
                    )
                )
        else:
            # Refusal: reconstruct what each domain actually held at the
            # close, and why it could not seal.  This is where a domain that
            # did not reset but whose neighbour regenerated shows up as
            # holding an inconsistent surviving generation.
            last_tick = getattr(req, "deadline", req.at) or req.at
            snap = sim.snapshots.get(last_tick)
            if snap is None:
                # Snapshot wasn't requested for this tick; fall back to final.
                snap = result.final_cells | {}
                snap = {
                    c: {
                        "value": v["value"],
                        "gen": v["gen"],
                        "settled": True,
                    }
                    for c, v in snap.items()
                }
            gens: dict[str, list[str]] = {}
            for d in domains:
                cells = [c for c in closure if board.cells[c].domain == d]
                dgens = []
                problem = ""
                for c in cells:
                    st = snap.get(c)
                    if st is None or st["value"] is None:
                        problem = problem or f"cell {c!r} empty at deadline"
                    elif not st["settled"]:
                        problem = problem or f"cell {c!r} unsettled at deadline"
                    else:
                        dgens.append(st["gen"])
                if dgens and len(set(dgens)) == 1:
                    g = dgens[0]
                else:
                    g = None
                gens.setdefault(str(g), []).append(d)
                out.acks.append(
                    DomainAck(
                        domain=d,
                        gen=g,
                        tick=None,
                        reset_in_run=d in reset_domains,
                        acked=False,
                        problem=problem,
                    )
                )
            if len(gens) > 1:
                out.reasons.append(
                    "closure domains hold different generations at deadline: "
                    + "; ".join(
                        f"{g}: {', '.join(ds)}" for g, ds in sorted(gens.items())
                    )
                )
            if rec is not None:
                out.reasons.extend(rec.reasons)
            else:
                out.reasons.append("window never evaluated (beyond horizon)")
        outcomes.append(out)
    return outcomes
