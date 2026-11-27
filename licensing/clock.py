"""Simulated clock.

Every component takes its notion of "now" from a single SimClock so tests and
the demo are fully deterministic. No wall-clock time is ever consulted.
"""
from __future__ import annotations


class SimClock:
    def __init__(self, start: int = 0) -> None:
        self._t = int(start)

    def now(self) -> int:
        return self._t

    def advance(self, dt: int) -> int:
        if dt < 0:
            raise ValueError("clock cannot move backwards in this simulation")
        self._t += int(dt)
        return self._t

    def set(self, t: int) -> int:
        if t < self._t:
            raise ValueError("clock cannot move backwards in this simulation")
        self._t = int(t)
        return self._t
