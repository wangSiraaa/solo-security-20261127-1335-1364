"""Split-fault-domain evidence service + rehearsal desk.

Public surface:

* ``scenario.build()``            – construct the demo topology
* ``LineEvidenceService``         – the 线路举证服务 (both disclosure approaches)
* ``Desk`` / ``Evidence``         – the 演练台 (rehearsal desk)
* ``DeliveryEngine``              – physical probe-ticket delivery + per-domain logs
"""

from .desk import ActuallyConnected, Desk, Evidence
from .delivery import DeliveryEngine, DeliveryResult, new_ticket_id
from .export_service import ExportStation, TopologyExportService
from .scenario import (
    Scenario,
    build,
    merge_interchanges_into_one_domain,
    route_backup_through_primary,
    unmerge_interchanges,
    unroute_backup,
)
from .service import LineEvidenceService
from .verifier_service import IsolationProof, ProofStation, TrustedVerifier
from .world import HandlingRecord, World

__all__ = [
    "ActuallyConnected",
    "Desk",
    "Evidence",
    "DeliveryEngine",
    "DeliveryResult",
    "new_ticket_id",
    "ExportStation",
    "TopologyExportService",
    "Scenario",
    "build",
    "merge_interchanges_into_one_domain",
    "route_backup_through_primary",
    "unmerge_interchanges",
    "unroute_backup",
    "LineEvidenceService",
    "IsolationProof",
    "ProofStation",
    "TrustedVerifier",
    "HandlingRecord",
    "World",
]
