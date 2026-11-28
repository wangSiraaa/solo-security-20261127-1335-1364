"""Deterministic interleaving of fragment traffic for tests and drills.

Two concurrency effects are modelled:

* **interleaving** — whole fragment *records* from different streams are
  shuffled in arrival order;
* **byte adjacency** — fragments are also emitted as a raw byte stream in which
  records from different streams sit next to each other, so a naive
  concatenate-and-scan parser would fuse them.

Reassembly must survive both because it keys on stream identity + offset and
because records are self-delimiting.
"""

from __future__ import annotations

from typing import List, Sequence

from .wire import Fragment


def interleave_records(stream_groups: Sequence[Sequence[Fragment]]) -> List[bytes]:
    """Round-robin whole records across streams, returning encoded records.

    Deterministic: no randomness, so tests are reproducible.
    """

    groups = [list(g) for g in stream_groups]
    out: List[bytes] = []
    idx = 0
    while True:
        emitted = False
        for g in groups:
            if idx < len(g):
                out.append(g[idx].encode())
                emitted = True
        if not emitted:
            break
        idx += 1
    return out


def to_pipe_bytes(records: Sequence[bytes]) -> bytes:
    """Concatenate records into a raw pipe byte stream (adjacency preserved)."""

    return b"".join(records)


def interleave_as_pipe(stream_groups: Sequence[Sequence[Fragment]]) -> bytes:
    return to_pipe_bytes(interleave_records(stream_groups))
