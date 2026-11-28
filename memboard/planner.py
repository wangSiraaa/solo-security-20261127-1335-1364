"""Maintenance planning: decide what must leave the board, and how.

Two strategies are offered, as the exercise allows:

``selective_remap``
    Move only the page slices that sit on defective rows to spare rows.
    Keeps healthy neighbours in place, so it asks little of the spare pool, but
    needs each defect to be *provable* (>= 2 independent observations, or extra
    scrub passes within a limited budget) and must re-check generation drift.

``group_drain``
    Drain a whole isolated domain (every page on the board) onto spares.
    Trivially explains the maintenance boundary, but demands a large spare
    pool (``pages * channels`` spare rows).

Provability rule (important):
    Duplicate reports of the *same* observation collapse to a single anomaly.
    One correction reported twice is **one** anomaly, not two.  Only distinct
    observations for the same physical row corroborate each other.

The returned :class:`MaintenancePlan` is what the technician signs off before
any data actually moves.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .evidence import EvidenceBundle
from .geometry import REGION_PRIMARY, REGION_SPARE, Geometry, RowAddr
from .inspection import InspectionReport, inspect

STRATEGY_SELECTIVE = "selective_remap"
STRATEGY_GROUP = "group_drain"

# Independent observations needed to call a corrected defect "provable".
CORRECTED_EVIDENCE_SUFFICIENT = 2


class PlanningError(Exception):
    """Raised when a strategy cannot be satisfied with the given budget."""


@dataclass(frozen=True)
class PageSliceMove:
    """One page slice relocated from a primary row to a spare row."""

    page: int
    channel: int
    source: RowAddr
    target: RowAddr
    reason: str

    def label(self) -> str:
        return f"pg{self.page}/ch{self.channel}: {self.source.label()} -> {self.target.label()}"


@dataclass
class MaintenancePlan:
    strategy: str
    board: str
    current_generation: int
    moves: list[PageSliceMove]
    drains_all: bool
    scrub_passes_used: int
    scrub_budget: int
    spare_rows_used: int
    spare_rows_total: int
    evidence: InspectionReport
    notes: list[str] = field(default_factory=list)
    computed_for_generation: int = 0

    @property
    def touched_pages(self) -> tuple[int, ...]:
        return tuple(sorted({m.page for m in self.moves}))

    @property
    def spare_remaining(self) -> int:
        return self.spare_rows_total - self.spare_rows_used

    @property
    def target_rows(self) -> set[RowAddr]:
        return {m.target for m in self.moves}

    def summary(self) -> str:
        kind = "FULL DOMAIN DRAIN" if self.drains_all else "SELECTIVE REMAP"
        lines = [
            f"[{kind}] board={self.board} generation={self.current_generation}",
            f"  moves={len(self.moves)} pages={len(self.touched_pages)} "
            f"spares {self.spare_rows_used}/{self.spare_rows_total} "
            f"scrub {self.scrub_passes_used}/{self.scrub_budget}",
        ]
        for note in self.notes:
            lines.append(f"  note: {note}")
        for move in self.moves:
            lines.append(f"  - {move.label()}  ({move.reason})")
        return "\n".join(lines)


def _spare_targets(geometry: Geometry, count: int, used: set[RowAddr], channel: int | None = None) -> list[RowAddr]:
    """Allocate ``count`` spare rows, preferring the source channel."""

    out: list[RowAddr] = []
    channels = [channel] if channel is not None else list(range(geometry.num_channels))
    for ch in channels:
        for row in geometry.spare_rows(ch):
            if row in used:
                continue
            out.append(row)
            if len(out) == count:
                return out
    if channel is not None:
        for ch in range(geometry.num_channels):
            if ch == channel:
                continue
            for row in geometry.spare_rows(ch):
                if row in used:
                    continue
                out.append(row)
                if len(out) == count:
                    return out
    return out


def _row_corroboration(report: InspectionReport) -> dict[RowAddr, int]:
    """Distinct (deduplicated) corrections that blame each physical row.

    Duplicates were already collapsed by :func:`inspect`, so a receipt filed
    twice contributes 1 here.
    """

    counts: dict[RowAddr, int] = {}
    for corr in report.corrections:
        counts[corr.row_at_generation] = counts.get(corr.row_at_generation, 0) + 1
    return counts


def _is_live(report: InspectionReport, row: RowAddr) -> bool:
    live_rows = {d.row for d in report.defects}
    return row in live_rows


def plan_maintenance(
    bundle: EvidenceBundle,
    geometry: Geometry,
    current_generation: int,
    strategy: str,
    spare_rows: int,
    scrub_budget: int,
    maintenance_window: int,
    extra_defect_rows: tuple[RowAddr, ...] = (),
    exclude_spares: frozenset[RowAddr] = frozenset(),
    exclude_sources: frozenset[RowAddr] = frozenset(),
) -> MaintenancePlan:
    """Compute a maintenance plan; raises :class:`PlanningError` when infeasible.

    ``exclude_spares`` lets a follow-up plan avoid spare rows already promised
    by an earlier plan (prevents double-allocation across classes or across a
    mid-flight extension).  ``exclude_sources`` skips primary rows whose slices
    are already handled.
    """

    if strategy not in (STRATEGY_SELECTIVE, STRATEGY_GROUP):
        raise ValueError(f"unknown strategy {strategy!r}")
    if spare_rows < 0 or scrub_budget < 0 or maintenance_window < 0:
        raise ValueError("budgets must be non-negative")

    report = inspect(bundle, geometry, current_generation, extra_defect_rows)
    notes: list[str] = []
    corroboration = _row_corroboration(report)
    scrub_passes = 0
    provable_rows: set[RowAddr] = set()
    unproven_rows: set[RowAddr] = set()

    for defect in report.defects:
        reason = ",".join(defect.reasons)
        if "retired" in reason or "externally-reported" in reason or "UE" in reason:
            # Independent, unambiguous evidence: a retired row, an
            # uncorrectable read, or a row reported by another subsystem.
            provable_rows.add(defect.row)
            continue
        n = corroboration.get(defect.row, 0)
        if n >= CORRECTED_EVIDENCE_SUFFICIENT:
            provable_rows.add(defect.row)
            continue
        needed = CORRECTED_EVIDENCE_SUFFICIENT - n
        if scrub_passes + needed <= scrub_budget:
            scrub_passes += needed
            provable_rows.add(defect.row)
        else:
            unproven_rows.add(defect.row)
            notes.append(
                f"row {defect.row.label()} left unproven: needs {needed} scrub pass(es), "
                f"only {scrub_budget - scrub_passes} left"
            )

    defect_rows = {d.row for d in report.defects}

    if strategy == STRATEGY_SELECTIVE:
        moves: list[PageSliceMove] = []
        used_spares: set[RowAddr] = set(exclude_spares)
        unresolvable: list[str] = []

        slices: list[tuple[int, int, RowAddr]] = []
        for row in sorted(provable_rows, key=lambda r: (r.channel, r.index)):
            if row in exclude_sources:
                continue
            page = geometry.page_on_row(row, current_generation)
            if page is None:
                notes.append(f"row {row.label()} flagged but backs no current page; skipped")
                continue
            slices.append((page, row.channel, row))

        for page, channel, row in sorted(slices):
            targets = _spare_targets(geometry, 1, used_spares, channel)
            if not targets:
                unresolvable.append(f"pg{page}/ch{channel} ({row.label()}): no spare row left")
                continue
            target = targets[0]
            used_spares.add(target)
            moves.append(
                PageSliceMove(
                    page=page,
                    channel=channel,
                    source=row,
                    target=target,
                    reason=next((d.reasons[0] for d in report.defects if d.row == row), "defect"),
                )
            )

        if unresolvable:
            raise PlanningError(
                "not enough spare rows for every defective slice: " + "; ".join(unresolvable)
            )
        newly_used = len(used_spares - set(exclude_spares))
        if newly_used > spare_rows:
            raise PlanningError(
                f"selective remap needs {newly_used} new spare rows, only {spare_rows} available"
            )
        if len(moves) > maintenance_window:
            raise PlanningError(
                f"selective remap needs {len(moves)} moves, window allows {maintenance_window}"
            )

        return MaintenancePlan(
            strategy=strategy,
            board=geometry.board,
            current_generation=current_generation,
            moves=moves,
            drains_all=False,
            scrub_passes_used=scrub_passes,
            scrub_budget=scrub_budget,
            spare_rows_used=len(used_spares),
            spare_rows_total=spare_rows,
            evidence=report,
            notes=notes,
            computed_for_generation=current_generation,
        )

    # -- group drain -------------------------------------------------------
    needed = geometry.domain_pages * geometry.num_channels
    if needed > spare_rows:
        raise PlanningError(
            f"group drain requires {needed} spare rows (domain pages x channels), "
            f"only {spare_rows} available; fall back to selective remap"
        )
    if needed > maintenance_window:
        raise PlanningError(
            f"group drain requires {needed} moves, window allows {maintenance_window}"
        )

    moves = []
    used_spares = set(exclude_spares)
    for page in geometry.all_pages():
        for channel in range(geometry.num_channels):
            source = geometry.primary_row(page, current_generation, channel)
            targets = _spare_targets(geometry, 1, used_spares, channel)
            if not targets:
                raise PlanningError(f"pg{page}/ch{channel}: spare pool exhausted during drain")
            target = targets[0]
            used_spares.add(target)
            moves.append(
                PageSliceMove(page=page, channel=channel, source=source, target=target,
                              reason="domain drain")
            )

    notes.append("group drain moves the whole isolated domain; no per-page defect proof required")
    if not defect_rows:
        notes.append("drain chosen despite no current defects; boundary is conservative")

    return MaintenancePlan(
        strategy=strategy,
        board=geometry.board,
        current_generation=current_generation,
        moves=moves,
        drains_all=True,
        scrub_passes_used=scrub_passes,
        scrub_budget=scrub_budget,
        spare_rows_used=len(used_spares),
        spare_rows_total=spare_rows,
        evidence=report,
        notes=notes,
        computed_for_generation=current_generation,
    )
