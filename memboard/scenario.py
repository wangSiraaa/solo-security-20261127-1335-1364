"""End-to-end scenarios for the memory-board exercise.

The scenario builds one piece of *baseline* evidence, derives an **edited**
variant where a single correction receipt's firmware generation is changed, and
runs two maintenance paths against a simulated board:

1. the baseline selective plan (with a mid-flight correction arriving), exercised
   from the inspection console through to a technician sign-off;
2. the twin classroom programs operating on the same board *after* the hand-off.

Everything is deterministic; no randomness beyond the seeded geometry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .client import Client, JournalEntry, SharedClock, build_classroom_pair
from .engine import (
    MigrationEvent,
    MigrationResult,
    execute_plan,
    verify_plan_spares,
    verify_spare_allocation,
)
from .evidence import AccessReceipt, CorrectionReceipt, EvidenceBundle, RetiredRow
from .geometry import REGION_PRIMARY, REGION_SPARE, Geometry, RowAddr
from .inspection import InspectionReport, inspect
from .planner import (
    STRATEGY_GROUP,
    STRATEGY_SELECTIVE,
    MaintenancePlan,
    PlanningError,
    plan_maintenance,
)
from .sim import BoardSim, MigrationError


# ---------------------------------------------------------------------------
# Board + evidence fixture
# ---------------------------------------------------------------------------

BOARD = "board-0"
CURRENT_GEN = 2
PAGE_COUNT = 32
CHANNELS = 2
ROWS_PER_CHANNEL = 36
SPARES_PER_CHANNEL = 8
MIRRORED = frozenset({2})
SHARED_PAGES = (0, 5)
CLASS_A_PAGE = 9
CLASS_B_PAGE = 12
INITIAL_SHARED_VALUE = 7


def make_geometry() -> Geometry:
    return Geometry(
        board=BOARD,
        num_channels=CHANNELS,
        domain_pages=PAGE_COUNT,
        rows_per_channel=ROWS_PER_CHANNEL,
        spare_rows_per_channel=SPARES_PER_CHANNEL,
        mirrored_pages=MIRRORED,
    )


def spare_target_set(plan: MaintenancePlan) -> set[RowAddr]:
    return {m.target for m in plan.moves}


# The physical rows the baseline evidence will blame, worked out from the
# deterministic geometry above.  Keeping them named makes the fixture
# auditable.
ROW_D0_CH0 = 12   # pg13@gen0/ch0 -> ch0/row12, live, backs pg3 under gen2
ROW_D3_CH1 = 7    # pg1@gen0/ch1 & pg18@gen2/ch1 -> ch1/row7, backs pg18
ROW_D1_CH0 = 11   # pg3@gen0/ch0 -> ch0/row11, orphaned under gen2
ROW_SHRINK_CH0 = 30    # pg21@gen2/ch0 -> ch0/row30, live, backs pg21
ROW_SHRINK_ORPHAN = 24  # pg21@gen0/ch0 -> ch0/row24, orphaned under gen2
ROW_RETIRED_CH1 = 35    # ch1/row35 retired@gen0, now backs pg7 under gen2
ROW_UE_CH0 = 22          # pg2@gen2/ch0 -> ch0/row22, uncorrectable, mirrored
ROW_MID_CH0 = 5          # pg7@gen2/ch0 -> ch0/row5, arrives mid-flight


def baseline_evidence() -> EvidenceBundle:
    """Evidence as first handed to the inspection console."""

    corrections = [
        # D0: pg13 correction observed at generation 0, reported twice.
        #       Duplicate reports are ONE anomaly, not two.
        CorrectionReceipt("c-pg13-g0-a", BOARD, 13, 0, 0, "CE", observed_at=10.0),
        CorrectionReceipt("c-pg13-g0-b", BOARD, 13, 0, 0, "CE", observed_at=11.0),
        # D1: pg3 observed at generation 0.  Its blamed row is orphaned under
        #      the current generation -> should NOT drive a move (shrink).
        CorrectionReceipt("c-pg3-g0-a", BOARD, 3, 0, 0, "CE", observed_at=12.0),
        CorrectionReceipt("c-pg3-g0-b", BOARD, 3, 0, 0, "CE", observed_at=13.0),
        # D3: two DISTINCT observations blaming ch1/row7 (different pages,
        #      different generations) -> genuinely corroborated, provable.
        CorrectionReceipt("c-pg1-g0", BOARD, 1, 1, 0, "CE", observed_at=14.0),
        CorrectionReceipt("c-pg18-g2", BOARD, 18, 1, 2, "CE", observed_at=15.0),
        # D-shrink: pg21 observed at the CURRENT generation -> live, makes the
        #           plan larger.  Editing its generation to 0 makes it orphaned.
        CorrectionReceipt("c-pg21-g2", BOARD, 21, 0, 2, "CE", observed_at=16.0),
        # D2: an uncorrectable read on mirrored pg2's primary -> fall back to
        #     the mirror copy and remap the slice.
        CorrectionReceipt("c-pg2-ue", BOARD, 2, 0, 2, "UE", observed_at=17.0),
    ]
    retired = [
        RetiredRow(BOARD, 1, ROW_RETIRED_CH1, generation=0, retired_at=1.0,
                   reason="previously decommissioned"),
    ]
    accesses = [
        AccessReceipt("a-read-0", BOARD, 0, -1, 2, "read", True, 20.0),
        AccessReceipt("a-read-5", BOARD, 5, -1, 2, "read", True, 21.0),
    ]
    return EvidenceBundle(corrections=corrections, retired=retired, accesses=accesses)


def edited_evidence() -> EvidenceBundle:
    """Baseline with ONE receipt's firmware generation changed (g2 -> g0)."""

    bundle = baseline_evidence()
    corrected = []
    changed = False
    for receipt in bundle.corrections:
        if receipt.receipt_id == "c-pg21-g2" and not changed:
            corrected.append(
                CorrectionReceipt(
                    receipt.receipt_id, receipt.board, receipt.logical_page,
                    receipt.channel, 0, receipt.syndrome, receipt.observed_at,
                    note="firmware generation edited for what-if",
                )
            )
            changed = True
        else:
            corrected.append(receipt)
    return EvidenceBundle(corrected, list(bundle.retired), list(bundle.accesses))


def fresh_mid_correction() -> CorrectionReceipt:
    """A correction for pg7/ch0 observed *now* (current generation)."""

    return CorrectionReceipt("c-pg7-mid", BOARD, 7, 0, CURRENT_GEN, "CE", observed_at=101.0)


# ---------------------------------------------------------------------------
# Board construction
# ---------------------------------------------------------------------------

def build_board(
    geometry: Geometry,
    generation: int = CURRENT_GEN,
    extra_bad: tuple[RowAddr, ...] = (),
) -> BoardSim:
    """Populate a fresh board from the fixture's physical facts."""

    degraded = {
        RowAddr(BOARD, 0, REGION_PRIMARY, ROW_D0_CH0),
        RowAddr(BOARD, 1, REGION_PRIMARY, ROW_D3_CH1),
    }
    bad = {RowAddr(BOARD, 0, REGION_PRIMARY, ROW_UE_CH0)}
    bad.update(extra_bad)
    # Only the historical retirement is modelled on the device; the other rows
    # named above are "what-if" targets, not board faults.
    retired = {
        RowAddr(BOARD, 1, REGION_PRIMARY, ROW_RETIRED_CH1),
    }
    board = BoardSim.build(
        geometry,
        generation=generation,
        static_bad=tuple(sorted(bad)),
        degraded=tuple(sorted(degraded)),
        retired=tuple(sorted(retired)),
    )
    # Seed every page with the classroom's initial content.
    for page in geometry.all_pages():
        value = INITIAL_SHARED_VALUE if page in SHARED_PAGES else page * 10 + 1
        board.load(page, revision=1, value=value)
    return board


# ---------------------------------------------------------------------------
# Part 1: inspection console -> plan -> sign-off -> migration
# ---------------------------------------------------------------------------

@dataclass
class InspectionOutcome:
    strategy: str
    report: InspectionReport
    plan: Optional[MaintenancePlan]
    error: Optional[str] = None


def run_inspection(
    bundle: EvidenceBundle,
    geometry: Geometry,
    current_generation: int,
    strategy: str,
    spare_rows: int,
    scrub_budget: int,
    maintenance_window: int,
) -> InspectionOutcome:
    report = inspect(bundle, geometry, current_generation)
    try:
        plan = plan_maintenance(
            bundle, geometry, current_generation, strategy,
            spare_rows=spare_rows, scrub_budget=scrub_budget,
            maintenance_window=maintenance_window,
        )
        return InspectionOutcome(strategy, report, plan)
    except PlanningError as exc:
        return InspectionOutcome(strategy, report, None, str(exc))


def refuse_double_allocation(plan_a: MaintenancePlan, plan_b: MaintenancePlan) -> None:
    """Guard: no spare row may be promised to both classroom maintenance plans."""

    overlap = plan_a.target_rows & plan_b.target_rows
    if overlap:
        raise PlanningError(
            "spare rows promised to both plans: "
            + ", ".join(sorted(r.label() for r in overlap))
        )


def exercise_baseline_migration(
    geometry: Geometry,
    plan: MaintenancePlan,
    window: int = 12,
) -> tuple[BoardSim, MigrationResult]:
    """Offline the board, run the plan, deliver a mid-flight correction.

    The mid-flight correction targets pg7/ch0, a slice *not* in the baseline
    plan, so it must not corrupt the in-flight moves; it is recorded for a
    follow-up plan.
    """

    board = build_board(geometry, generation=CURRENT_GEN)
    seen_mid: list[CorrectionReceipt] = []

    def mid_hook(sim: BoardSim, result: MigrationResult, step: int) -> None:
        # A correction arrives while the board is mid-migration.  Record it; it
        # must not retroactively alter moves already committed.
        seen_mid.append(fresh_mid_correction())
        result.events.append(
            MigrationEvent(step, "correction-arrived", "pg7/ch0 CE during migration")
        )

    board.offline = True
    try:
        result = execute_plan(plan, board, window=window,
                              mid_hook=mid_hook, hook_at_move=max(1, len(plan.moves) // 2))
    finally:
        # Even if execution fails, bring the board back online for verification.
        board.offline = False
    result.mid_corrections = seen_mid  # type: ignore[attr-defined]
    return board, result


def follow_up_plan(
    geometry: Geometry,
    bundle: EvidenceBundle,
    first_plan: MaintenancePlan,
    mid_receipts: list[CorrectionReceipt],
    spare_rows: int,
    scrub_budget: int,
    window: int,
) -> MaintenancePlan:
    """Extend maintenance to cover evidence that arrived mid-flight."""

    extended = bundle
    for receipt in mid_receipts:
        extended = extended.with_correction(receipt)
    return plan_maintenance(
        extended, geometry, CURRENT_GEN, STRATEGY_SELECTIVE,
        spare_rows=spare_rows, scrub_budget=scrub_budget, maintenance_window=window,
        exclude_spares=frozenset(first_plan.target_rows),
        exclude_sources=frozenset(m.source for m in first_plan.moves),
    )


# ---------------------------------------------------------------------------
# Part 2: classroom programs on the same board, post hand-off
# ---------------------------------------------------------------------------

@dataclass
class ClassroomOutcome:
    client_a: Client
    client_b: Client
    clock: SharedClock
    shared_before: dict[int, tuple[int, int]]
    a_old_revision: int
    a_new_revision: int
    confirmation_ticks: dict[str, Optional[int]]
    isolation_violations: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def exercise_classrooms(
    board: BoardSim,
    shared_pages: tuple[int, ...] = SHARED_PAGES,
    page_a: int = CLASS_A_PAGE,
    page_b: int = CLASS_B_PAGE,
) -> ClassroomOutcome:
    """Both classrooms write, read back, and we check isolation + confirmations."""

    clock = SharedClock()
    a, b = build_classroom_pair(board, shared_pages, page_a, page_b)
    a.clock = clock
    b.clock = clock

    notes: list[str] = []

    # Shared read-only baseline, seen identically by both.
    shared_before = {
        p: board.read(p, f"shared-snapshot-pg{p}")[0] for p in shared_pages
    }
    for page in shared_pages:
        a.read_page(page, receipt_id=f"a-shared-{page}")
        b.read_page(page, receipt_id=f"b-shared-{page}")

    # Each classroom edits its own private correction page, preserving the old
    # revision as reference material (private_history keeps every revision).
    a.write_private(101, receipt_id="a-priv-v1")
    b.write_private(202, receipt_id="b-priv-v1")
    a.read_page(page_a, receipt_id="a-priv-readback-1")
    b.read_page(page_b, receipt_id="b-priv-readback-1")

    a_old_revision = 1
    a.write_private(303, receipt_id="a-priv-v2")   # rewrite, keep old as reference
    a_new_revision = 2
    b.write_private(404, receipt_id="b-priv-v2")

    # Prove the old revision is still available as a reference for comparison.
    notes.append(
        f"class-A keeps old private revision {a_old_revision}={a.revision_view(a_old_revision)} "
        f"alongside new revision {a_new_revision}={a.revision_view(a_new_revision)}"
    )

    # Cross-class isolation probes: each of these MUST be rejected.
    violations: list[str] = [
        _expect_client_error("A writes shared page", lambda: a.write_private(999, "force-shared-w")),
        _expect_client_error("A writes B's private page", lambda: a.write_page(page_b, 888, "force-b-w")),
        _expect_client_error("B writes A's private page", lambda: b.write_page(page_a, 777, "force-a-w")),
        _expect_client_error("A reads B's private page", lambda: a.read_page(page_b, "force-b-r")),
    ]
    # Non-raising sanity: each may read shared pages.
    a.read_page(shared_pages[0], receipt_id="a-shared-reread")

    confirmation_ticks = {}
    for client in (a, b):
        for write, tick in client.confirmations():
            confirmation_ticks[f"{client.name}:{write.receipt_id}"] = tick

    return ClassroomOutcome(
        client_a=a,
        client_b=b,
        clock=clock,
        shared_before=shared_before,
        a_old_revision=a_old_revision,
        a_new_revision=a_new_revision,
        confirmation_ticks=confirmation_ticks,
        isolation_violations=[v for v in violations if v is not None],
        notes=notes,
    )


def _force_write_shared(client: Client):
    client.board.write(client.shared_pages[0], 99, 4242, "force-shared")
    client._guard_write(client.shared_pages[0])


def _force_write_page(client: Client, page: int):
    client._guard_write(page)


def _force_read_page(client: Client, page: int):
    client._guard_read(page)


def _expect_client_error(desc: str, fn) -> str | None:
    """Return ``None`` if the probe was correctly rejected, else a finding."""

    from .client import ClientIsolationError

    try:
        fn()
    except ClientIsolationError:
        return None
    except Exception as exc:  # pragma: no cover - unexpected type
        return f"{desc}: raised unexpected {type(exc).__name__}: {exc}"
    return f"{desc}: NOT rejected (isolation hole)"


def readback_volume(client: Client, prefix: str) -> dict[int, tuple[int, int] | None]:
    return client.read_volume(prefix=prefix)
