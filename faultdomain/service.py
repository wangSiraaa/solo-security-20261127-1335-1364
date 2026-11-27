"""The line-evidence service (线路举证服务).

Exposes the two acceptable disclosure conventions behind one object and handles
the operational wrinkles the brief calls out:

* **late reports** – a domain has not yet exported its segment/adjacency detail,
* **port re-leasing** – a lease is re-issued on different links/ports,
* **contract expiry** – the current epoch has passed ``valid_until``.

The point of keeping the two approaches side by side is to preserve, explicitly,
what the station can verify on its own under each:

* export approach – the station assembles labels, recombines fragments offline,
  and *sees shared domains* (a disclosure cost);
* verifier approach – the station checks a signature and a port binding but
  never sees the internal shared relation, and cannot renew a stale proof.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .delivery import DeliveryEngine
from .export_service import ExportBundle, ExportStation, TopologyExportService
from .model import Lease
from .verifier_service import IsolationProof, ProofStation, TrustedVerifier
from .world import World


@dataclass
class ExportOutcome:
    bundle: ExportBundle
    station: ExportStation
    complete: bool
    reason: str = ""


class LineEvidenceService:
    def __init__(self, world: World, delivery: Optional[DeliveryEngine] = None) -> None:
        self.world = world
        self.delivery = delivery or DeliveryEngine(world)
        self.exporter = TopologyExportService(world)
        self.verifier: Optional[TrustedVerifier] = None

    # ---- approach A: per-domain export ---------------------------------

    def export(self, lease_ids: List[str]) -> ExportOutcome:
        bundle = self.exporter.export(lease_ids)
        station = ExportStation(bundle)
        complete = all(bundle.leases[l].complete for l in lease_ids)
        reason = "" if complete else "one or more domains have not reported yet"
        return ExportOutcome(bundle=bundle, station=station, complete=complete, reason=reason)

    def mark_late(self, operator_domain: str) -> None:
        self.exporter.mark_late(operator_domain)

    def report_now(self, operator_domain: str) -> None:
        self.exporter.report_now(operator_domain)

    # ---- approach B: trusted verifier ----------------------------------

    def verifier_setup(self, domains: List[str], lease_ids: List[str]) -> TrustedVerifier:
        self.verifier = TrustedVerifier(self.world)
        for dom in domains:
            self.verifier.receive(dom, lease_ids)
        return self.verifier

    def issue_proof(self, lease_a: str, lease_b: str) -> IsolationProof:
        if self.verifier is None:
            raise RuntimeError("verifier not set up")
        return self.verifier.issue(lease_a, lease_b)

    def proof_station(self) -> ProofStation:
        if self.verifier is None:
            raise RuntimeError("verifier not set up")
        return ProofStation(self.verifier.public_key())

    # ---- operational: port re-leasing ----------------------------------

    def rele_connect(
        self,
        lease_id: str,
        new_lease: Lease,
        domain_for_verifier: Optional[List[str]] = None,
    ) -> None:
        """Install ``new_lease`` under the same lease id (a re-lease/reroute).

        Any previously-issued export or proof is now stale: the concrete path,
        its ports and its commitment have changed.
        """
        self.world.reissue_lease(new_lease)
        if self.verifier is not None:
            self.verifier.refresh(list(self.world.leases.keys()), domain_for_verifier)

    # ---- operational: contract expiry ----------------------------------

    def advance(self, n: int) -> int:
        return self.world.tick(n)

    def contract_lapsed(self, lease_id: str, epoch: Optional[int] = None) -> bool:
        epoch = self.world.epoch if epoch is None else epoch
        lease = self.world.leases[lease_id]
        return not lease.is_valid_at(epoch)
