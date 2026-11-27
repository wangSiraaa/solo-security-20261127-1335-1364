"""The bench's combiner contract.

Given a set of equal-length material paths, one of which carries an
*independent-uniform guarantee for the remaining inputs*, the combiner returns
an output with that same guarantee.  Paths without a guarantee may be included
as long as they cannot be the sole source of independence.

Concrete, defensive instantiation of the contract:

* ``HEAD`` -- the one path whose material is guaranteed independent-uniform of
  the others.  Its guarantee does not come from the routed reserves; it is the
  path's own declared property.  Even if the other paths are all the same
  source (or are wheeled in from a synthetic declaration), the output keeps
  HEAD's guarantee.
* Dependent paths are XORed in and *also* absorbed so the output is never just
  a copy of HEAD.  (We mix in a tag derived from all path identities.)

The combinator is purely structural; it makes no cryptographic security claim
and its output is practice material only.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Sequence, Tuple

# A path is (independence-guaranteed?, material bytes)
CombinablePath = Tuple[bool, bytes]


@dataclass(frozen=True)
class CombinerContract:
    """Written form of the bench combiner contract for a receiving lab."""

    text: str = (
        "Combiner contract (bench): take N equal-length material paths and XOR "
        "them. The output carries an independence/uniformity guarantee exactly "
        "when at least one path is guaranteed independent-uniform of all the "
        "others. Paths with no such guarantee may be present, but they never "
        "supply the guarantee themselves. The output is practice material, not "
        "a real key."
    )

    @staticmethod
    def require_equal_lengths(paths: Sequence[CombinablePath]) -> int:
        lens = {len(m) for _, m in paths}
        if len(lens) != 1:
            raise ValueError(f"combiner paths must be equal length, got {sorted(lens)}")
        n = lens.pop()
        if n == 0:
            raise ValueError("combiner paths must be non-empty")
        return n

    @staticmethod
    def combine(paths: Sequence[CombinablePath]) -> bytes:
        n = CombinerContract.require_equal_lengths(paths)
        if not any(guaranteed for guaranteed, _ in paths):
            raise ValueError(
                "combiner contract needs at least one independent-uniform path"
            )
        # XOR all paths together (dependent paths included, never the sole
        # source of independence).
        acc = bytearray(n)
        for _, material in paths:
            for i in range(n):
                acc[i] ^= material[i]
        # Absorb a tag binding all path roles so the output is not merely the
        # guaranteed path verbatim. Pure domain separation, not a proof.
        tag = hashlib.sha256(
            b"|".join(b"1" if g else b"0" for g, _ in paths)
        ).digest()
        for i in range(n):
            acc[i] ^= tag[i % len(tag)]
        return bytes(acc)
