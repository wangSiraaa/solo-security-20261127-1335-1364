"""The fake memory board and the mechanics of moving data.

The board keeps its bytes in physical rows.  A logical page read walks the
current-generation striping to the backing rows.  Data placement follows a
stub protocol: a replacement row for a page slice starts as an *incomplete
clone* that must be finalised (from a viable replica) before the old row can
leave service.  Retiring a source row before its clone is final raises -- what
the exercise wants to catch.

Mirroring: a mirrored page has both a primary and a mirror copy.  A slice whose
primary is uncorrectable can still be cloned from its mirror copy.  A read
survives one unreadable copy as long as the other copy is good.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Optional

from .evidence import AccessReceipt, CorrectionReceipt
from .geometry import REGION_MIRROR, REGION_PRIMARY, REGION_SPARE, Geometry, RowAddr
from .planner import PageSliceMove


class MigrationError(Exception):
    pass


class BoardOfflineError(MigrationError):
    """Raised when classroom traffic hits the board during the maintenance window."""


PageData = tuple[int, int]  # (revision, value) for one page slice


def _page_key(page: int) -> str:
    return f"page{page:02d}"


@dataclass
class BoardSim:
    geometry: Geometry
    generation: int
    cells: dict[RowAddr, dict[str, PageData]] = field(default_factory=dict)
    spare_owners: dict[RowAddr, tuple[int, int]] = field(default_factory=dict)  # -> (page, channel)
    pending_clones: dict[RowAddr, tuple[int, int]] = field(default_factory=dict)
    static_bad: set[RowAddr] = field(default_factory=set)   # uncorrectable (UE)
    degraded_rows: set[RowAddr] = field(default_factory=set)  # corrected (CE), readable
    retired_rows: set[RowAddr] = field(default_factory=set)
    offline: bool = False
    access_log: list[AccessReceipt] = field(default_factory=list)
    _map_cache: dict[int, dict[tuple[int, int], RowAddr]] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        geometry: Geometry,
        generation: int,
        static_bad: tuple[RowAddr, ...] = (),
        degraded: tuple[RowAddr, ...] = (),
        retired: tuple[RowAddr, ...] = (),
    ) -> "BoardSim":
        board = cls(geometry=geometry, generation=generation)
        board.static_bad = set(static_bad)
        board.degraded_rows = set(degraded)
        board.retired_rows = set(retired)
        return board

    # -- mapping --------------------------------------------------------

    def row_for_slice(self, page: int, channel: int, generation: int | None = None) -> RowAddr:
        gen = self.generation if generation is None else generation
        if gen == self.generation:
            for spare, (pg, ch) in self.spare_owners.items():
                if (pg, ch) == (page, channel):
                    return spare
            cache = self._map_cache.setdefault(gen, {})
            key = (page, channel)
            if key not in cache:
                cache[key] = self.geometry.primary_row(page, gen, channel)
            return cache[key]
        # Historic view (used only for reasoning; remapped slices excluded).
        return self.geometry.primary_row(page, gen, channel)

    def invalidate_maps(self) -> None:
        self._map_cache.clear()

    def rows_for_page(self, page: int) -> list[RowAddr]:
        return [self.row_for_slice(page, ch) for ch in range(self.geometry.num_channels)]

    def _guard_online(self) -> None:
        if self.offline:
            raise BoardOfflineError("board is offline for maintenance; no traffic served")

    # -- population -----------------------------------------------------

    def load(self, page: int, revision: int, value: int) -> None:
        for ch in range(self.geometry.num_channels):
            row = self.row_for_slice(page, ch)
            self.cells.setdefault(row, {})[_page_key(page)] = (revision, value)
            if self.geometry.is_mirrored(page):
                mrow = self.geometry.mirror_row(page, self.generation, ch)
                self.cells.setdefault(mrow, {})[_page_key(page)] = (revision, value)

    def peek(self, page: int, channel: int = 0) -> Optional[PageData]:
        row = self.row_for_slice(page, channel)
        return self.cells.get(row, {}).get(_page_key(page))

    # -- reads / writes -------------------------------------------------

    def _row_readable(self, row: RowAddr) -> bool:
        # A retired row is decommissioned from *primary service*: its bytes are
        # still there to be evacuated, so it remains cloneable.  An
        # uncorrectable (static_bad) row is genuinely unreadable, and an
        # incomplete clone is not served yet.
        if row in self.static_bad:
            return False
        if row in self.pending_clones:
            return False
        return True

    def write(self, page: int, revision: int, value: int, receipt_id: str) -> AccessReceipt:
        self._guard_online()
        ok = True
        for ch in range(self.geometry.num_channels):
            row = self.row_for_slice(page, ch)
            if self._row_readable(row):
                self.cells.setdefault(row, {})[_page_key(page)] = (revision, value)
            else:
                ok = False
            if self.geometry.is_mirrored(page):
                mrow = self.geometry.mirror_row(page, self.generation, ch)
                if self._row_readable(mrow):
                    self.cells.setdefault(mrow, {})[_page_key(page)] = (revision, value)
                else:
                    ok = False
        receipt = AccessReceipt(receipt_id, self.geometry.board, page, -1,
                                self.generation, "write", ok)
        self.access_log.append(receipt)
        return receipt

    def read(self, page: int, receipt_id: str) -> tuple[Optional[PageData], AccessReceipt]:
        self._guard_online()
        results: list[PageData] = []
        ok = True
        for ch in range(self.geometry.num_channels):
            row = self.row_for_slice(page, ch)
            data = self.cells.get(row, {}).get(_page_key(page)) if self._row_readable(row) else None
            if data is None and self.geometry.is_mirrored(page):
                mrow = self.geometry.mirror_row(page, self.generation, ch)
                if self._row_readable(mrow):
                    data = self.cells.get(mrow, {}).get(_page_key(page))
            if data is None:
                ok = False
            else:
                results.append(data)
        data = max(results, key=lambda d: d[0]) if results else None
        if data is None:
            ok = False
        receipt = AccessReceipt(receipt_id, self.geometry.board, page, -1,
                                self.generation, "read", ok)
        self.access_log.append(receipt)
        return (data if ok else None), receipt

    # -- correction synthesis ------------------------------------------

    def make_correction(self, receipt_id: str, page: int, channel: int, syndrome: str = "CE",
                        generation: int | None = None, observed_at: float = 0.0,
                        note: str = "") -> CorrectionReceipt:
        return CorrectionReceipt(receipt_id, self.geometry.board, page, channel,
                                 self.generation if generation is None else generation,
                                 syndrome, observed_at, note)

    # -- migration primitives (stub protocol) --------------------------

    def _source_data(self, move: PageSliceMove) -> PageData | None:
        key = _page_key(move.page)
        data = self.cells.get(move.source, {}).get(key)
        if data is None and self.geometry.is_mirrored(move.page):
            mrow = self.geometry.mirror_row(move.page, self.generation, move.channel)
            data = self.cells.get(mrow, {}).get(key)
        return data

    def begin_clone(self, move: PageSliceMove) -> None:
        if move.target in self.spare_owners and self.spare_owners[move.target] != (move.page, move.channel):
            raise MigrationError(
                f"spare {move.target.label()} already owned by {self.spare_owners[move.target]}"
            )
        if move.target in self.pending_clones:
            raise MigrationError(f"spare {move.target.label()} already holds a pending clone")
        src = self._source_data(move)
        if src is None:
            raise MigrationError(
                f"cannot clone pg{move.page}/ch{move.channel}: no readable replica at source"
            )
        self.cells.setdefault(move.target, {})[_page_key(move.page)] = src
        self.pending_clones[move.target] = (move.page, move.channel)

    def finalise_clone(self, move: PageSliceMove) -> None:
        if move.target not in self.pending_clones:
            raise MigrationError(
                f"spare {move.target.label()} is not a pending clone; begin_clone first"
            )
        key = _page_key(move.page)
        best = self.cells.get(move.target, {}).get(key)
        if self.geometry.is_mirrored(move.page):
            mrow = self.geometry.mirror_row(move.page, self.generation, move.channel)
            alt = self.cells.get(mrow, {}).get(key)
            if alt is not None and (best is None or alt[0] > best[0]):
                best = alt
        if best is None:
            raise MigrationError(f"no viable replica for pg{move.page}/ch{move.channel}")
        self.cells.setdefault(move.target, {})[key] = best
        del self.pending_clones[move.target]
        self.spare_owners[move.target] = (move.page, move.channel)
        self.invalidate_maps()

    def abandon_clone(self, move: PageSliceMove) -> None:
        self.pending_clones.pop(move.target, None)
        self.cells.pop(move.target, None)

    def retire_source(self, move: PageSliceMove) -> None:
        if move.target in self.pending_clones:
            raise MigrationError(
                f"refusing to retire {move.source.label()}: clone for pg{move.page} not finalised"
            )
        if self.spare_owners.get(move.target) != (move.page, move.channel):
            raise MigrationError(
                f"refusing to retire {move.source.label()}: spare {move.target.label()} does not own this slice"
            )
        self.cells.get(move.source, {}).pop(_page_key(move.page), None)
        self.retired_rows.add(move.source)
        self.invalidate_maps()

    # -- firmware generation change ------------------------------------

    def apply_generation_change(self, new_generation: int) -> None:
        """Model a firmware revision that reshuffles the striping.

        Each logical slice's bytes are re-striped onto the row the new
        generation designates.  Slices already remapped onto a spare stay on
        the spare; only their mirror copy (if any) is re-striped.  This is how
        an old correction receipt can become orphaned: its blamed primary row
        no longer backs that page.
        """

        if new_generation == self.generation:
            return
        old_gen = self.generation
        keys = [_page_key(p) for p in self.geometry.all_pages()]
        remapped = {(p, c) for _, (p, c) in self.spare_owners.items()}

        def old_rows(page: int, ch: int) -> list[RowAddr]:
            rows = [self.geometry.primary_row(page, old_gen, ch)]
            if self.geometry.is_mirrored(page):
                rows.append(self.geometry.mirror_row(page, old_gen, ch))
            return rows

        def new_primary(page: int, ch: int) -> RowAddr:
            return self.geometry.primary_row(page, new_generation, ch)

        def new_mirror(page: int, ch: int) -> RowAddr:
            return self.geometry.mirror_row(page, new_generation, ch)

        carried: dict[tuple[int, int], PageData] = {}
        mirror_carried: dict[tuple[int, int], PageData] = {}
        for page in self.geometry.all_pages():
            key = _page_key(page)
            for ch in range(self.geometry.num_channels):
                if (page, ch) in remapped:
                    # Primary stays on spare; carry only the mirror copy.
                    if self.geometry.is_mirrored(page):
                        d = self.cells.get(self.geometry.mirror_row(page, old_gen, ch), {}).get(key)
                        if d is not None:
                            mirror_carried[(page, ch)] = d
                    continue
                d = self.cells.get(self.geometry.primary_row(page, old_gen, ch), {}).get(key)
                if d is not None:
                    carried[(page, ch)] = d
                if self.geometry.is_mirrored(page):
                    m = self.cells.get(self.geometry.mirror_row(page, old_gen, ch), {}).get(key)
                    if m is not None:
                        mirror_carried[(page, ch)] = m

        # Clear old primary/mirror locations that are not spare rows.
        for page in self.geometry.all_pages():
            key = _page_key(page)
            for ch in range(self.geometry.num_channels):
                for row in old_rows(page, ch):
                    if row.region == REGION_PRIMARY and row not in self.spare_owners:
                        self.cells.get(row, {}).pop(key, None)

        self.generation = new_generation
        self.invalidate_maps()
        for (page, ch), d in carried.items():
            self.cells.setdefault(new_primary(page, ch), {})[_page_key(page)] = d
        for (page, ch), d in mirror_carried.items():
            self.cells.setdefault(new_mirror(page, ch), {})[_page_key(page)] = d

    # -- snapshot for scenario branching -------------------------------

    def snapshot(self) -> "BoardSim":
        return copy.deepcopy(self)
