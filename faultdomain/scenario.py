"""Scenario construction and narrative for the exercise.

Topology (all names symbolic):

* Operators ``OP-A``/``OP-B`` run the primary links; ``OP-C``/``OP-D`` run the
  backup links, so the two paths are on *different operational units*.  A fifth
  operator, ``OP-X``, runs both interchanges -- which is the trap: the names
  differ and the units differ, yet the hand-over can turn out to be one domain.

* The liaison station's pair of paths:

  - **primary (lease P)**:  SRC --A1--> [IC-EAST] --B2--> DST
        hop OP-A:A1 (src port -> pA1), interchange IC-EAST, hop OP-B:B2 (pB2 -> dst)
  - **backup  (lease Q)**:  SRC --C3--> [IC-WEST] --D4--> DST
        hop OP-C:C3 (src2 port -> pC3), interchange IC-WEST, hop OP-D:D4 (pD4 -> dst2)

  Both interchanges are run by OP-X but are **different fault domains** while
  the drill starts (``IC-EAST`` and ``IC-WEST``), so P and Q are disjoint on
  every operator domain *and* interchange.

* The single "interchange change" that breaks isolation (``只更改一处交接``):
  the backup's hand-over (``IC-WEST``) is revealed/rewired to sit inside the
  primary's fault domain (``IC-EAST``).  One edit, one interchange -- yet now
  both paths cross that single domain: a cut there kills both, disjointness
  fails, and you can no longer promise reachability through that domain's
  outage.  (``route_backup_through_primary``.)

The station's access ports:
  SRC has primary ``pa-src`` (on A1) and backup ``pa-src-2`` (on C3).
  DST has primary ``pb-dst`` (on B2) and backup ``pb-dst-2`` (on D4).
The "backup detached" step detaches ``pb-dst-2``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .model import Dispatcher, Hop, Lease
from .world import World


@dataclass
class Scenario:
    world: World
    leases: dict
    station_ports: dict  # terminal -> {role: (port_id, lease_id)}

    def lease(self, lid: str) -> Lease:
        return self.world.leases[lid]


def build() -> Scenario:
    d = Dispatcher()

    # --- operators & domains ----------------------------------------------
    d.add_operator("OP-A")
    d.add_operator("OP-B")
    d.add_operator("OP-C")
    d.add_operator("OP-D")
    d.add_operator("OP-X")  # runs the two interchanges

    # --- access ports on the two terminals -------------------------------
    # Primary path uses: A1.a = pa-src, A1.b = pA1 ; B2.a = pB2, B2.b = pb-dst
    d.add_link("A1", "OP-A", "pa-src", "pA1")
    d.add_link("B2", "OP-B", "pB2", "pb-dst")

    # Backup path uses: C3.a = pa-src-2, C3.b = pC3 ; D4.a = pD4, D4.b = pb-dst-2
    d.add_link("C3", "OP-C", "pa-src-2", "pC3")
    d.add_link("D4", "OP-D", "pD4", "pb-dst-2")

    # --- interchanges ------------------------------------------------------
    # IC-EAST joins OP-A's pA1 with OP-B's pB2 (primary).
    d.add_interchange(
        "IC-EAST", operated_by="OP-X",
        members=[("OP-A", "pA1"), ("OP-B", "pB2")],
        note="primary hand-over, run by OP-X",
    )
    # IC-WEST joins OP-C's pC3 with OP-D's pD4 (backup).
    d.add_interchange(
        "IC-WEST", operated_by="OP-X",
        members=[("OP-C", "pC3"), ("OP-D", "pD4")],
        note="backup hand-over, run by OP-X",
    )

    # --- world -------------------------------------------------------------
    w = World(d)
    w.add_terminal("SRC", operator="OP-A", ports=["pa-src", "pa-src-2"])
    w.add_terminal("DST", operator="OP-B", ports=["pb-dst", "pb-dst-2"])

    primary = Lease(
        lease_id="L-PRIMARY",
        hops=(Hop("OP-A", "A1"), Hop("OP-B", "B2")),
        valid_from=0,
        valid_until=1000,
    )
    backup = Lease(
        lease_id="L-BACKUP",
        hops=(Hop("OP-C", "C3"), Hop("OP-D", "D4")),
        valid_from=0,
        valid_until=1000,
    )
    w.add_lease(primary)
    w.add_lease(backup)

    station_ports = {
        "SRC": {"primary": ("pa-src", "L-PRIMARY"), "backup": ("pa-src-2", "L-BACKUP")},
        "DST": {"primary": ("pb-dst", "L-PRIMARY"), "backup": ("pb-dst-2", "L-BACKUP")},
    }

    return Scenario(world=w, leases={"L-PRIMARY": primary, "L-BACKUP": backup},
                    station_ports=station_ports)


def route_backup_through_primary(d: Dispatcher, domain: str = "IC-EAST") -> None:
    """The single interchange change: put the backup hand-over in the primary's
    fault domain.  After this, both leased paths cross one and the same domain."""
    d.alias_interchange("IC-WEST", d.fault_domain_of_interchange(domain))


def unroute_backup(d: Dispatcher) -> None:
    d.unalias_interchange("IC-WEST")


def unmerge_interchanges(d: Dispatcher) -> None:
    d.unalias_interchange("IC-EAST")
    d.unalias_interchange("IC-WEST")


# Backwards-compatible alias used by earlier drafts / callers.
def merge_interchanges_into_one_domain(d: Dispatcher, domain: str = "X-HUB") -> None:
    d.alias_interchange("IC-EAST", domain)
    d.alias_interchange("IC-WEST", domain)
