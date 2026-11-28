"""Inspection console: turns raw receipts into a defect picture.

Key rules encoded here:

* A correction is resolved to a physical row **using the generation it was
  observed under**.  After a firmware revision the same logical page can sit on
  a different row, so replaying an old receipt against the new generation would
  blame the wrong row.
* Two receipts describing the same observation (same board / page / channel /
  generation / syndrome) count as **one** anomaly, no matter how many copies
  arrive or what receipt id / timestamp they carry.
* A row that was already retired is not "new damage"; it is folded in as an
  independently known unusable row.
"""

from __future__ import annotations

from dataclasses import dataclass

from .evidence import CorrectionReceipt, EvidenceBundle, RetiredRow
from .geometry import REGION_PRIMARY, Geometry, RowAddr


@dataclass(frozen=True)
class Correction:
    """One *deduplicated* corrected (or uncorrectable) observation."""

    fingerprint: tuple
    receipts: tuple[str, ...]  # every receipt id that reported it
    row_at_generation: RowAddr  # physical row under the receipt's generation
    logical_page: int
    channel: int
    generation: int
    syndrome: str

    @property
    def is_independent_anomaly(self) -> bool:
        return True


@dataclass(frozen=True)
class DefectRow:
    row: RowAddr
    reasons: tuple[str, ...]
    correction_fingerprint: tuple | None = None

    def merge(self, reason: str) -> "DefectRow":
        return DefectRow(self.row, (*self.reasons, reason), self.correction_fingerprint)


@dataclass(frozen=True)
class InspectionReport:
    corrections: tuple[Correction, ...]
    retired_rows: tuple[RowAddr, ...]
    healthy_pages: tuple[int, ...]
    defects: tuple[DefectRow, ...]           # defective rows *under current gen*
    unmapped_defects: tuple[DefectRow, ...]  # rows known bad but no current page backs them
    current_generation: int

    @property
    def bad_rows(self) -> tuple[RowAddr, ...]:
        return tuple(sorted({d.row for d in self.defects}))

    @property
    def total_anomalies(self) -> int:
        """Independent anomalies = deduplicated corrections + retired rows."""
        return len(self.corrections) + len(self.retired_rows)

    @property
    def damaged_pages(self) -> tuple[int, ...]:
        assert self._geom is not None, "InspectionReport was built without geometry"
        pages = {
            self._geom.page_on_row(d.row, self.current_generation)
            for d in self.defects
        }
        return tuple(sorted(p for p in pages if p is not None))

    # The methods below need geometry; they are thin conveniences so callers do
    # not have to re-derive page<->row by hand.
    _geom: Geometry | None = None

    def current_page_for(self, row: RowAddr) -> int | None:
        assert self._geom is not None, "InspectionReport was built without geometry"
        return self._geom.page_on_row(row, self.current_generation)

    def current_row_for(self, page: int, channel: int) -> RowAddr:
        assert self._geom is not None, "InspectionReport was built without geometry"
        return self._geom.primary_row(page, self.current_generation, channel)

    def correction_is_live(self, corr: Correction) -> bool:
        """Does ``corr`` still point at the same physical row under current gen?

        True  -> the damaged row holds a live page slice today.
        False -> firmware remapped things; the bad row no longer backs that
                 page, so the page itself is not endangered by this receipt.
        """

        assert self._geom is not None, "InspectionReport was built without geometry"
        page_row = self._geom.primary_row(corr.logical_page, self.generation, corr.channel)
        # The row the receipt blamed vs. the row the same page uses now.
        current_row = self._geom.primary_row(corr.logical_page, self.current_generation, corr.channel)
        return page_row == corr.row_at_generation and current_row == corr.row_at_generation


def inspect(
    bundle: EvidenceBundle,
    geometry: Geometry,
    current_generation: int,
    extra_defect_rows: tuple[RowAddr, ...] = (),
) -> InspectionReport:
    """Build an :class:`InspectionReport` from raw evidence."""

    # 1. Deduplicate corrections by observation fingerprint.
    by_fp: dict[tuple, Correction] = {}
    for receipt in bundle.corrections:
        row_now = geometry.primary_row(receipt.logical_page, receipt.generation, receipt.channel)
        fp = receipt.fingerprint()
        if fp in by_fp:
            prev = by_fp[fp]
            by_fp[fp] = Correction(
                fingerprint=fp,
                receipts=(*prev.receipts, receipt.receipt_id),
                row_at_generation=prev.row_at_generation,
                logical_page=prev.logical_page,
                channel=prev.channel,
                generation=prev.generation,
                syndrome=prev.syndrome,
            )
        else:
            by_fp[fp] = Correction(
                fingerprint=fp,
                receipts=(receipt.receipt_id,),
                row_at_generation=row_now,
                logical_page=receipt.logical_page,
                channel=receipt.channel,
                generation=receipt.generation,
                syndrome=receipt.syndrome,
            )

    corrections = tuple(sorted(by_fp.values(), key=lambda c: (c.generation, c.channel, c.logical_page, c.syndrome)))

    retired_rows = tuple(sorted({r.row() for r in bundle.retired}))

    # 2. Fold retired rows + extra rows into the defect set, resolving each
    #    damaged physical row to the page it backs *under the current generation*.
    defects_by_row: dict[RowAddr, DefectRow] = {}

    def add(row: RowAddr, reason: str, fp: tuple | None = None) -> None:
        if row.region != REGION_PRIMARY:
            return
        if row in defects_by_row:
            existing = defects_by_row[row]
            defects_by_row[row] = existing.merge(reason)
        else:
            defects_by_row[row] = DefectRow(row, (reason,), fp)

    for retired in sorted(bundle.retired, key=lambda r: (r.channel, r.index)):
        add(retired.row(), f"retired@g{retired.generation}")

    for row in extra_defect_rows:
        add(row, "externally-reported")

    for corr in corrections:
        add(
            corr.row_at_generation,
            f"{corr.syndrome}@g{corr.generation}",
            corr.fingerprint,
        )

    # 3. Split defects into those that still back a live page vs. orphaned rows.
    live: list[DefectRow] = []
    orphaned: list[DefectRow] = []
    damaged_pages: set[int] = set()
    for defect in sorted(defects_by_row.values(), key=lambda d: (d.row.channel, d.row.index)):
        page = geometry.page_on_row(defect.row, current_generation)
        if page is None:
            orphaned.append(defect)
        else:
            live.append(defect)
            damaged_pages.add(page)

    healthy_pages = tuple(sorted(set(geometry.all_pages()) - damaged_pages))

    report = InspectionReport(
        corrections=corrections,
        retired_rows=retired_rows,
        healthy_pages=healthy_pages,
        defects=tuple(live),
        unmapped_defects=tuple(orphaned),
        current_generation=current_generation,
    )
    object.__setattr__(report, "_geom", geometry)
    return report
