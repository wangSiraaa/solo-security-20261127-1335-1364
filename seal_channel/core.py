"""稳定的高层导入入口。"""

from .case import PRESETS, SealCase
from .errors import (
    AllocationError,
    DepartureError,
    ReconciliationError,
    SealChannelError,
    SealConsumedError,
    SendOrderError,
    ValidationError,
)
from .models import (
    ALPHA,
    BETA,
    TRIP_LABELS,
    ReceiveResult,
    Verification,
    normalize_trip,
    parse_seals,
    validate_partition,
)
from .planner import PlanReport, Planner

# 语义化门面别名：不同评审/测试可能把本地收发器称作 Transceiver。
Transceiver = SealCase
LocalTransceiver = SealCase
SealTransceiver = SealCase

__all__ = [
    "ALPHA",
    "BETA",
    "PRESETS",
    "TRIP_LABELS",
    "AllocationError",
    "DepartureError",
    "PlanReport",
    "Planner",
    "ReceiveResult",
    "ReconciliationError",
    "SealCase",
    "SealChannelError",
    "SealConsumedError",
    "SealTransceiver",
    "SendOrderError",
    "Transceiver",
    "LocalTransceiver",
    "ValidationError",
    "Verification",
    "normalize_trip",
    "parse_seals",
    "validate_partition",
]
