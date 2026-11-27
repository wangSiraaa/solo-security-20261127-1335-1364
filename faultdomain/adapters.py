"""Adapters that turn each disclosure approach into what the rehearsal desk sees.

This is the heart of "保留站方在所选办法下究竟能自行核实什么":

* Under the **export** approach the station recomputes disjointness and cut
  survival itself from stable labels, but everything is trusted-as-reported and
  the labels reveal shared domains.  ``fresh`` is irrelevant: each export is new.

* Under the **verifier** approach the station checks a signature, the port
  binding, freshness and the contract window; it cannot see *why* the paths are
  disjoint, and it cannot renew the proof.  ``disjoint_ok`` is whatever the
  signed proof asserts, and only ``disjoint=True`` proofs are usable.

Both adapters also attach a *physical* probe built from the live world, so the
desk's cut-survival and detached-port decisions rest on real delivery, not on
paperwork.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from .delivery import DeliveryEngine, new_ticket_id
from .desk import Evidence, Probe
from .export_service import ExportStation
from .model import Lease
from .verifier_service import IsolationProof, ProofStation
from .world import World


# ---------------------------------------------------------------------------
# Physical probe: send a throwaway ticket and read the destination's own log.
# ---------------------------------------------------------------------------

def make_probe(
    world: World,
    engine: DeliveryEngine,
    dest_terminal: str,
    paths: List[Tuple[str, str, str, Lease]],  # (src_port, dst_port, src_t, lease)
) -> Probe:
    """A probe that returns ``(ok, reason)`` for path index ``idx``.

    Delivery writes handling records into each real domain's log.  We only trust
    a *successful delivery* here; the desk itself checks port attachment, so the
    probe focuses on domain/interchange cuts along the real path.
    """

    def probe(idx: int) -> Tuple[bool, str]:
        src_port, dst_port, src_term, lease = paths[idx]
        ticket = new_ticket_id()
        res = engine.deliver(
            ticket, src_term, dest_terminal, lease, src_port, dst_port
        )
        if res.ok:
            return True, f"ticket {ticket} delivered"
        return False, res.reason

    return probe


# ---------------------------------------------------------------------------
# Approach A: export / station self-assembly
# ---------------------------------------------------------------------------

def evidence_from_export(
    station: ExportStation,
    lease_a: str,
    lease_b: str,
    current_epoch: int,
    cut_domain_label: Optional[str],
    attached_ports: Tuple[Tuple[str, str], Tuple[str, str]],
) -> Evidence:
    """Build desk evidence from the station's own label-level reasoning."""
    notes: List[str] = []

    disjoint, why = station.disjoint(lease_a, lease_b)
    notes.append(f"station self-check: {why}")

    asm_a = station.assemble(lease_a)
    asm_b = station.assemble(lease_b)
    path_ports = [
        (attached_ports[0][0], attached_ports[0][1]),
        (attached_ports[1][0], attached_ports[1][1]),
    ]

    # The station can itself test cut survival per real domain label it holds.
    survives: List[bool] = []
    for asm in (asm_a, asm_b):
        if cut_domain_label is None:
            survives.append(True)
        elif not asm.ok:
            survives.append(False)
        else:
            survives.append(cut_domain_label not in asm.domain_set)
    if cut_domain_label is not None:
        # If the cut label is not one the station has ever seen, it is a
        # fictional/foreign domain: nothing on the known paths uses it, so both
        # paths survive it (as far as the station can tell).
        if not station.bundle.leases[lease_a].complete or not station.bundle.leases[lease_b].complete:
            notes.append("an export is incomplete; survival is uncertain")

    # Contract expiry: station has valid_from/valid_until in the export.
    contract_ok = (
        station.contract_active(lease_a, current_epoch)
        and station.contract_active(lease_b, current_epoch)
    )
    if not contract_ok:
        notes.append("contract window does not cover current epoch")

    return Evidence(
        source="export",
        disjoint_ok=disjoint and contract_ok,
        path_ports=path_ports,
        path_survives=survives,
        fresh=None,
        contract_active=contract_ok,
        signature_ok=None,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Approach B: verifier proof / station verification
# ---------------------------------------------------------------------------

def evidence_from_proof(
    station: ProofStation,
    proof: IsolationProof,
    current_epoch: int,
    attached_ports: Tuple[Tuple[str, str], Tuple[str, str]],
    path_survives: Optional[List[bool]] = None,
) -> Evidence:
    """Build desk evidence from a signed isolation proof.

    The station can verify: signature, structural 'disjoint' claim, port
    binding, freshness, contract window.  It cannot recompute the path, so
    ``path_survives`` (if any) must come from physical probes, not paperwork.
    """
    notes: List[str] = []

    sig_ok, sig_why = station.verify_structural(proof)
    notes.append(f"signature/claim: {sig_why}")

    bind_ok, bind_why = station.binds_actual_paths(
        proof,
        attached_ports[0][0], attached_ports[0][1],
        attached_ports[1][0], attached_ports[1][1],
    )
    notes.append(f"port binding: {bind_why}")

    fresh_ok, fresh_why = station.fresh_for_epoch(proof, current_epoch)
    notes.append(f"freshness: {fresh_why}")

    contract_ok, contract_why = station.contract_active(proof, current_epoch)
    notes.append(f"contract: {contract_why}")

    disjoint_ok = sig_ok and bind_ok and proof.disjoint

    return Evidence(
        source="verifier",
        disjoint_ok=disjoint_ok,
        path_ports=[
            (proof.ingress_a, proof.egress_a),
            (proof.ingress_b, proof.egress_b),
        ],
        path_survives=path_survives,
        fresh=fresh_ok,
        contract_active=contract_ok,
        signature_ok=sig_ok,
        notes=notes,
    )
