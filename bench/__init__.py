"""Random practice-material issuance bench (teaching-lab simulator).

Everything here is a *practice* system: the only randomness it consumes is
prefabricated bit strings, and the only devices it knows about are synthetic
declarations.  It never produces or stores a real key.

Top-level workflow lives in :class:`bench.engine.IssuanceEngine`; the written
contract for the receiving lab is ``docs/CHARTER.md``.
"""

from .combiner import CombinablePath, CombinerContract
from .content import ChassisDeclaration, DeclarationBook, Feed
from .engine import IssuanceEngine, IssuanceError, Issued, OrderRecord, PoolKey, PoolStatus
from .ledger import Draw, IndependenceLedger
from .reserve import BatchKey, Reserve, ReserveBook
from .device import Checkpoint, ScenarioRunner, run_narrative, standard_bench

__all__ = [
    "CombinerContract",
    "CombinablePath",
    "ChassisDeclaration",
    "DeclarationBook",
    "Feed",
    "IssuanceEngine",
    "IssuanceError",
    "Issued",
    "OrderRecord",
    "PoolKey",
    "PoolStatus",
    "Draw",
    "IndependenceLedger",
    "BatchKey",
    "Reserve",
    "ReserveBook",
    "Checkpoint",
    "ScenarioRunner",
    "run_narrative",
    "standard_bench",
]
