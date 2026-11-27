"""Fictional semantic-version parsing and range checks.

The exercise deliberately uses paper versions such as ``1.4.0-fictional``;
nothing here compares against real vendor releases.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List

_VERSION_RE = re.compile(
    r"^\s*(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[-+][0-9A-Za-z.-]+)?\s*$"
)


@dataclass(frozen=True)
class Version:
    major: int
    minor: int = 0
    patch: int = 0
    text: str = ""

    @classmethod
    def parse(cls, value: str | "Version") -> "Version":
        if isinstance(value, Version):
            return value
        match = _VERSION_RE.match(value)
        if not match:
            raise ValueError(f"unsupported fictional version: {value!r}")
        major, minor, patch = (int(g or 0) for g in match.groups())
        return cls(major, minor, patch, value)

    @property
    def tuple(self) -> tuple[int, int, int]:
        return (self.major, self.minor, self.patch)

    def __str__(self) -> str:
        return self.text or f"{self.major}.{self.minor}.{self.patch}"

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self.tuple < other.tuple

    def __le__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self.tuple <= other.tuple

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self.tuple > other.tuple

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self.tuple >= other.tuple

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Version):
            return self.tuple == other.tuple
        if isinstance(other, str):
            try:
                return self.tuple == Version.parse(other).tuple
            except ValueError:
                return False
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.tuple)


_OPS = {
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
    "==": lambda a, b: a == b,
    "=": lambda a, b: a == b,
}


@dataclass(frozen=True)
class VersionConstraint:
    op: str
    version: Version

    @classmethod
    def parse(cls, text: str) -> "VersionConstraint":
        text = text.strip()
        for op in (">=", "<=", "==", "=", ">", "<"):
            if text.startswith(op):
                raw = text[len(op) :].strip()
                return cls("==" if op == "=" else op, Version.parse(raw))
        # A bare version means exactly that version.
        return cls("==", Version.parse(text))

    def matches(self, value: str | Version) -> bool:
        return _OPS[self.op](Version.parse(value), self.version)


def parse_range(expr: str | Iterable[str]) -> List[VersionConstraint]:
    if isinstance(expr, str):
        parts = [p.strip() for p in expr.split(",") if p.strip()]
    else:
        parts = list(expr)
    return [VersionConstraint.parse(p) for p in parts]


def version_in_range(value: str | Version, expr: str | Iterable[str]) -> bool:
    constraints = parse_range(expr)
    return all(c.matches(value) for c in constraints)
