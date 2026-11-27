"""Approach A -- per-domain export of stable, pseudonymous segment labels and
adjacency witnesses.

Each real domain (operator or interchange) exports, for the leases the station
cares about, **stable but non-identifying** labels:

* ``segment_label``  – stable pseudonym for one operator link,
* ``domain_label``   – stable pseudonym for the operator fault domain,
* ``interchange_label`` – stable pseudonym for the interchange fault domain,
* adjacency witnesses that say "segment X meets segment Y at interchange Z".

Labels are stable across exports.  That is exactly what lets the station stitch
the two leased paths together on its own and *offline-recompute* other
combinations (e.g. splice a prefix of one path onto a suffix of the other).
It is also the disclosure cost: because the labels are stable and not
lease-scoped, exporting twice reveals which fault domains two leases share.

What the station CAN self-verify under this approach:
  * that the two assembled paths use no common segment / domain / interchange,
  * that the endpoints match the ports it actually plugged,
  * survival of a named domain cut (it has the concrete domain set),
  * recombination of path fragments, offline.

What it CANNOT verify: nothing is authority-signed -- it trusts the domains'
own exports, and the labels leak shared-domain structure over time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Optional, Tuple

from .crypto import H
from .model import Dispatcher, Lease
from .world import World

_LABEL_LEN = 12


def _plabel(prefix: str, obj) -> str:
    return f"{prefix}-{H(obj)[:_LABEL_LEN]}"


@dataclass(frozen=True)
class Segment:
    label: str
    domain_label: str
    # endpoint port ids, only populated where a station port is actually the end
    ingress_port: Optional[str] = None
    egress_port: Optional[str] = None


@dataclass(frozen=True)
class Adjacency:
    witness: str
    left_segment: str
    right_segment: str
    interchange_label: str


@dataclass
class LeaseExport:
    lease_id: str
    segments: Tuple[str, ...]
    complete: bool
    domain_set: FrozenSet[str]  # operator domain labels ∪ interchange labels
    src_port: Optional[str]
    dst_port: Optional[str]
    valid_from: int
    valid_until: int
    epoch: int


@dataclass
class ExportBundle:
    epoch: int
    leases: Dict[str, LeaseExport]
    segments: Dict[str, Segment]
    adjacencies: Dict[str, Adjacency]


@dataclass
class Assembly:
    ok: bool
    lease_id: str
    segments: Tuple[str, ...] = ()
    interchanges: Tuple[str, ...] = ()
    domain_set: FrozenSet[str] = frozenset()
    reason: str = ""


class TopologyExportService:
    """Domain-side exporter.  Domains can be 'late' (not yet reported)."""

    def __init__(self, world: World) -> None:
        self.world = world
        self.dispatcher: Dispatcher = world.dispatcher
        self.late_domains: set = set()  # operator domains that have not reported

    # ---- operator control --------------------------------------------

    def mark_late(self, operator_domain: str) -> None:
        self.late_domains.add(operator_domain)

    def report_now(self, operator_domain: str) -> None:
        self.late_domains.discard(operator_domain)

    # ---- label helpers -------------------------------------------------

    def _domain_label(self, domain: str) -> str:
        return _plabel("D", {"domain": domain})

    def _interchange_label(self, interchange_id: str) -> str:
        # Labels are by *fault domain*, not by physical interchange name.  Two
        # differently-named interchanges that actually share a fault domain
        # therefore export the same label -- which is how a repeated export lets
        # the station see that two leases share a domain.
        fault_domain = self.dispatcher.fault_domain_of_interchange(interchange_id)
        return _plabel("X", {"fault_domain": fault_domain})

    def _segment_label(self, operator: str, link_id: str) -> str:
        return _plabel("S", {"operator": operator, "link": link_id})

    def _adj_witness(self, a: str, b: str, ic_label: str) -> str:
        return _plabel("A", {"left": a, "right": b, "at": ic_label})

    # ---- export -------------------------------------------------------
    def export(self, lease_ids: List[str]) -> ExportBundle:
        segments: Dict[str, Segment] = {}
        adjacencies: Dict[str, Adjacency] = {}
        leases: Dict[str, LeaseExport] = {}

        for lease_id in lease_ids:
            lease = self.world.leases[lease_id]
            view = self.dispatcher.resolve(lease)

            seg_labels: List[str] = []
            domain_set: set = set()
            complete = True

            for idx, (hop, (op, enter, exit_)) in enumerate(zip(lease.hops, view.detail)):
                op_domain = self.dispatcher.domain_of_operator(hop.operator)
                if op_domain in self.late_domains:
                    # This domain has not reported its segment yet.
                    complete = False
                    seg_labels.append(_plabel("gap", {"lease": lease_id, "hop": idx}))
                    continue
                seg_label = self._segment_label(hop.operator, hop.link_id)
                ingress = None
                egress = None
                if idx == 0:
                    ingress = enter  # station's source port (real handle)
                if idx == len(lease.hops) - 1:
                    egress = exit_  # station's destination port (real handle)
                segments[seg_label] = Segment(
                    label=seg_label,
                    domain_label=self._domain_label(op_domain),
                    ingress_port=ingress,
                    egress_port=egress,
                )
                domain_set.add(self._domain_label(op_domain))
                seg_labels.append(seg_label)

            # interchanges + adjacency witnesses (only for consecutive reported
            # segments; a gap suppresses the witness so station sees a break)
            for idx in range(len(lease.hops) - 1):
                left = seg_labels[idx]
                right = seg_labels[idx + 1]
                if left.startswith("gap-") or right.startswith("gap-"):
                    continue
                ic_id = view.interchanges[idx]
                ic_label = self._interchange_label(ic_id)
                witness = self._adj_witness(left, right, ic_label)
                adjacencies[witness] = Adjacency(
                    witness=witness,
                    left_segment=left,
                    right_segment=right,
                    interchange_label=ic_label,
                )
                domain_set.add(ic_label)

            src = view.detail[0][1]
            dst = view.detail[-1][2]
            leases[lease_id] = LeaseExport(
                lease_id=lease_id,
                segments=tuple(seg_labels),
                complete=complete,
                domain_set=frozenset(domain_set),
                src_port=src,
                dst_port=dst,
                valid_from=lease.valid_from,
                valid_until=lease.valid_until,
                epoch=self.world.epoch,
            )

        return ExportBundle(
            epoch=self.world.epoch,
            leases=leases,
            segments=segments,
            adjacencies=adjacencies,
        )


class ExportStation:
    """The liaison station reasoning over an export bundle.

    It assembles paths itself, offline-recombines fragments, checks disjointness
    and cut survival.  It never holds a real domain name, only stable labels.
    """

    def __init__(self, bundle: ExportBundle) -> None:
        self.bundle = bundle

    # ---- assembly -----------------------------------------------------

    def assemble(self, lease_id: str) -> Assembly:
        le = self.bundle.leases.get(lease_id)
        if le is None:
            return Assembly(False, lease_id, reason="no export for lease")
        if not le.complete:
            return Assembly(False, lease_id, reason="export incomplete (late domain report)")
        # every consecutive pair must have a matching adjacency witness
        interchanges: List[str] = []
        for idx in range(len(le.segments) - 1):
            left, right = le.segments[idx], le.segments[idx + 1]
            match = [
                a for a in self.bundle.adjacencies.values()
                if a.left_segment == left and a.right_segment == right
            ]
            if not match:
                return Assembly(
                    False, lease_id,
                    reason=f"no adjacency witness between {left} and {right}",
                )
            interchanges.append(match[0].interchange_label)
        return Assembly(
            ok=True,
            lease_id=lease_id,
            segments=le.segments,
            interchanges=tuple(interchanges),
            domain_set=le.domain_set,
        )

    # ---- contract validity -------------------------------------------

    def contract_active(self, lease_id: str, epoch: int) -> bool:
        le = self.bundle.leases[lease_id]
        return le.valid_from <= epoch <= le.valid_until

    # ---- disjointness -------------------------------------------------

    def disjoint(self, lease_a: str, lease_b: str) -> Tuple[bool, str]:
        """True iff the two paths share no segment, domain, or interchange."""
        aa = self.assemble(lease_a)
        bb = self.assemble(lease_b)
        if not aa.ok or not bb.ok:
            return False, f"cannot assemble: {aa.reason or bb.reason}"
        shared_seg = set(aa.segments) & set(bb.segments)
        if shared_seg:
            return False, f"shared segment label(s): {sorted(shared_seg)}"
        shared_dom = set(aa.domain_set) & set(bb.domain_set)
        if shared_dom:
            # This is the disclosure of approach A: the station can *see* it.
            return False, f"shared fault domain label(s): {sorted(shared_dom)}"
        return True, "disjoint"

    def shared_domains(self, lease_a: str, lease_b: str) -> List[str]:
        """Used for the 'repeated export reveals shared domain' demonstration."""
        aa = self.bundle.leases[lease_a]
        bb = self.bundle.leases[lease_b]
        return sorted(aa.domain_set & bb.domain_set)

    # ---- offline recombination ---------------------------------------

    def recombine(self, lease_a: str, lease_b: str, cut_index_a: int) -> Tuple[str, ...]:
        """Splice ``a[:cut_index_a] + b[cut_index_a:]`` and return the label chain.

        Purely offline: uses only the already-exported labels and witnesses to
        test whether an alternative combination would have an adjacency break.
        """
        aa = self.assemble(lease_a)
        bb = self.assemble(lease_b)
        if not aa.ok or not bb.ok:
            raise ValueError("cannot recombine an unassembled path")
        suffix = bb.segments[cut_index_a:]
        return tuple(aa.segments[:cut_index_a]) + tuple(suffix)

    def recombination_is_contiguous(self, chain: Tuple[str, ...]) -> bool:
        for idx in range(len(chain) - 1):
            left, right = chain[idx], chain[idx + 1]
            if not any(
                a.left_segment == left and a.right_segment == right
                for a in self.bundle.adjacencies.values()
            ):
                return False
        return True

    # ---- actual connected ports --------------------------------------

    def binds_ports(self, lease_id: str, src_port: str, dst_port: str) -> bool:
        le = self.bundle.leases[lease_id]
        return le.src_port == src_port and le.dst_port == dst_port

    # ---- cut survival (station computes this itself) ------------------

    def survives_cut(self, lease_id: str, domain_label: str) -> bool:
        """Fictional/unknown labels are on nobody's path -> everything survives."""
        asm = self.assemble(lease_id)
        if not asm.ok:
            return False
        return domain_label not in asm.domain_set
