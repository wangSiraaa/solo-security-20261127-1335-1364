"""A second operator's console (第另一位操作员).

It can isolate a fault domain it has heard of -- including a **fictional** one
that no real path uses -- switch the lines it is watching, and then look up what
was *actually received* by querying the per-domain handling logs.

The point of the exercise: cutting a fictional domain changes nothing physical,
so a genuinely working path still delivers; the announcements you can retrieve
are exactly the ones whose delivery records exist, and a fake domain's log is
empty.  You cannot manufacture receipt by isolating a domain name.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from .delivery import DeliveryEngine
from .model import Lease
from .world import HandlingRecord, World


@dataclass
class LineSpec:
    """A lease the operator can switch to, with its concrete access ports."""

    name: str
    lease_id: str
    src_port: str
    dst_port: str
    src_terminal: str = "SRC"
    dst_terminal: str = "DST"


class SecondOperatorConsole:
    def __init__(self, world: World, engine: DeliveryEngine) -> None:
        self.world = world
        self.engine = engine
        self.lines: Dict[str, LineSpec] = {}
        self.active: Optional[str] = None
        self.received: Dict[str, List[HandlingRecord]] = {}

    # ---- line management ---------------------------------------------

    def register_line(self, spec: LineSpec) -> None:
        self.lines[spec.name] = spec

    def switch_line(self, name: str) -> LineSpec:
        if name not in self.lines:
            raise KeyError(f"unknown line {name}")
        self.active = name
        self.world.log(f"operator-2 switches to line {name}")
        return self.lines[name]

    # ---- isolation ----------------------------------------------------

    def isolate(self, domain: str, reason: str = "operator-2 drill") -> None:
        """Isolate any domain, real or fictional."""
        self.world.cut_domain(domain, reason=reason)
        self.world.log(f"operator-2 isolates {domain}")

    def restore(self, domain: str) -> None:
        self.world.restore_domain(domain)

    # ---- announce & receive -------------------------------------------

    def announce(self, ticket_id: str, text: str = "announcement") -> bool:
        """Send one announcement over the active line; record what is received."""
        if self.active is None:
            raise RuntimeError("no active line")
        spec = self.lines[self.active]
        lease: Lease = self.world.leases[spec.lease_id]
        res = self.engine.deliver(
            ticket_id,
            spec.src_terminal,
            spec.dst_terminal,
            lease,
            spec.src_port,
            spec.dst_port,
            announce=text,
        )
        if res.ok:
            # The receiving side's real receipts are the destination terminal's
            # handling records for this ticket.
            self.received[ticket_id] = self.world.records_for(spec.dst_terminal, ticket_id)
        return res.ok

    def received_announcements(self) -> List[str]:
        """Ticket ids this operator can prove were genuinely received."""
        return sorted(self.received.keys())

    def query_domain_log(self, domain: str) -> List[HandlingRecord]:
        return self.world.records_for(domain)
