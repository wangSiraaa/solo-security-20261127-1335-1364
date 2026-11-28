"""Offline calibration-board startup inspection and deployment tool.

No hardware is required or touched: the board, its reset/clear/window control
sequences, and the calibration program are all simulated from a maintainer's
description.
"""

from __future__ import annotations

from .model import Board, Cell, SpecError
from .events import Event, ReadRequest
from .sim import Simulator, RunResult, ReadRecord
from .planner import (
    ResetSpec,
    Plan,
    allowed_interleavings,
    synthesize_plan,
    prove_fixed_tick,
    handshake_plan,
    build_run,
)
from .handshake import handshake_windows, HandshakeOutcome

__all__ = [
    "Board",
    "Cell",
    "SpecError",
    "Event",
    "ReadRequest",
    "Simulator",
    "RunResult",
    "ReadRecord",
    "ResetSpec",
    "Plan",
    "allowed_interleavings",
    "synthesize_plan",
    "prove_fixed_tick",
    "handshake_plan",
    "build_run",
    "handshake_windows",
    "HandshakeOutcome",
]
