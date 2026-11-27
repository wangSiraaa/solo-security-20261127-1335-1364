"""Physical reserve pools of prefabricated practice bits.

A reserve is a *pool* keyed by ``(source_id, epoch)``.  Two declarations that
name the same key share the same pool -- that is the whole point of the
"same noise box" scenario.  Taking material advances a cursor: the taken range
is spent and the leftover fragment stays in the pool.

Material is deterministic from ``(source, epoch, offset)`` so that an idempotent
re-issue of the same order reproduces byte-identical material.  None of this is
a real secret.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Dict, Iterable, Tuple

BatchKey = Tuple[str, int]  # (source_id, epoch)


def _expand(seed: bytes, offset: int, nbytes: int) -> bytes:
    """Deterministic prefabricated bit string for a slice of a reserve."""
    out = bytearray()
    block = 0
    while len(out) < nbytes:
        h = hashlib.sha256(
            seed
            + b"|"
            + offset.to_bytes(8, "big")
            + b"|"
            + block.to_bytes(8, "big")
        ).digest()
        out += h
        block += 1
    return bytes(out[:nbytes])


@dataclass
class Reserve:
    source_id: str
    epoch: int
    total_bytes: int
    _cursor: int = 0

    @property
    def key(self) -> BatchKey:
        return (self.source_id, self.epoch)

    @property
    def seed(self) -> bytes:
        return hashlib.sha256(
            f"RESERVE|{self.source_id}|{self.epoch}".encode()
        ).digest()

    def live(self) -> int:
        return self.total_bytes - self._cursor

    def take(self, nbytes: int, *, dry_run: bool = False) -> Tuple[int, bytes]:
        """Take ``nbytes`` from the pool, returning ``(offset, material)``.

        ``dry_run`` computes the slice without advancing the cursor (used to
        price an order before committing).
        """
        if nbytes > self.live():
            raise ValueError(
                f"reserve {self.key} has {self.live()} bytes, need {nbytes}"
            )
        offset = self._cursor
        material = _expand(self.seed, offset, nbytes)
        if not dry_run:
            self._cursor += nbytes
        return offset, material

    def reset(self) -> None:
        self._cursor = 0

    def material_at(self, offset: int, nbytes: int) -> bytes:
        return _expand(self.seed, offset, nbytes)


class ReserveBook:
    """All physical reserves the bench actually holds."""

    def __init__(self) -> None:
        self._reserves: Dict[BatchKey, Reserve] = {}

    def add(self, source_id: str, epoch: int, total_bytes: int) -> Reserve:
        key = (source_id, epoch)
        if key in self._reserves:
            raise ValueError(f"duplicate reserve {key}")
        r = Reserve(source_id=source_id, epoch=epoch, total_bytes=total_bytes)
        self._reserves[key] = r
        return r

    def get(self, key: BatchKey) -> Reserve | None:
        return self._reserves.get(key)

    def has(self, key: BatchKey) -> bool:
        return key in self._reserves

    def keys(self) -> Iterable[BatchKey]:
        return self._reserves.keys()

    def all(self) -> Iterable[Reserve]:
        return self._reserves.values()

    def reset(self) -> None:
        for r in self._reserves.values():
            r.reset()

    def clone(self) -> "ReserveBook":
        """Copy the book including current cursors (a point-in-time snapshot)."""
        out = ReserveBook()
        for r in self._reserves.values():
            out._reserves[r.key] = Reserve(
                source_id=r.source_id,
                epoch=r.epoch,
                total_bytes=r.total_bytes,
                _cursor=r._cursor,
            )
        return out
