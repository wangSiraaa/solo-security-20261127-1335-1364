"""Mutable world state: terminals, leases, injected cuts, epochs, handling logs.

The ``Dispatcher`` is the immutable-ish topology; ``World`` is the live scenario
state layered on top.  A ``Terminal`` is an end station (the liaison station or
the far end); its access ports can be physically attached or detached.  Each
domain keeps its own *handling log* of probe tickets it processed, which is the
ground-truth record of what was actually received.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .model import Cut, Dispatcher, Lease, PathView


@dataclass
class Terminal:
    """An end station with switchable access ports (primary / backup)."""

    name: str
    operator: str  # operator that hosts the access, i.e. the terminal's domain
    ports: Dict[str, bool] = field(default_factory=dict)  # port_id -> attached

    def attach(self, port_id: str) -> None:
        self.ports[port_id] = True

    def detach(self, port_id: str) -> None:
        self.ports[port_id] = False

    def is_attached(self, port_id: str) -> bool:
        return self.ports.get(port_id, False)


@dataclass(frozen=True)
class HandlingRecord:
    """One domain's record that it handled a probe ticket."""

    ticket_id: str
    domain: str
    role: str  # "operator" | "interchange" | "terminal"
    detail: str
    epoch: int


class World:
    """Live scenario state."""

    def __init__(self, dispatcher: Dispatcher) -> None:
        self.dispatcher = dispatcher
        self.terminals: Dict[str, Terminal] = {}
        self.leases: Dict[str, Lease] = {}
        self.cuts: Dict[str, Cut] = {}
        self.epoch: int = 0
        self.handling_log: Dict[str, List[HandlingRecord]] = {}
        self.events: List[Tuple[int, str]] = []

    # ---- construction -------------------------------------------------

    def add_terminal(self, name: str, operator: str, ports: List[str]) -> Terminal:
        t = Terminal(name=name, operator=operator)
        for p in ports:
            t.attach(p)
        self.terminals[name] = t
        return t

    def add_lease(self, lease: Lease) -> None:
        self.leases[lease.lease_id] = lease

    def reissue_lease(self, lease: Lease) -> None:
        """Replace a lease in place (models port re-leasing / re-contract)."""
        self.leases[lease.lease_id] = lease
        self.log(f"lease {lease.lease_id} reissued")

    # ---- state transitions -------------------------------------------

    def tick(self, n: int = 1) -> int:
        self.epoch += n
        return self.epoch

    def cut_domain(self, domain: str, reason: str = "") -> Cut:
        cut = Cut(domain=domain, reason=reason)
        self.cuts[domain] = cut
        self.log(f"cut {domain} ({reason})" if reason else f"cut {domain}")
        return cut

    def restore_domain(self, domain: str) -> None:
        self.cuts.pop(domain, None)
        self.log(f"restore {domain}")

    # ---- attachment ---------------------------------------------------

    def port_attached(self, port_id: str) -> Optional[bool]:
        """Attachment for a terminal port; ``None`` means 'not a terminal port'."""
        found = None
        for t in self.terminals.values():
            if port_id in t.ports:
                found = t.is_attached(port_id)
        return found

    def is_attached(self, port_id: str) -> bool:
        # Internal operator ports are always considered connected; only the
        # terminal access ports can be physically detached.
        val = self.port_attached(port_id)
        return True if val is None else val

    def assigned_ports(self, terminal_name: str) -> List[str]:
        t = self.terminals[terminal_name]
        return list(t.ports.keys())

    # ---- resolution / domain view ------------------------------------

    def resolve(self, lease: Lease) -> PathView:
        return self.dispatcher.resolve(lease)

    def lease_domains(self, lease: Lease) -> frozenset:
        return self.resolve(lease).domains

    # ---- handling log -------------------------------------------------

    def _record(self, rec: HandlingRecord) -> None:
        self.handling_log.setdefault(rec.domain, []).append(rec)

    def records_for(self, domain: str, ticket_id: Optional[str] = None) -> List[HandlingRecord]:
        recs = self.handling_log.get(domain, [])
        if ticket_id is None:
            return list(recs)
        return [r for r in recs if r.ticket_id == ticket_id]

    def find_received(self, ticket_id: str) -> List[HandlingRecord]:
        """All handling records for a ticket, across every domain."""
        out = []
        for recs in self.handling_log.values():
            out.extend(r for r in recs if r.ticket_id == ticket_id)
        return out

    # ---- misc ---------------------------------------------------------

    def log(self, msg: str) -> None:
        self.events.append((self.epoch, msg))

    def summary(self) -> str:
        lines = [f"epoch={self.epoch}"]
        lines.append(f"leases: {sorted(self.leases)}")
        lines.append(f"cuts: {sorted(self.cuts)}")
        for name, t in self.terminals.items():
            attached = [p for p, a in t.ports.items() if a]
            detached = [p for p, a in t.ports.items() if not a]
            lines.append(f"terminal {name}: attached={attached} detached={detached}")
        return "\n".join(lines)
