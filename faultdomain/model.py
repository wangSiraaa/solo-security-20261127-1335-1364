"""Core physical/domain model for the split-fault-domain exercise.

Vocabulary (matching the exercise brief):

* **operator** – a company that owns links and access ports.
* **fault domain** – a unit of failure.  Both operators *and* interchanges are
  fault domains: cutting a domain makes everything inside it unreachable.
* **link** – a port-to-port adjacency inside one operator; carries traffic only
  while both endpoint ports are attached and its operator domain is not cut.
* **interchange (交接域)** – the place where two operators' links meet; a
  distinct fault domain.  Two hops are physically contiguous only if the
  previous hop's egress port and the next hop's ingress port are both members
  of the same interchange.
* **lease (租线)** – an ordered list of ``Hop`` objects describing a path.
* **cut** – one fault injected at one domain.

The station never receives this object directly; services read from it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Tuple


@dataclass(frozen=True)
class Port:
    """A physical access port belonging to one operator."""

    port_id: str
    operator: str

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return f"{self.operator}:{self.port_id}"


@dataclass
class Link:
    """A port-to-port adjacency inside a single operator."""

    link_id: str
    operator: str
    a: str
    b: str

    def ports(self) -> Tuple[str, str]:
        return self.a, self.b

    def other_end(self, port_id: str) -> Optional[str]:
        if port_id == self.a:
            return self.b
        if port_id == self.b:
            return self.a
        return None


@dataclass(frozen=True)
class Hop:
    """One leased hop: a named link owned by one operator."""

    operator: str
    link_id: str

    def key(self) -> Tuple[str, str]:
        return (self.operator, self.link_id)


@dataclass(frozen=True)
class Lease:
    """A leased path made of contiguous hops."""

    lease_id: str
    hops: Tuple[Hop, ...]
    valid_from: int = 0
    valid_until: int = 10**9  # contract expiry, in world epochs

    def links(self) -> List[Tuple[str, str]]:
        return [h.key() for h in self.hops]

    def is_valid_at(self, epoch: int) -> bool:
        return self.valid_from <= epoch <= self.valid_until


@dataclass
class Cut:
    """A single fault injected at a domain."""

    domain: str
    reason: str = ""


@dataclass
class Interchange:
    """An interchange fault domain; members are (operator, port_id) tuples."""

    interchange_id: str
    operated_by: str
    members: List[Tuple[str, str]] = field(default_factory=list)
    note: str = ""

    def contains(self, operator: str, port_id: str) -> bool:
        return (operator, port_id) in self.members

    def add(self, operator: str, port_id: str) -> None:
        if (operator, port_id) not in self.members:
            self.members.append((operator, port_id))

    def remove(self, operator: str, port_id: str) -> None:
        if (operator, port_id) in self.members:
            self.members.remove((operator, port_id))


@dataclass(frozen=True)
class PathView:
    """A fully resolved path with the material a verifier/station reasons over."""

    lease_id: str
    hops: Tuple[Hop, ...]
    detail: Tuple[Tuple[str, str, str], ...]  # (operator, enter_port, exit_port)
    ingress: str
    egress: str
    interchanges: Tuple[str, ...]
    domains: FrozenSet[str]

    def commit_payload(self) -> dict:
        return {
            "lease_id": self.lease_id,
            "hops": [list(h.key()) for h in self.hops],
            "detail": [list(d) for d in self.detail],
            "ingress": self.ingress,
            "egress": self.egress,
            "interchanges": list(self.interchanges),
        }


class Dispatcher:
    """The shared physical model.  Services read from it; the station never does."""

    def __init__(self) -> None:
        self.ports: Dict[str, Port] = {}
        self.links: Dict[Tuple[str, str], Link] = {}
        self.interchanges: Dict[str, Interchange] = {}
        self.operator_domain: Dict[str, str] = {}
        # interchange_id -> fault domain it actually belongs to (identity by
        # default).  Two differently-named interchanges can be the *same*
        # physical fault domain; aliasing them is the "single interchange
        # change" that makes two paths share a domain.
        self.interchange_domain: Dict[str, str] = {}

    # ---- construction -------------------------------------------------

    def add_operator(self, operator: str, domain: Optional[str] = None) -> None:
        self.operator_domain[operator] = domain or operator

    def alias_interchange(self, interchange_id: str, fault_domain: str) -> None:
        """Declare that ``interchange_id`` lives in ``fault_domain``."""
        self.interchange_domain[interchange_id] = fault_domain

    def unalias_interchange(self, interchange_id: str) -> None:
        self.interchange_domain.pop(interchange_id, None)

    def fault_domain_of_interchange(self, interchange_id: str) -> str:
        return self.interchange_domain.get(interchange_id, interchange_id)

    def add_port(self, operator: str, port_id: str) -> Port:
        port = Port(port_id=port_id, operator=operator)
        self.ports[port_id] = port
        self.add_operator(operator)
        return port

    def add_link(self, link_id: str, operator: str, a: str, b: str) -> Link:
        link = Link(link_id=link_id, operator=operator, a=a, b=b)
        self.links[(operator, link_id)] = link
        for p in (a, b):
            if p not in self.ports:
                self.add_port(operator, p)
        return link

    def add_interchange(
        self, interchange_id: str, operated_by: str,
        members: List[Tuple[str, str]], note: str = "",
    ) -> Interchange:
        ic = Interchange(interchange_id=interchange_id, operated_by=operated_by, note=note)
        for operator, port in members:
            ic.add(operator, port)
        self.interchanges[interchange_id] = ic
        self.add_operator(operated_by)
        return ic

    # ---- lookups ------------------------------------------------------

    def link(self, operator: str, link_id: str) -> Link:
        try:
            return self.links[(operator, link_id)]
        except KeyError:
            raise KeyError(f"unknown link {operator}:{link_id}") from None

    def interchange_for_pair(
        self, end_a: Tuple[str, str], end_b: Tuple[str, str]
    ) -> List[str]:
        out = []
        for ic in self.interchanges.values():
            if ic.contains(*end_a) and ic.contains(*end_b):
                out.append(ic.interchange_id)
        return sorted(out)

    def _join_interchange(self, prev_hop: Hop, prev_exit: str, hop: Hop, enter: str) -> Optional[str]:
        ics = self.interchange_for_pair((prev_hop.operator, prev_exit), (hop.operator, enter))
        return ics[0] if ics else None

    def domain_of_operator(self, operator: str) -> str:
        return self.operator_domain.get(operator, operator)

    # ---- resolution ---------------------------------------------------

    def resolve(self, lease: Lease) -> PathView:
        """Resolve a lease to concrete ports/interchanges.

        The first hop is entered at its ``a`` end and left at ``b``; each later
        hop is entered at whichever end shares an interchange with the previous
        egress port.

        Raises ``ValueError`` for an empty path, unknown link, or non-contiguous
        hops.
        """
        if not lease.hops:
            raise ValueError(f"{lease.lease_id}: empty path")

        detail: List[Tuple[str, str, str]] = []
        interchanges: List[str] = []
        prev_hop: Optional[Hop] = None
        prev_exit: Optional[str] = None

        for hop in lease.hops:
            link = self.link(hop.operator, hop.link_id)
            if prev_hop is None:
                enter_port, exit_port = link.a, link.b
            else:
                enters = [
                    p for p in link.ports()
                    if self._join_interchange(prev_hop, prev_exit, hop, p) is not None
                ]
                if not enters:
                    raise ValueError(
                        f"{lease.lease_id}: hop {hop.key()} does not join "
                        f"{prev_hop.key()}@{prev_exit} at any interchange"
                    )
                enter_port = enters[0]
                exit_port = link.other_end(enter_port)
                ic = self._join_interchange(prev_hop, prev_exit, hop, enter_port)
                if ic not in interchanges:
                    interchanges.append(ic)
            detail.append((hop.operator, enter_port, exit_port))
            prev_hop, prev_exit = hop, exit_port

        domains = {self.domain_of_operator(h.operator) for h in lease.hops}
        domains.update(self.fault_domain_of_interchange(ic) for ic in interchanges)
        return PathView(
            lease_id=lease.lease_id,
            hops=lease.hops,
            detail=tuple(detail),
            ingress=detail[0][1],
            egress=detail[-1][2],
            interchanges=tuple(interchanges),
            domains=frozenset(domains),
        )

    # ---- cut evaluation ----------------------------------------------

    @staticmethod
    def surviving(views: List[PathView], cut: Cut) -> List[PathView]:
        return [v for v in views if cut.domain not in v.domains]
