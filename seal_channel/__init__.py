"""受保护报讯信道的本地离线收发器。

模块不依赖第三方库，状态以原子写入的 JSON 保存，因此每次启动新的 CLI 进程
都等价于一次真实重启。
"""

from .core import (
    ALPHA,
    BETA,
    TRIP_LABELS,
    LocalTransceiver,
    PlanReport,
    Planner,
    ReceiveResult,
    SealCase,
    SealChannelError,
    SealTransceiver,
    Transceiver,
    Verification,
    normalize_trip,
    parse_seals,
)
from .planner import PlanReport, Planner

__all__ = [
    "ALPHA",
    "BETA",
    "TRIP_LABELS",
    "PlanReport",
    "Planner",
    "ReceiveResult",
    "SealCase",
    "SealChannelError",
    "SealTransceiver",
    "Transceiver",
    "LocalTransceiver",
    "Verification",
    "normalize_trip",
    "parse_seals",
]

__version__ = "1.0.0"
