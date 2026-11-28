"""Physical geometry of the fictional memory board.

The model is deliberately small and fully deterministic:

* a logical page is striped across ``num_channels`` interleaved channels;
* each channel slice lands on exactly one physical *row*;
* rows live in a ``primary`` region or a ``mirror`` region;
* ``spare`` rows form a separate, always-online region used for remapping;
* the logical-page -> physical-row mapping is a permutation that is seeded by
  the firmware *generation*.  A firmware revision therefore reshuffles which
  physical row backs which logical page.  This is the property the exercise
  hinges on: an old correction receipt must be interpreted with the generation
  that was active when it was observed, while the data that must be moved is
  found with the *current* generation.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from functools import lru_cache

REGION_PRIMARY = "primary"
REGION_MIRROR = "mirror"
REGION_SPARE = "spare"

REGIONS = (REGION_PRIMARY, REGION_MIRROR, REGION_SPARE)


@dataclass(frozen=True, order=True)
class RowAddr:
    """A physical row: (board, channel, region, index)."""

    board: str
    channel: int
    region: str
    index: int

    def label(self) -> str:
        return f"{self.board}/ch{self.channel}/{self.region}/row{self.index:02d}"

    def __str__(self) -> str:  # pragma: no cover - convenience
        return self.label()


def _seed(*parts: object) -> int:
    payload = "|".join(str(p) for p in parts)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return int(digest[:16], 16)


@lru_cache(maxsize=None)
def _permutation(board: str, generation: int, salt: str, n: int) -> tuple[int, ...]:
    """Deterministic bijection ``logical_page -> physical_row_index``."""

    rng = random.Random(_seed(board, generation, salt))
    values = list(range(n))
    rng.shuffle(values)
    return tuple(values)


@dataclass(frozen=True)
class Geometry:
    board: str
    num_channels: int = 2
    domain_pages: int = 32
    rows_per_channel: int = 36  # primary-row capacity; may exceed live pages
    spare_rows_per_channel: int = 8
    mirrored_pages: frozenset = frozenset()

    # -- mapping helpers ---------------------------------------------------

    def _perm(self, generation: int, channel: int, region: str) -> tuple[int, ...]:
        if region not in (REGION_PRIMARY, REGION_MIRROR):
            raise ValueError(f"no page permutation for region {region!r}")
        return _permutation(self.board, generation, f"{region}:{channel}", self.rows_per_channel)

    def _page_to_index(self, page: int, generation: int, channel: int, region: str) -> int:
        if not 0 <= page < self.domain_pages:
            raise ValueError(f"page {page} outside domain 0..{self.domain_pages - 1}")
        return self._perm(generation, channel, region)[page]

    def primary_row(self, page: int, generation: int, channel: int) -> RowAddr:
        idx = self._page_to_index(page, generation, channel, REGION_PRIMARY)
        return RowAddr(self.board, channel, REGION_PRIMARY, idx)

    def mirror_row(self, page: int, generation: int, channel: int) -> RowAddr:
        idx = self._page_to_index(page, generation, channel, REGION_MIRROR)
        return RowAddr(self.board, channel, REGION_MIRROR, idx)

    def page_on_row(self, row: RowAddr, generation: int) -> int | None:
        """Inverse mapping: which logical page slice lives on ``row`` now?

        Only the first ``domain_pages`` entries of the permutation are live;
        rows outside that window are physically present but unused.
        """

        if row.region not in (REGION_PRIMARY, REGION_MIRROR):
            return None
        if not 0 <= row.index < self.rows_per_channel:
            return None
        for page in range(self.domain_pages):
            if self._page_to_index(page, generation, row.channel, row.region) == row.index:
                return page
        return None

    def is_mirrored(self, page: int) -> bool:
        return page in self.mirrored_pages

    # -- row inventories ---------------------------------------------------

    def spare_row(self, channel: int, index: int) -> RowAddr:
        return RowAddr(self.board, channel, REGION_SPARE, index)

    def spare_rows(self, channel: int) -> list[RowAddr]:
        return [self.spare_row(channel, i) for i in range(self.spare_rows_per_channel)]

    def primary_rows(self) -> list[RowAddr]:
        return [
            RowAddr(self.board, c, REGION_PRIMARY, i)
            for c in range(self.num_channels)
            for i in range(self.rows_per_channel)
        ]

    def live_primary_rows(self, generation: int) -> list[RowAddr]:
        return [
            self.primary_row(page, generation, c)
            for c in range(self.num_channels)
            for page in range(self.domain_pages)
        ]

    def unused_primary_rows(self, generation: int) -> list[RowAddr]:
        live = {(r.channel, r.index) for r in self.live_primary_rows(generation)}
        return [r for r in self.primary_rows() if (r.channel, r.index) not in live]

    def mirror_rows(self) -> list[RowAddr]:
        return [
            RowAddr(self.board, c, REGION_MIRROR, i)
            for c in range(self.num_channels)
            for i in range(self.rows_per_channel)
        ]

    def all_pages(self) -> list[int]:
        return list(range(self.domain_pages))

