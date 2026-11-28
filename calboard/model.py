"""Board description model for the offline calibration-board tool.

A board is described by a maintainer as:
  * independent power / reset domains,
  * register cells, each either cleared by a set of domain resets or retained
    across them,
  * default wiring between cells (a cell read out through wiring depends on the
    cells feeding it),
  * optional cross-domain control lines used by the runtime generation handshake.

Nothing here touches hardware; it is a pure data model used by the planner and
the runtime simulator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

VALID_KINDS = ("public", "raw")
VALID_RESET_KINDS = ("power_on", "warm", "local_power_loss")


class SpecError(ValueError):
    """Raised when a board description is malformed."""


@dataclass
class Cell:
    """One register cell.

    ``kind``
        ``"public"``  - a calibration result a window may publish.
        ``"raw"``     - a tenant metered / raw value that must stay on board.
    ``clear_scope``
        Domains whose reset clears this cell.  An empty scope means the cell is
        retained across every reset (it survives).
    ``value`` / ``gen``
        Initial content and the calibration-round token ("generation") that
        produced that content.  ``gen is None`` means empty / invalid.
    """

    name: str
    domain: str
    kind: str = "public"
    clear_scope: frozenset[str] = field(default_factory=frozenset)
    value: Any = None
    gen: Optional[str] = None

    def __post_init__(self) -> None:
        if self.kind not in VALID_KINDS:
            raise SpecError(f"cell {self.name!r}: bad kind {self.kind!r}")
        self.clear_scope = frozenset(self.clear_scope)

    @property
    def retained(self) -> bool:
        return not self.clear_scope

    def clone(self) -> "Cell":
        return Cell(
            name=self.name,
            domain=self.domain,
            kind=self.kind,
            clear_scope=frozenset(self.clear_scope),
            value=self.value,
            gen=self.gen,
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "domain": self.domain,
            "kind": self.kind,
            "clear_scope": sorted(self.clear_scope),
            "retained": self.retained,
            "value": self.value,
            "gen": self.gen,
        }


@dataclass
class Board:
    name: str
    domains: list[str]
    cells: dict[str, Cell]
    wiring: list[tuple[str, str]] = field(default_factory=list)  # src -> dst means dst depends on src
    links: list[tuple[str, str]] = field(default_factory=list)  # cross-domain control lines
    initial_rounds: dict[str, Optional[str]] = field(default_factory=dict)
    # reset kind -> {"order": [...], "max_stagger": int, "latency": int}
    reset_policy: dict[str, dict] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if len(set(self.domains)) != len(self.domains):
            raise SpecError("duplicate domain name")
        if not self.domains:
            raise SpecError("board must declare at least one domain")
        for cell in self.cells.values():
            if cell.domain not in self.domains:
                raise SpecError(f"cell {cell.name!r} in unknown domain {cell.domain!r}")
            for d in cell.clear_scope:
                if d not in self.domains:
                    raise SpecError(
                        f"cell {cell.name!r} cleared by unknown domain {d!r}"
                    )
        for src, dst in self.wiring:
            if src not in self.cells:
                raise SpecError(f"wiring source {src!r} is not a cell")
            if dst not in self.cells:
                raise SpecError(f"wiring destination {dst!r} is not a cell")
        for a, b in self.links:
            if a not in self.domains or b not in self.domains:
                raise SpecError(f"control line {a!r}-{b!r} references unknown domain")
            if a == b:
                raise SpecError(f"control line {a!r}-{b!r} is a self loop")
        if self.initial_rounds:
            for d in self.initial_rounds:
                if d not in self.domains:
                    raise SpecError(f"initial round for unknown domain {d!r}")
        for kind, policy in self.reset_policy.items():
            order = list(policy.get("order") or policy.get("domains") or [])
            if not order:
                raise SpecError(f"reset policy {kind!r} has no domain order")
            for d in order:
                if d not in self.domains:
                    raise SpecError(
                        f"reset policy {kind!r} names unknown domain {d!r}"
                    )
            if len(set(order)) != len(order):
                raise SpecError(f"reset policy {kind!r} repeats a domain")

    # -- convenience ----------------------------------------------------
    def cells_in_domain(self, domain: str) -> list[Cell]:
        return [c for c in self.cells.values() if c.domain == domain]

    def cells_cleared_by(self, domain: str) -> list[Cell]:
        return [c for c in self.cells.values() if domain in c.clear_scope]

    def link_adjacency(self) -> dict[str, set[str]]:
        adj: dict[str, set[str]] = {d: set() for d in self.domains}
        for a, b in self.links:
            adj[a].add(b)
            adj[b].add(a)
        return adj

    def domains_connected(self, domains: Iterable[str]) -> bool:
        scope = set(domains)
        if len(scope) <= 1:
            return True
        adj = self.link_adjacency()
        start = next(iter(scope))
        seen = {start}
        stack = [start]
        while stack:
            cur = stack.pop()
            for nxt in adj[cur]:
                if nxt in scope and nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        return seen == scope

    def incoming(self) -> dict[str, list[str]]:
        inc: dict[str, list[str]] = {name: [] for name in self.cells}
        for src, dst in self.wiring:
            inc[dst].append(src)
        return inc

    def cell_closure(self, name: str) -> list[str]:
        """Cells that must be read in order to read ``name`` (incl. itself)."""
        if name not in self.cells:
            raise SpecError(f"unknown cell/output {name!r}")
        inc = self.incoming()
        seen: set[str] = set()
        stack = [name]
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(inc[cur])
        return sorted(seen)

    def requirement_closure(self, names: Iterable[str]) -> list[str]:
        out: set[str] = set()
        for name in names:
            out.update(self.cell_closure(name))
        return sorted(out)

    def clone(self) -> "Board":
        return Board(
            name=self.name,
            domains=list(self.domains),
            cells={k: v.clone() for k, v in self.cells.items()},
            wiring=list(self.wiring),
            links=list(self.links),
            initial_rounds=dict(self.initial_rounds),
            reset_policy={k: dict(v) for k, v in self.reset_policy.items()},
        )

    def with_cell_clear_scope(self, cell: str, clear_scope: Iterable[str]) -> "Board":
        """Return a clone where one cell's clear scope is replaced."""
        if cell not in self.cells:
            raise SpecError(f"unknown cell {cell!r}")
        b = self.clone()
        b.cells[cell].clear_scope = frozenset(clear_scope)
        b.__post_init__()
        return b

    # -- (de)serialisation ---------------------------------------------
    @classmethod
    def from_dict(cls, doc: dict) -> "Board":
        domains = list(doc.get("domains") or [])
        cells: dict[str, Cell] = {}
        for spec in doc.get("cells") or []:
            name = spec["name"]
            domain = spec["domain"]
            if "clear_with" in spec:
                scope = frozenset(spec["clear_with"] or [])
            elif spec.get("retained", False):
                scope = frozenset()
            elif "clear_scope" in spec:
                scope = frozenset(spec["clear_scope"] or [])
            else:
                scope = frozenset({domain})
            kind = spec.get("kind", "public")
            init = spec.get("initial") or {}
            cells[name] = Cell(
                name=name,
                domain=domain,
                kind=kind,
                clear_scope=scope,
                value=init.get("value", spec.get("value")),
                gen=init.get("gen", spec.get("gen")),
            )
        wiring: list[tuple[str, str]] = []
        for edge in doc.get("wiring") or []:
            if isinstance(edge, dict):
                wiring.append((edge["from"], edge["to"]))
            else:
                wiring.append((edge[0], edge[1]))
        links: list[tuple[str, str]] = []
        for edge in doc.get("links") or []:
            if isinstance(edge, dict):
                links.append((edge["a"], edge["b"]))
            else:
                links.append((edge[0], edge[1]))
        return cls(
            name=doc.get("name", "board"),
            domains=domains,
            cells=cells,
            wiring=wiring,
            links=links,
            initial_rounds=dict(doc.get("initial_rounds") or {}),
            reset_policy={
                k: {
                    "order": list(v.get("order") or v.get("domains") or []),
                    "max_stagger": int(v.get("max_stagger", 0)),
                    "latency": int(v.get("latency", 0)),
                }
                for k, v in (doc.get("reset_policy") or {}).items()
            },
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "domains": list(self.domains),
            "cells": [c.to_dict() for c in self.cells.values()],
            "wiring": [{"from": a, "to": b} for a, b in self.wiring],
            "links": [{"a": a, "b": b} for a, b in self.links],
            "initial_rounds": dict(self.initial_rounds),
            "reset_policy": {
                k: {
                    "order": list(v.get("order") or []),
                    "max_stagger": int(v.get("max_stagger", 0)),
                    "latency": int(v.get("latency", 0)),
                }
                for k, v in self.reset_policy.items()
            },
        }
