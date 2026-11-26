"""Shared harness for the stdlib unittest suite (no third-party deps).

Each test gets a throw-away world: a desk + two halls whose directories live
under a per-test temp dir, one current event with choices, and four members.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ballotbox import Desk, Hall  # noqa: E402


class Clock:
    def __init__(self, t: int = 1000):
        self.t = t

    def __call__(self) -> int:
        return self.t

    def tick(self, dt: int = 1) -> int:
        self.t += dt
        return self.t


class World:
    def __init__(self, root: str):
        self.clock = Clock()
        self.root = root
        self.desk = Desk(os.path.join(root, "desk"), clock=self.clock)
        self.east = Hall("east", os.path.join(root, "halls", "east"),
                         online=True, clock=self.clock)
        self.west = Hall("west", os.path.join(root, "halls", "west"),
                         online=True, clock=self.clock)
        self.desk.register_hall(self.east)
        self.desk.register_hall(self.west)
        for m in ("A", "B", "C", "D"):
            self.desk.add_member(m)
        self.event = self.desk.register_event(
            "EV-2024", choices=("yes", "no", "abstain"), deadline=5000)

    def hall(self, name: str) -> Hall:
        return {"east": self.east, "west": self.west}[name]


class WorldTestCase(unittest.TestCase):
    """Base case that builds a fresh ``self.w`` world per test."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.w = World(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()


__all__ = ["Clock", "World", "WorldTestCase"]
