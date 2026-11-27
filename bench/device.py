"""High-level scenarios the teaching lab runs on the bench.

The requested narrative:

1. Three consecutive orders; the first two must really draw material under the
   chosen contract.
2. Change *only* the device declarations so the two chassis become the same
   source; re-walk the same batch and check how many remain issuable.
3. Restore the sources; increase *only* the next order's required length; then
   check the available balance.
4. Simulate a lost reply after material was taken; re-fetch by order number and
   recover the *same* receipt/material and the true remaining balance, with no
   double-spend.

The primary contract is METHOD A (partitioned stock).  A companion function
demonstrates METHOD B (the combiner contract).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple
from .content import ChassisDeclaration, DeclarationBook, Feed
from .engine import IssuanceEngine, IssuanceError, Issued
from .reserve import ReserveBook

DEFAULT_LENGTH = 32        # bytes per practice string
DEFAULT_POOL_BYTES = 64    # bytes of prefabricated material per (pool, epoch)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


def _initial_declarations(epoch: int) -> DeclarationBook:
    """Two chassis, initially declared to draw from *different* noise boxes."""
    decls = DeclarationBook()
    decls.set(
        ChassisDeclaration(
            chassis_id="rack-1",
            feeds=(
                Feed(source_name="up-A1", pool_id="noisebox-A", epoch=epoch),
                Feed(source_name="up-B1", pool_id="noisebox-B", epoch=epoch),
            ),
        )
    )
    decls.set(
        ChassisDeclaration(
            chassis_id="rack-2",
            feeds=(Feed(source_name="up-C1", pool_id="noisebox-C", epoch=epoch),),
        )
    )
    return decls


def _same_source_declarations(epoch: int) -> DeclarationBook:
    """Both chassis now name the SAME box; literal names deliberately differ."""
    decls = DeclarationBook()
    decls.set(
        ChassisDeclaration(
            chassis_id="rack-1",
            feeds=(Feed(source_name="up-A1", pool_id="noisebox-A", epoch=epoch),),
        )
    )
    decls.set(
        ChassisDeclaration(
            chassis_id="rack-2",
            feeds=(Feed(source_name="up-A1-dup", pool_id="noisebox-A", epoch=epoch),),
        )
    )
    return decls


def standard_bench(
    *,
    method: str = "partition",
    pool_bytes: int = DEFAULT_POOL_BYTES,
    epoch: int = 1,
) -> Tuple[IssuanceEngine, "ScenarioRunner"]:
    reserves = ReserveBook()
    for pool in ("noisebox-A", "noisebox-B", "noisebox-C"):
        reserves.add(pool, epoch, pool_bytes)
    engine = IssuanceEngine(reserves, _initial_declarations(epoch), method=method)
    return engine, ScenarioRunner(engine)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


@dataclass
class Checkpoint:
    label: str
    length_bytes: int
    independent_bytes: int
    dependent_bytes: int
    paused_bytes: int
    max_independent_orders: int
    fresh_pools: Tuple[str, ...]
    note: str = ""


class ScenarioRunner:
    def __init__(self, engine: IssuanceEngine):
        self.engine = engine

    def checkpoint(self, label: str, length_bytes: int, note: str = "") -> Checkpoint:
        bal = self.engine.balance(length_bytes)
        fresh = tuple(
            f"{p}@{e}" for (p, e) in self.engine.independent_pools(length_bytes)
        )
        return Checkpoint(
            label=label,
            length_bytes=length_bytes,
            independent_bytes=bal["independent_bytes"],
            dependent_bytes=bal["dependent_bytes"],
            paused_bytes=bal["paused_bytes"],
            max_independent_orders=bal["max_independent_orders"],
            fresh_pools=fresh,
            note=note,
        )

    def issue(self, length_bytes: int, order_id: Optional[str] = None) -> Issued:
        return self.engine.issue(length_bytes, order_id=order_id)

    def try_issue(
        self, length_bytes: int, order_id: Optional[str] = None
    ) -> Tuple[Optional[Issued], Optional[str]]:
        try:
            return self.engine.issue(length_bytes, order_id=order_id), None
        except IssuanceError as exc:
            return None, str(exc)


# ---------------------------------------------------------------------------
# Counterfactual re-walk: how many of the same batch could the collapsed
# declarations have served, from the same starting point?
# ---------------------------------------------------------------------------


def _counterfactual_orders(
    source_engine: IssuanceEngine,
    decls: DeclarationBook,
    length_bytes: int,
    n_orders: Optional[int] = None,
) -> dict:
    """Re-walk a batch from a point-in-time snapshot under other declarations.

    ``n_orders`` bounds the walk (e.g. exactly the 3 batch orders); ``None``
    drains until capacity is exhausted so the full count is visible.
    """
    snapshot = source_engine.reserves.clone()
    probe = IssuanceEngine(snapshot, decls, method=source_engine.method)
    probe.paused = set(source_engine.paused)
    limit = n_orders if n_orders is not None else 10_000
    results = []
    for i in range(1, limit + 1):
        ok, why = probe.can_issue(length_bytes)
        if not ok:
            results.append({"stop_after": i - 1, "reason": why})
            break
        issued = probe.issue(length_bytes, order_id=f"probe-{i}")
        results.append(
            {
                "order": i,
                "pool": f"{issued.basis[0][0]}@{issued.basis[0][1]}",
                "receipt": issued.receipt,
            }
        )
    else:
        # Stopped at the requested bound without exhausting capacity.
        ok, why = probe.can_issue(length_bytes)
        if ok:
            results.append({"bound_reached": limit, "more_available": True})
        else:
            results.append({"stop_after": limit, "reason": why})
    return {
        "length_bytes": length_bytes,
        "orders_served": sum(1 for r in results if "order" in r),
        "trace": results,
    }


# ---------------------------------------------------------------------------
# The requested narrative
# ---------------------------------------------------------------------------


def run_narrative(
    engine: IssuanceEngine,
    runner: ScenarioRunner,
    *,
    length: int = DEFAULT_LENGTH,
    epoch: int = 1,
) -> List[dict]:
    out: List[dict] = []
    L = length

    # -- 1) Three consecutive orders; the first two really get material ----
    first_two = []
    for oid in ("ORD-1", "ORD-2"):
        issued = runner.issue(L, order_id=oid)
        first_two.append(
            {
                "order_id": issued.order_id,
                "pool": f"{issued.basis[0][0]}@{issued.basis[0][1]}",
                "receipt": issued.receipt,
                "material": issued.material_hex,
            }
        )
    cp_two = runner.checkpoint("after-first-two", L)
    out.append(
        {
            "step": "1_first_two_orders",
            "issued": first_two,
            "checkpoint": cp_two.__dict__,
        }
    )

    # Baseline: how many total orders this batch could serve before we touch
    # the declarations (so step 2's change is isolated).
    baseline_orders = _counterfactual_orders(engine, engine.declarations, L)
    out.append(
        {
            "step": "1b_baseline_capacity",
            "declarations": "initial (different boxes)",
            **baseline_orders,
        }
    )

    # -- 2) Change ONLY declarations: both chassis now name the SAME box ----
    same = _same_source_declarations(epoch)
    # Re-walk the SAME 3-order batch from the identical live state (after the
    # first two orders), under two declaration sets.  Only declarations differ;
    # the point-in-time clone means neither probe spends step 3's material.
    #   (a) original declarations -> how many of the 3 can be served?
    #   (b) same-source declarations -> how many of the 3 can be served?
    #   (c) same-source, drained -> total remaining issuable capacity.
    orig_batch = _counterfactual_orders(engine, engine.declarations, L, n_orders=3)
    same_batch = _counterfactual_orders(engine, same, L, n_orders=3)
    same_total = _counterfactual_orders(engine, same, L)
    engine.replace_declarations(same)
    cp_same = runner.checkpoint("same-source", L)
    out.append(
        {
            "step": "2_same_source_rewalk",
            "note": "only device declarations changed; names differ literally",
            "declared_pools": {
                f"{p}@{e}": sorted(cids)
                for (p, e), cids in engine.declared_pools().items()
            },
            "three_order_batch_original_decls": orig_batch,
            "three_order_batch_same_source": same_batch,
            "total_capacity_if_rewalked": same_total,
            "checkpoint": cp_same.__dict__,
            "issuable_now": cp_same.max_independent_orders,
        }
    )

    # -- 3) Restore sources; increase ONLY the next order's length ---------
    engine.replace_declarations(_initial_declarations(epoch))
    bigger = 2 * L
    cp_restored = runner.checkpoint("restored", L)
    cp_bigger = runner.checkpoint("restored-bigger", bigger)
    o3 = runner.issue(bigger, order_id="ORD-3")
    cp_after = runner.checkpoint("after-ORD-3", bigger)
    out.append(
        {
            "step": "3_restore_and_bigger_length",
            "restored_pools": {
                f"{p}@{e}": sorted(cids)
                for (p, e), cids in engine.declared_pools().items()
            },
            "balance_at_original_len": cp_restored.__dict__,
            "balance_at_bigger_len": cp_bigger.__dict__,
            "issued_bigger": {
                "order_id": o3.order_id,
                "length_bytes": o3.length_bytes,
                "pool": f"{o3.basis[0][0]}@{o3.basis[0][1]}",
                "receipt": o3.receipt,
                "material": o3.material_hex,
            },
            "checkpoint_after_ORD_3": cp_after.__dict__,
        }
    )

    # -- 4) Lost reply after material was taken; recover by order number ---
    # Two entry points, both keyed only by the order number:
    #   (a) a naive resend through issue() -- must be idempotent, no re-spend;
    #   (b) a receipt lookup through refetch().
    before = runner.checkpoint("before-reclaim", L)
    resend = engine.issue(o3.length_bytes, order_id="ORD-3")   # (a) resend
    lookup = engine.refetch("ORD-3", at_length=L)              # (b) lookup
    after = runner.checkpoint("after-reclaim", L)
    out.append(
        {
            "step": "4_lost_reply_refetch",
            "order_id": lookup.order_id,
            "same_receipt": lookup.receipt == o3.receipt,
            "same_material": lookup.material_hex == o3.material_hex,
            "resend_same_receipt": resend.receipt == o3.receipt,
            "resend_same_material": resend.material_hex == o3.material_hex,
            "true_remaining_at_original_len": lookup.remaining,
            "balance_before_refetch": before.__dict__,
            "balance_after_refetch": after.__dict__,
            "no_double_spend": (
                before.independent_bytes == after.independent_bytes
                and before.dependent_bytes == after.dependent_bytes
            ),
        }
    )
    return out


# ---------------------------------------------------------------------------
# Companion: METHOD B (combiner) demo
# ---------------------------------------------------------------------------


def run_combiner_demo(L: int = 64) -> List[dict]:
    """Show the combiner contract: one independent head + dependent paths."""
    from .combiner import CombinerContract

    out: List[dict] = []
    reserves = ReserveBook()
    reserves.add("box-H", 1, 128)   # fresh, independent head volume
    reserves.add("box-D", 1, 128)   # dependent volume, declared synthetic only
    decls = DeclarationBook()
    decls.set(
        ChassisDeclaration(
            chassis_id="rack-1",
            feeds=(Feed(source_name="h", pool_id="box-H", epoch=1),),
        )
    )
    # box-D is named only by a *synthetic* feed -> it cannot back independence,
    # but it is fine as a dependent combiner path.
    decls.set(
        ChassisDeclaration(
            chassis_id="rack-synth",
            feeds=(Feed(source_name="d", pool_id="box-D", epoch=1, synthetic=True),),
        )
    )
    engine = IssuanceEngine(reserves, decls, method="combiner")
    runner = ScenarioRunner(engine)

    out.append({"step": "combiner_start", **runner.checkpoint("start", L).__dict__})
    issued = runner.issue(L, order_id="CMB-1")
    out.append(
        {
            "step": "combiner_issued",
            "contract": CombinerContract().text,
            "order_id": issued.order_id,
            "basis": [f"{p}@{e}" for p, e in issued.basis],
            "material": issued.material_hex,
            "receipt": issued.receipt,
        }
    )
    out.append({"step": "combiner_after", **runner.checkpoint("after", L).__dict__})

    # With the independent head paused, remaining dependent-only volume must not
    # be able to mint another independent output.
    engine.pause_pool(("box-H", 1))
    cp_paused = runner.checkpoint("head-paused", L)
    try:
        runner.issue(L, order_id="CMB-2")
        out.append({"step": "combiner_second", "issued": "unexpectedly-yes"})
    except IssuanceError as exc:
        out.append(
            {
                "step": "combiner_second",
                "checkpoint": cp_paused.__dict__,
                "refused": str(exc),
                "why": "only dependent volume remains; it cannot supply independence",
            }
        )
    return out


# ---------------------------------------------------------------------------
# Companion: device pause and the promise to the next customer
# ---------------------------------------------------------------------------


def run_pause_scenario(L: int = DEFAULT_LENGTH) -> List[dict]:
    """Show what the bench may promise the *next* customer after a pause.

    Policy (stated in the charter): a pause subtracts the paused source from
    the promise until it is re-verified.  We promise only verified material.
    """
    engine, runner = standard_bench(method="partition")
    out: List[dict] = []

    before = runner.checkpoint("before-pause", L)
    out.append({"step": "pause_before", **before.__dict__})

    engine.pause_pool(("noisebox-C", 1))
    paused = runner.checkpoint("after-pause-C", L)
    out.append(
        {
            "step": "pause_after_C",
            "checkpoint": paused.__dict__,
            "promised_to_next": paused.max_independent_orders,
            "why": "paused source is excluded until re-verified",
        }
    )

    # The paused source must not serve an order even though bytes remain.
    # Drain the two still-verified sources first.
    served = []
    for i in range(1, 8):
        issued, err = runner.try_issue(L, order_id=f"P-{i}")
        if err:
            out.append(
                {
                    "step": "pause_stop",
                    "served_while_C_paused": served,
                    "refused_after": i - 1,
                    "error": err,
                }
            )
            break
        served.append(f"{issued.order_id}:{issued.basis[0][0]}")

    engine.resume_pool(("noisebox-C", 1))
    resumed = runner.checkpoint("after-resume-C", L)
    out.append(
        {
            "step": "pause_resume_C",
            "checkpoint": resumed.__dict__,
            "promised_to_next": resumed.max_independent_orders,
        }
    )
    return out
