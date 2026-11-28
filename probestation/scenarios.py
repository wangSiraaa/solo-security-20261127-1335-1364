"""Worked exercises: the calibration room's end-to-end runs on fictional rigs.

These functions build a disposable rig, run a strategy, verify the hand-off,
reconstruct, and calibrate.  They are used by the demo and the tests, and
they double as the "answer sheets" for the two required cases:

* ``scenario_a_unique_from_bijection`` -- a slot that is *individually*
  consistent with two probes, but the reliable reads in the other slots plus
  the one-probe-per-slot fact pin it uniquely.
* ``scenario_b_genuine_swap`` -- two slots really may be swapped, everything
  else is named.  The report keeps the two raw and emits no guessed identity.

It also covers:

* a *complete* recovery sheet within the beat budget (fewer beats than probes,
  so one-at-a-time calling cannot fit), with the per-slot single-erasure
  guarantee;
* degrading the same record (remove one cell; swap one receipt for last
  shift's) and asking *which probes still have grounds*;
* restoring attestation material and changing only the room's curves, proving
  attribution is unchanged while converted values change;
* a wiring-sheet for the beyond-guarantee part that leaves checkable
  ambiguity instead of a guess.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Sequence, Tuple

from .calibrate import (
    SlotReading,
    alt_curves,
    default_curves,
    issue_report,
)
from .entities import ProbeRegistry
from .plan import (
    PRESET_CODEWORDS,
    RollcallStrategy,
    preset_beats,
    verify_injectivity,
)
from .reconstruct import Reconstruction, ValidBeat, reconstruct, swap_witnesses
from .rig import SLOT_LABELS, Rig, SamplingLog, Session

PIDS_DEFAULT = ("P-A1", "P-B2", "P-C3", "P-D4", "P-E5", "P-F6")

# Identity orders used as *physical insertions* in the exercises.  Note these
# are positions chosen by the repair technician; they are never used as an
# identity key by the verifier.
INSERTION_A = dict(zip(SLOT_LABELS, ("P-B2", "P-A1", "P-C3", "P-D4", "P-E5", "P-F6")))
INSERTION_B = dict(zip(SLOT_LABELS, ("P-A1", "P-B2", "P-C3", "P-D4", "P-E5", "P-F6")))


# --------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------
@dataclass
class WorkedRun:
    registry: ProbeRegistry
    rig: Rig
    session: Session
    bench_key: bytes
    log: SamplingLog
    valid: List[ValidBeat]
    rejected: List[object]
    reconstruction: Reconstruction

    def readings(self, raws: Dict[str, int]) -> List[SlotReading]:
        return [SlotReading(s, raws[s]) for s in SLOT_LABELS]


def _new_rig(exercise: str, insertion: Dict[str, str], bench_key: Optional[bytes] = None):
    reg = ProbeRegistry(PIDS_DEFAULT)
    reg.start_exercise(exercise)
    rig = Rig(reg)
    rig.insert(insertion)
    bench = rig.make_bench()
    return reg, rig, bench


def run_preplan(
    insertion: Dict[str, str],
    session: Session,
    exercise: str,
    missing_per_beat: Optional[Dict[int, Sequence[str]]] = None,
    receipts_override: Optional[Dict[int, Dict[str, dict]]] = None,
    bench_key: Optional[bytes] = None,
) -> WorkedRun:
    """Run the fixed parallel schedule and reconstruct from the verified log."""
    reg, rig, bench = _new_rig(exercise, insertion, bench_key)
    missing_per_beat = missing_per_beat or {}
    overrides = receipts_override or {}
    log = SamplingLog()
    for beat, call in preset_beats():
        kwargs = {}
        if overrides.get(beat) is not None:
            kwargs["receipts_override"] = overrides[beat]
        log.add(
            bench.record_beat(
                beat,
                call,
                session,
                missing_slots=missing_per_beat.get(beat, ()),
                **kwargs,
            )
        )
    vlog = log.verify(session, reg, bench.sign_key())
    recon = reconstruct(vlog.beats, reg.probe_ids, SLOT_LABELS, vlog.authenticated)
    return WorkedRun(reg, rig, session, bench.sign_key(), log, vlog.beats, vlog.rejected, recon)


def run_rollcall(
    insertion: Dict[str, str],
    session: Session,
    exercise: str,
    budget: int = 5,
    missing_by_beat: Optional[Dict[int, Sequence[str]]] = None,
) -> Tuple[object, WorkedRun]:
    """Run the adaptive on-site roll-caller."""
    reg, rig, bench = _new_rig(exercise, insertion)
    strat = RollcallStrategy(reg.probe_ids, SLOT_LABELS)
    missfn = lambda b: set(missing_by_beat.get(b, ())) if missing_by_beat else set()
    result = strat.run(bench, session, reg, budget, missfn)
    # Re-verify the full stack independently.
    vlog = SamplingLog([t.rec for t in result.turns]).verify(session, reg, bench.sign_key())
    recon = reconstruct(vlog.beats, reg.probe_ids, SLOT_LABELS, vlog.authenticated)
    run = WorkedRun(reg, rig, session, bench.sign_key(), SamplingLog([t.rec for t in result.turns]),
                    vlog.beats, vlog.rejected, recon)
    return result, run


# --------------------------------------------------------------------------
# The recovery sheet (complete within budget) + guarantee
# --------------------------------------------------------------------------
@dataclass
class RecoverySheet:
    beats: List[Tuple[int, Tuple[str, ...]]]
    n_beats: int
    n_probes: int
    codewords: Dict[str, str]
    guarantee: str

    def as_dict(self) -> dict:
        return {
            "beats": [(b, list(c)) for b, c in self.beats],
            "n_beats": self.n_beats,
            "n_probes": self.n_probes,
            "codewords": self.codewords,
            "guarantee": self.guarantee,
        }


def recovery_sheet() -> RecoverySheet:
    beats = preset_beats()
    return RecoverySheet(
        beats=beats,
        n_beats=len(beats),
        n_probes=len(PRESET_CODEWORDS),
        codewords=dict(PRESET_CODEWORDS),
        guarantee=(
            "并行 5 拍 < 6 支探头，逐个叫号超出预算；"
            "任一物理槽整轮至多缺读 1 格时，所有插入方式仍被唯一确定"
            "（码距>=2，缺读格对任意候选插入是同一物理格）。"
        ),
    )


def prove_sheet(sheet: Optional[RecoverySheet] = None) -> bool:
    """Heavy exhaustive proof of the single-erasure-per-slot guarantee."""
    sheet = sheet or recovery_sheet()
    ok, _witness = verify_injectivity(sheet.codewords, SLOT_LABELS, PIDS_DEFAULT, 1)
    return ok


# --------------------------------------------------------------------------
# Scenario A: individually 2-consistent, uniquely fixed by the rest + bijection
# --------------------------------------------------------------------------
SCENARIO_A_MISSING = {0: ("甲",), 1: ("甲",)}


def scenario_a_unique_from_bijection() -> WorkedRun:
    sess = Session.new()
    return run_preplan(INSERTION_A, sess, "EX-A", missing_per_beat=SCENARIO_A_MISSING)


# --------------------------------------------------------------------------
# Scenario B: a genuine two-slot (甲/乙) swap, the rest still named
# --------------------------------------------------------------------------
# Probes P-A1 (code 00001) and P-B2 (code 00010) are distinguished only by
# beats 3 and 4.  If slots 甲,乙 both lose exactly those two reads, A1 and B2
# can be exchanged between 甲/乙 with no surviving observation to tell them
# apart -- a real, irreducible swap.  Every other slot stays pinned.
SCENARIO_B_MISSING = {3: ("甲", "乙"), 4: ("甲", "乙")}


def scenario_b_genuine_swap() -> WorkedRun:
    sess = Session.new()
    return run_preplan(INSERTION_B, sess, "EX-B", missing_per_beat=SCENARIO_B_MISSING)


# --------------------------------------------------------------------------
# Degradation checks
# --------------------------------------------------------------------------
def drop_one_cell(run: WorkedRun, beat: int, slot: str) -> Reconstruction:
    """Remove one response cell from an otherwise-valid beat and re-check."""
    adjusted: List[ValidBeat] = []
    for vb in run.valid:
        bits = dict(vb.bits)
        if vb.beat == beat:
            bits[slot] = None  # explicit missing read
        adjusted.append(
            ValidBeat(vb.beat, vb.challenge_id, vb.call_set, bits, vb.trusted_call_set)
        )
    # Keep the same authenticated roster: this isolates the effect of the lost
    # *bit* alone (no receipt changes).
    auth = {p for vb in run.valid for p in vb.trusted()}
    return reconstruct(adjusted, run.registry.probe_ids, SLOT_LABELS, auth)


def stale_receipt_run(insertion: Dict[str, str], exercise: str) -> Tuple[WorkedRun, WorkedRun]:
    """Return (honest run, run with one receipt replaced by a last-shift one).

    ``P-A1`` is called only in beat 4.  Replacing that single receipt with a
    last-shift one leaves the *bits* intact but removes P-A1's only valid
    current receipt, so slot 甲 keeps its physical membership but loses its
    naming grounds (the room must not print a name).  All other slots keep
    their grounds.
    """
    cur = Session.new()
    old = Session.new()  # previous shift, different nonce
    honest = run_preplan(insertion, cur, exercise + "-honest")

    # Deterministic replacement: beat 4's call set with P-A1 answered by the
    # old session (its nonce/challenge are foreign to *this* session).
    reg, rig, bench = _new_rig(exercise + "-stale", insertion, bench_key=honest.bench_key)
    override: Dict[int, Dict[str, dict]] = {}
    for beat, call in preset_beats():
        if beat == 4 and "P-A1" in call:
            override[beat] = {
                p: (
                    rig.probe(p).answer(cur.nonce, cur.challenge(beat))
                    if p != "P-A1"
                    else rig.probe("P-A1").answer(old.nonce, old.challenge(beat))
                )
                for p in call
            }
    stale = run_preplan(
        insertion, cur, exercise + "-stale", receipts_override=override, bench_key=honest.bench_key
    )
    return honest, stale


def grounds(rec: Reconstruction) -> Dict[str, Tuple[str, ...]]:
    """Which identities still have grounds per slot after a degradation."""
    return rec.grounds()


def reenroll_same_roster(insertion: Dict[str, str]) -> Tuple[WorkedRun, WorkedRun]:
    """Restore / re-issue attestation material for the same roster.

    Start from the degraded state (slot 甲 lost naming grounds via a stale
    receipt), then "restore" the material by obtaining a valid current
    receipt for P-A1.  Restoring the paper only re-adds the *name*; it does
    not move the physical attribution, which was already P-A1 by the bits.
    """
    _honest, degraded = stale_receipt_run(insertion, "EX-RESTORE")
    session = degraded.session
    reg = degraded.registry

    # Restore: re-issue this session's proof for P-A1 (the probe answers again).
    # Work on copies so the degraded run's log stays intact for comparison.
    ch = session.challenge(4)
    fresh = reg.probe("P-A1").answer(session.nonce, ch)
    restored_records = []
    for rec in degraded.log.records:
        if rec.beat == 4 and "P-A1" in rec.receipts:
            receipts = dict(rec.receipts)
            receipts["P-A1"] = fresh
            rec = replace(rec, receipts=receipts)
        restored_records.append(rec)
    restored_log = SamplingLog(restored_records)
    vlog = restored_log.verify(session, reg, degraded.bench_key)
    recon = reconstruct(vlog.beats, reg.probe_ids, SLOT_LABELS, vlog.authenticated)
    restored = WorkedRun(
        reg, degraded.rig, session, degraded.bench_key, restored_log,
        vlog.beats, vlog.rejected, recon,
    )
    return degraded, restored


# --------------------------------------------------------------------------
# Curve-only change: attribution fixed, values change
# --------------------------------------------------------------------------
RAWS = {"甲": 150, "乙": 200, "丙": 175, "丁": 140, "戊": 220, "己": 160}


def curve_only_change(run: WorkedRun) -> Tuple[object, object]:
    """Issue the report under default curves, then under alternate curves.

    Attribution must be identical; converted values must differ for every
    named slot.
    """
    readings = run.readings(RAWS)
    rep_default = issue_report(readings, run.reconstruction, default_curves())
    rep_alt = issue_report(readings, run.reconstruction, alt_curves())
    return rep_default, rep_alt


# --------------------------------------------------------------------------
# Wiring sheet for the beyond-guarantee part: documented ambiguity
# --------------------------------------------------------------------------
def sheet_with_checkable_ambiguity(insertion: Dict[str, str], missing: Dict[int, Sequence[str]]) -> Tuple[WorkedRun, Dict[str, object]]:
    """Run under a deliberately over-budget erasure; keep only the part that
    can be guaranteed, and document the local ambiguity with witnesses."""
    sess = Session.new()
    run = run_preplan(insertion, sess, "EX-OVER", missing_per_beat=missing)
    witnesses = swap_witnesses(run.reconstruction)
    return run, witnesses
