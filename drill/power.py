"""Power feasibility with per-feed branch limits.

Each feed (支路) has its own capacity.  A dual-fed cabinet can be served by
either feed, so the instantaneous load on each feed depends on how dual-fed
cabinets are assigned.  This is what makes a *shared branch* matter: if one
feed has to carry two chassis at once, their simultaneous startup peaks may
exceed that branch's breaker even though total supply looks sufficient.

Deterministic model (one tick == one scheduling round):
  * feed load       = sum of (base_load + startup_peak-at-this-tick) of
                      cabinets assigned to that feed
  * branch capacity = fixed per feed
  * battery may shave an instantaneous overload up to ``discharge_rate`` W
    for one tick, otherwise the overloaded feed must shed a chassis

A pair of starts is co-feasible iff there is an assignment of dual-fed
cabinets to feeds under which no feed exceeds capacity + discharge_rate.
"""
from __future__ import annotations

from itertools import combinations, product
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .model import Cabinet, Topology

DischargeRate = float


def _load_of(cab: Cabinet, starting: Set[str]) -> float:
    return cab.base_load + (cab.startup_peak if cab.name in starting else 0.0)


def best_assignment(topology: Topology, present: frozenset,
                    powered: Sequence[str], starting: Set[str],
                    ) -> Tuple[Dict[str, str], Dict[str, float]]:
    """Pick a feed for each dual-fed cabinet minimising the max feed load.

    Returns (cabinet->feed, feed->load).  Single-fed cabinets are forced.
    """
    feeds = [f for f in topology.feeds if f.name in present]
    load_of = {name: _load_of(topology.cabinet(name), starting) for name in powered}
    forced: Dict[str, str] = {}
    flexible: List[Tuple[str, Tuple[str, ...]]] = []
    for name in powered:
        cab = topology.cabinet(name)
        opts = tuple(f for f in cab.feeds if f in present)
        if not opts:
            continue
        if len(opts) == 1:
            forced[name] = opts[0]
        else:
            flexible.append((name, opts))

    best: Optional[Tuple[Dict[str, str], Dict[str, float]]] = None
    best_max = float("inf")
    feed_caps = {f.name: f.capacity for f in feeds}
    # bound search; scenarios are tiny
    for combo in product(*(opts for _, opts in flexible)):
        assign = dict(forced)
        for (name, _), feed in zip(flexible, combo):
            assign[name] = feed
        loads = {f: 0.0 for f in feed_caps}
        for name, feed in assign.items():
            loads[feed] += load_of[name]
        mx = max(loads.values()) if loads else 0.0
        if mx < best_max:
            best_max = mx
            best = (assign, loads)
    if best is None:
        return {}, {f.name: 0.0 for f in feeds}
    return best


def feed_overload(loads: Dict[str, float], topology: Topology,
                  present: Sequence[str]) -> float:
    cap = {f.name: f.capacity for f in topology.feeds}
    return sum(max(0.0, loads.get(f, 0.0) - cap[f]) for f in present)


def spare_headroom(loads: Dict[str, float], topology: Topology,
                   present: Sequence[str]) -> float:
    cap = {f.name: f.capacity for f in topology.feeds}
    return sum(max(0.0, cap[f] - loads.get(f, 0.0)) for f in present)


def feasible(topology: Topology, present: Sequence[str], battery: float,
             start_now: Sequence[str], discharge_rate: float = 60.0) -> bool:
    """Can every currently powered cabinet plus ``start_now`` peaks be served?"""
    present_f = frozenset(present)
    powered = [c.name for c in topology.cabinets if c.powered_by(present_f)]
    for n in start_now:
        if n not in powered:
            powered.append(n)
    assign, loads = best_assignment(topology, present_f, powered, set(start_now))
    overload = feed_overload(loads, topology, present)
    total_load = sum(loads.values())
    total_cap = sum(f.capacity for f in topology.feeds if f.name in present_f)
    # instantaneous overload must be shaved by battery (bounded) this tick
    if overload > min(discharge_rate, battery) + 1e-9:
        return False
    # sustained shortfall beyond capacity must be battery-funded
    sustained = max(0.0, total_load - total_cap)
    if sustained > battery + 1e-9:
        return False
    return True


def startup_conflicts(topology: Topology, present: Sequence[str],
                      battery: float, start_now: Sequence[str],
                      discharge_rate: float = 60.0) -> List[Tuple[str, str]]:
    conflicts: List[Tuple[str, str]] = []
    for a, b in combinations(start_now, 2):
        if not feasible(topology, present, battery, [a, b], discharge_rate):
            conflicts.append((a, b))
    return conflicts


def serial_start_tick_map(topology: Topology, present: Sequence[str],
                          battery: float, start_now: Sequence[str],
                          discharge_rate: float = 60.0) -> Dict[str, int]:
    remaining = list(start_now)
    plan: Dict[str, int] = {}
    tick = 0
    while remaining:
        batch: List[str] = []
        for name in list(remaining):
            if feasible(topology, present, battery, batch + [name], discharge_rate):
                batch.append(name)
        if not batch:
            batch = [remaining[0]]
        for name in batch:
            plan[name] = tick
            remaining.remove(name)
        tick += 1
    return plan


def co_feasible_pairs(topology: Topology, present: Sequence[str], battery: float,
                      names: Sequence[str],
                      discharge_rate: float = 60.0) -> Set[Tuple[str, str]]:
    return {(a, b) for a, b in combinations(names, 2)
            if feasible(topology, present, battery, [a, b], discharge_rate)}
