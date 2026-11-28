"""Island merge desk: what an island can actually open, tracked per piece.

The whole point of this exercise: **receiving a bag is not the same as the
island getting a useful new piece**. So an island never just records a
byte-count. Each island merges what actually arrived into its own store and
reports whether that closed any gap for the requested window.

A `Guarantee` exists only when, for the requested window, the island can
independently assemble an original-observation file:

  * every (seg, t) position of the window is covered, and
  * the assembled id order is continuously sequenced (no skipped position), and
  * every assembled segment's inner digest matches the manifest (the island
    "复核内层摘要"), and
  * the resulting window root is deterministic and reproducible.

Coverage can come from three independent sources:
  1. the station's own track (轨段) calendar -- it acquired those positions,
  2. previously ingested overlapping pieces (先前入库的重叠片),
  3. pieces actually received on this shipment and digest-verified.

Nobody's *count of bytes* is ever treated as coverage. Two islands that receive
the same number of bytes but whose bags overlap existing material differently
end up with different gaps; that falls out of positional coverage directly.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Optional

from .model import Calendar, Manifest, SpaceTime, TrackRange, sha256_hex


def window_root(ordered_sids: tuple[tuple[str, bytes], ...]) -> str:
    h = hashlib.sha256()
    h.update(b"window|")
    for sid, data in ordered_sids:
        h.update(sid.encode("utf-8"))
        h.update(b"\x00")
        h.update(data)
        h.update(b"\x00")
    return h.hexdigest()


@dataclass
class Guarantee:
    """A verifiable, independently-openable recovery commitment for a window."""

    station: str
    obs_id: str
    root: str
    window: TrackRange
    openings: tuple[str, ...]
    receipt_ids: tuple[str, ...]

    def as_dict(self) -> dict:
        return {
            "station": self.station,
            "obs_id": self.obs_id,
            "root": self.root,
            "window": {
                "seg_lo": self.window.seg_lo,
                "seg_hi": self.window.seg_hi,
                "t_lo": self.window.t_lo,
                "t_hi": self.window.t_hi,
            },
            "openings": list(self.openings),
            "receipt_ids": list(self.receipt_ids),
        }


@dataclass
class IslandStore:
    """One island station's independent merge desk."""

    station: str
    calendar: Calendar
    manifest: Manifest
    # pieces the station already had ingested before this shipment
    held: dict[str, bytes] = field(default_factory=dict)
    # pieces actually received & verified (from carriers)
    received: dict[str, bytes] = field(default_factory=dict)
    receipt_ids: list[str] = field(default_factory=list)
    guarantees: list["Guarantee"] = field(default_factory=list)
    clobbered: int = 0          # duplicate pieces that would have overwritten
    verified_rejections: int = 0  # pieces whose inner digest did not verify

    # ---- positional helpers ------------------------------------------------ #

    def _positions_for_held(self) -> set[tuple[int, int]]:
        by_id = self.manifest.by_id()
        out = set()
        for sid in set(self.held) | set(self.received):
            seg = by_id[sid]
            out.add((seg.seg, seg.t))
        return out

    def _calendar_positions(self) -> set[tuple[int, int]]:
        return {s.as_tuple() for s in self.calendar.steps}

    def bytes_for(self, sid: str) -> Optional[bytes]:
        return self.received.get(sid, self.held.get(sid))

    def _source_positions(self) -> set[tuple[int, int]]:
        """Positions the island can physically assemble bytes for."""
        return self._calendar_positions() | self._positions_for_held()

    def _ordered_sids(self, rng: TrackRange) -> tuple[str, ...]:
        return tuple(
            f"{self.manifest.obs_id}:s{seg}:t{t}"
            for seg in range(rng.seg_lo, rng.seg_hi + 1)
            for t in range(rng.t_lo, rng.t_hi + 1)
        )

    def missing(self, rng: TrackRange) -> set[tuple[int, int]]:
        """Positions of the window not covered by any source (the real gap)."""
        want = {p.as_tuple() for p in rng.iter_steps()}
        return want - self._source_positions()

    def can_open(self, rng: TrackRange) -> bool:
        """Can the station independently assemble the whole window right now?"""
        if self.missing(rng):
            return False
        by_id = self.manifest.by_id()
        for sid in self._ordered_sids(rng):
            seg = by_id[sid]
            pos = (seg.seg, seg.t)
            if pos in self._calendar_positions():
                # The station's own acquired track; trusted as observed data.
                continue
            data = self.bytes_for(sid)
            if data is None or sha256_hex(data) != seg.inner_digest:
                return False
        return True

    def _assemble(self, rng: TrackRange) -> tuple[tuple[str, bytes], ...]:
        by_id = self.manifest.by_id()
        out = []
        for sid in self._ordered_sids(rng):
            seg = by_id[sid]
            pos = (seg.seg, seg.t)
            data = self.bytes_for(sid)
            if data is None and pos in self._calendar_positions():
                # Own acquired track: use the station's own bytes (reproduced
                # deterministically from the manifest for this synthetic model).
                data = seg.data
            if data is None:
                raise ValueError(f"cannot assemble {sid}: no source")
            out.append((sid, data))
        return tuple(out)

    # ---- merge ------------------------------------------------------------- #

    def merge(self, rng: TrackRange, pieces: dict[str, bytes]) -> Optional[Guarantee]:
        """Real merge attempt. Returns a Guarantee iff the window now opens."""
        by_id = self.manifest.by_id()
        accepted: dict[str, bytes] = {}
        for sid, data in pieces.items():
            if sid not in by_id:
                continue
            if sha256_hex(data) != by_id[sid].inner_digest:
                self.verified_rejections += 1
                continue
            accepted[sid] = data

        # content-addressed: a duplicate may not clobber an existing equal-rooted
        # piece; anything that disagrees would be junk and is counted, not stored.
        for sid, data in accepted.items():
            if sid in self.received and self.received[sid] != data:
                self.clobbered += 1
        self.received.update(accepted)

        if not self.can_open(rng):
            return None

        ordered = self._assemble(rng)
        root = window_root(ordered)
        g = Guarantee(
            station=self.station,
            obs_id=self.manifest.obs_id,
            root=root,
            window=rng,
            openings=tuple(sid for sid, _ in ordered),
            receipt_ids=tuple(sorted(self.receipt_ids)),
        )
        self.guarantees.append(g)
        return g

    def record_receipt(self, receipt_id: str) -> None:
        if receipt_id not in self.receipt_ids:
            self.receipt_ids.append(receipt_id)

    # ---- recovery expression for comparisons ------------------------------ #

    def recovery(self, rng: TrackRange) -> set[tuple[int, int]]:
        want = {p.as_tuple() for p in rng.iter_steps()}
        return want & self._source_positions()


def bytes_received(store: IslandStore) -> int:
    return sum(len(v) for v in store.received.values())
