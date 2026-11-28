"""Tests for the probe certification station simulation."""

from __future__ import annotations

import itertools
import random

from probestation.calibrate import (
    alt_curves,
    default_curves,
    issue_report,
    piecewise_linear,
)
from probestation.crypto import mac, verify_mac
from probestation.entities import ProbeRegistry
from probestation.plan import (
    PRESET_CODEWORDS,
    PreplanStrategy,
    preset_beats,
    schedule_from_codewords,
)
from probestation.reconstruct import (
    reconstruct,
    swap_witnesses,
)
from probestation.rig import (
    SLOT_LABELS,
    Rig,
    SamplingLog,
    Session,
)
from probestation.scenarios import (
    INSERTION_A,
    INSERTION_B,
    PIDS_DEFAULT,
    curve_only_change,
    drop_one_cell,
    recovery_sheet,
    run_preplan,
    run_rollcall,
    scenario_a_unique_from_bijection,
    scenario_b_genuine_swap,
    sheet_with_checkable_ambiguity,
    stale_receipt_run,
)

SLOTS = SLOT_LABELS
PIDS = PIDS_DEFAULT


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _fresh(insertion=INSERTION_B, exercise="T"):
    reg = ProbeRegistry(PIDS)
    reg.start_exercise(exercise)
    rig = Rig(reg)
    rig.insert(insertion)
    bench = rig.make_bench()
    return reg, rig, bench


def _run(insertion, missing=None):
    sess = Session.new()
    return run_preplan(insertion, sess, "T", missing_per_beat=missing or {})


# --------------------------------------------------------------------------
# crypto / entities
# --------------------------------------------------------------------------
def test_mac_roundtrip_and_tamper():
    key = b"k"
    obj = {"a": 1, "b": [2, 3]}
    tag = mac(key, obj)
    assert verify_mac(key, obj, tag)
    assert not verify_mac(key, {"a": 1, "b": [2, 4]}, tag)
    assert not verify_mac(b"other", obj, tag)


def test_attestation_has_no_slot():
    reg = ProbeRegistry(PIDS)
    reg.start_exercise("EX")
    att = reg.attestation("P-A1").as_dict()
    assert set(att) == {"probe_id", "fingerprint", "mgmt_tag"}
    for bad in SLOTS + ("slot", "bed", "index"):
        assert bad not in att


def test_answer_requires_current_nonce():
    reg = ProbeRegistry(PIDS)
    reg.start_exercise("EX")
    p = reg.probe("P-A1")
    nonce = "this-session"
    good = p.answer(nonce, "c1")
    assert reg.verify_answer(good, nonce, "c1")
    # old / foreign nonce
    assert not reg.verify_answer(good, "last-shift", "c1")
    # wrong challenge
    assert not reg.verify_answer(good, nonce, "c2")
    # forged tag
    forged = dict(good)
    forged["tag"] = "00" * 32
    assert not reg.verify_answer(forged, nonce, "c1")


def test_answer_is_named_only():
    """A probe answers only when named (callers name probes); no slot in it."""
    reg = ProbeRegistry(PIDS)
    reg.start_exercise("EX")
    ans = reg.probe("P-A1").answer("n", "c")
    assert set(ans) == {"probe_id", "nonce", "challenge_id", "tag", "fingerprint"}
    assert ans["probe_id"] == "P-A1"


# --------------------------------------------------------------------------
# rig / wiring isolation
# --------------------------------------------------------------------------
def test_certify_never_touches_wiring():
    """Corrupting the wiring after recording must not change any result."""
    reg, rig, bench = _fresh()
    sess = Session.new()
    log = SamplingLog()
    for b, call in preset_beats():
        log.add(bench.record_beat(b, call, sess))
    before = log.as_dicts()
    vlog = log.verify(sess, reg, bench.sign_key())
    r1 = reconstruct(vlog.beats, PIDS, SLOTS, vlog.authenticated)

    rig.bus.corrupt()  # hardware wiring is now broken
    # Everything below runs only on already-recorded data.
    vlog2 = SamplingLog([r for r in log.records]).verify(sess, reg, bench.sign_key())
    r2 = reconstruct(vlog2.beats, PIDS, SLOTS, vlog2.authenticated)
    assert r1.fixed == r2.fixed == INSERTION_B
    assert before == log.as_dicts()  # records untouched


def test_bits_are_per_slot_only():
    """A beat carries one bit per slot and, beyond the asked call set, no
    identity-per-slot mapping."""
    reg, rig, bench = _fresh()
    sess = Session.new()
    b, call = preset_beats()[0]
    rec = bench.record_beat(b, call, sess)
    assert set(rec.bits) == set(SLOTS)
    assert all(v in (0, 1, None) for v in rec.bits.values())
    # the record exposes physical insertions only as per-slot bits; the
    # slip carries the asked call set (which the room already knows) and
    # per-probe receipts, but never a probe->slot association.
    d = rec.as_dict()
    assert set(d) >= {"beat", "nonce", "challenge_id", "call_set", "bits", "receipts", "bench_tag"}
    assert "slots" not in d and "perm" not in d and "mapping" not in d


# --------------------------------------------------------------------------
# verification / replay
# --------------------------------------------------------------------------
def test_stale_record_rejected():
    reg, rig, bench = _fresh()
    old = Session.new()
    log = SamplingLog()
    for b, call in preset_beats():
        log.add(bench.record_beat(b, call, old))
    # Hand the old log to a *new* session.
    cur = Session.new()
    vlog = log.verify(cur, reg, bench.sign_key())
    assert vlog.beats == []
    assert len(vlog.rejected) == len(preset_beats())


def test_tampered_bit_rejected():
    reg, rig, bench = _fresh()
    sess = Session.new()
    rec = bench.record_beat(0, preset_beats()[0][1], sess)
    # flip a bit after signing
    rec.bits["甲"] = 1 - (rec.bits["甲"] or 0)
    vlog = SamplingLog([rec]).verify(sess, reg, bench.sign_key())
    assert vlog.beats == []
    assert vlog.rejected[0].beat == 0


# --------------------------------------------------------------------------
# reconstruction correctness
# --------------------------------------------------------------------------
def test_full_recovery_no_missing():
    run = _run(INSERTION_B)
    assert run.reconstruction.complete
    assert run.reconstruction.fixed == INSERTION_B


def test_all_insertions_recovered_without_missing():
    """Every physical insertion is uniquely recovered (720 cases)."""
    sess = Session.new()
    for pm in itertools.permutations(PIDS):
        perm = dict(zip(SLOTS, pm))
        reg = ProbeRegistry(PIDS)
        reg.start_exercise("U")
        rig = Rig(reg)
        rig.insert(perm)
        bench = rig.make_bench()
        log = SamplingLog()
        for b, call in preset_beats():
            log.add(bench.record_beat(b, call, sess))
        vlog = log.verify(sess, reg, bench.sign_key())
        r = reconstruct(vlog.beats, PIDS, SLOTS, vlog.authenticated)
        assert r.complete and r.fixed == perm


def test_identity_not_from_position():
    """Re-insert the same identities in a different order: names follow."""
    sess = Session.new()
    perm = dict(zip(SLOTS, ("P-C3", "P-F6", "P-A1", "P-E5", "P-B2", "P-D4")))
    reg = ProbeRegistry(PIDS)
    reg.start_exercise("U")
    rig = Rig(reg)
    rig.insert(perm)
    bench = rig.make_bench()
    log = SamplingLog()
    for b, call in preset_beats():
        log.add(bench.record_beat(b, call, sess))
    vlog = log.verify(sess, reg, bench.sign_key())
    r = reconstruct(vlog.beats, PIDS, SLOTS, vlog.authenticated)
    assert r.fixed == perm


def test_missing_cells_tolerated():
    """<=1 lost read per slot is always uniquely recovered (sampled)."""
    random.seed(7)
    sess = Session.new()
    for _ in range(40):
        pm = random.choice(list(itertools.permutations(PIDS)))
        perm = dict(zip(SLOTS, pm))
        missing = {}
        for s in SLOTS:
            if random.random() < 0.6:
                b = random.randrange(len(preset_beats()))
                missing.setdefault(b, []).append(s)
        reg, rig, bench = _fresh(perm, "M")
        log = SamplingLog()
        for b, call in preset_beats():
            log.add(bench.record_beat(b, call, sess, missing_slots=missing.get(b, ())))
        vlog = log.verify(sess, reg, bench.sign_key())
        r = reconstruct(vlog.beats, PIDS, SLOTS, vlog.authenticated)
        assert r.complete and r.fixed == perm, (pm, missing, r.ambiguous)


def test_empty_domain_never_from_missing():
    """Missing reads can never create an inconsistency."""
    sess = Session.new()
    reg, rig, bench = _fresh(INSERTION_B)
    log = SamplingLog()
    # make the first two beats fully dark
    for b, call in preset_beats():
        log.add(bench.record_beat(b, call, sess, missing_slots=SLOTS if b < 2 else ()))
    vlog = log.verify(sess, reg, bench.sign_key())
    r = reconstruct(vlog.beats, PIDS, SLOTS, vlog.authenticated)
    assert r.rejected_reason is None


# --------------------------------------------------------------------------
# schedule properties
# --------------------------------------------------------------------------
def test_schedule_budget_and_balance():
    beats = preset_beats()
    assert len(beats) < len(PIDS)  # fewer beats than probes
    cols = [0] * 5
    for b, call in beats:
        for p in call:
            cols[b] += 1
    assert all(2 <= c <= 4 for c in cols), cols


def test_schedule_min_distance_two():
    codes = [int(c, 2) for c in PRESET_CODEWORDS.values()]
    for a, b in itertools.combinations(codes, 2):
        assert bin(a ^ b).count("1") >= 2


def test_codeword_schedule_builder():
    cw = {"P-A1": "100", "P-B2": "010", "P-C3": "001"}
    beats, width = schedule_from_codewords(cw)
    assert width == 3
    assert beats[0][1] == ("P-A1",)
    assert beats[2][1] == ("P-C3",)


def test_recovery_sheet_guarantee_text():
    sheet = recovery_sheet()
    assert sheet.n_beats < sheet.n_probes
    assert "唯一确定" in sheet.guarantee


# --------------------------------------------------------------------------
# required narrative scenarios
# --------------------------------------------------------------------------
def test_scenario_a_unique_via_bijection():
    run = scenario_a_unique_from_bijection()
    r = run.reconstruction
    assert r.complete
    assert r.fixed == INSERTION_A
    # slot 甲, alone, is still 2-consistent...
    assert len(r.raw_domains["甲"]) == 2
    # ...but the other slots + one-per-slot bijection pin it uniquely.
    assert r.evidence["甲"].kind == "bijection"
    # every other slot is directly pinned
    others = [s for s in SLOTS if s != "甲"]
    assert all(r.evidence[s].kind == "direct" for s in others)


def test_scenario_b_genuine_swap():
    run = scenario_b_genuine_swap()
    r = run.reconstruction
    # exactly two slots (甲/乙) may swap; all other slots are named and correct
    assert set(r.ambiguous) == {"甲", "乙"}
    assert r.ambiguous["甲"] == r.ambiguous["乙"] == ("P-A1", "P-B2")
    named = list(r.fixed)
    assert set(named) == {"丙", "丁", "戊", "己"}
    for s in named:
        assert r.fixed[s] == INSERTION_B[s]
    wit = swap_witnesses(r)
    assert set(wit["completions"][0]) == {"甲", "乙"}


def test_unresolved_slot_gets_no_name_in_report():
    run = scenario_b_genuine_swap()
    rep = issue_report(run.readings({s: 150 for s in SLOTS}), run.reconstruction, default_curves())
    lines = {ln.slot: ln for ln in rep.lines}
    for s in ("甲", "乙"):
        assert lines[s].identity is None
        assert lines[s].converted is None
        assert lines[s].basis == "unresolved-raw-only"
        assert lines[s].raw == 150
    assert rep.unresolved_slots == ["甲", "乙"]
    for s in ("丙", "丁", "戊", "己"):
        assert lines[s].identity is not None
        assert lines[s].converted is not None


# --------------------------------------------------------------------------
# degradation
# --------------------------------------------------------------------------
def test_drop_one_cell_keeps_grounds():
    run = scenario_a_unique_from_bijection()
    # remove the second missing cell's read (make it *another* missing) -> still complete
    d = drop_one_cell(run, 2, "甲")
    assert d.complete
    assert d.fixed == INSERTION_A


def test_drop_one_cell_from_complete_run_keeps_grounds():
    full = _run(INSERTION_B)
    assert full.reconstruction.complete
    d = drop_one_cell(full, 4, "甲")
    # still has grounds for every slot; 甲 stays pinned to P-A1
    assert d.grounds()["甲"] == ("P-A1",)


def test_stale_receipt_removes_naming_grounds():
    honest, stale = stale_receipt_run(INSERTION_B, "ST")
    assert honest.reconstruction.complete
    # stale run: bits still pin 甲's physical membership, but no valid current
    # receipt -> cannot name it.
    assert not stale.reconstruction.complete
    assert stale.reconstruction.reasons.get("甲")
    # the pin is still P-A1: grounds are not lost, only the printable name
    assert stale.reconstruction.grounds()["甲"] == ("P-A1",)
    # other slots unaffected
    named = stale.reconstruction.fixed
    assert set(named) == {"乙", "丙", "丁", "戊", "己"}
    for s in named:
        assert named[s] == INSERTION_B[s]


def test_physically_pinned_but_unnamed_stays_raw():
    """A slot pinned by bits but lacking a current receipt keeps raw, no name."""
    from probestation.calibrate import SlotReading
    from probestation.scenarios import RAWS

    _honest, stale = stale_receipt_run(INSERTION_B, "ST2")
    rep = issue_report(
        [SlotReading(s, RAWS[s]) for s in SLOTS],
        stale.reconstruction,
        default_curves(),
    )
    line = next(l for l in rep.lines if l.slot == "甲")
    assert line.identity is None
    assert line.converted is None
    assert line.raw == RAWS["甲"]
    assert line.basis == "unresolved-raw-only"
    assert line.evidence["ambiguous_candidates"] == ["P-A1"]


def test_stale_receipt_record_itself_not_rejected():
    """A stale *receipt* does not reject the beat's bits (only its naming)."""
    honest, stale = stale_receipt_run(INSERTION_B, "ST")
    assert stale.rejected == []
    assert stale.reconstruction.rejected_reason is None


# --------------------------------------------------------------------------
# curve-only change
# --------------------------------------------------------------------------
def test_reenroll_preserves_attribution():
    from probestation.scenarios import reenroll_same_roster

    degraded, restored = reenroll_same_roster(INSERTION_B)
    # degraded: 甲 unnamed (only physical pin), everything else named
    assert not degraded.reconstruction.complete
    assert degraded.reconstruction.grounds()["甲"] == ("P-A1",)
    # restored: fresh receipt re-adds the name; attribution identical
    assert restored.reconstruction.complete
    assert restored.reconstruction.fixed == INSERTION_B
    for s in SLOTS:
        assert restored.reconstruction.fixed[s] == INSERTION_B[s]


def test_curve_change_keeps_attribution_changes_values():
    run = _run(INSERTION_B)
    rep_def, rep_alt = curve_only_change(run)
    assert [ln.identity for ln in rep_def.lines] == [ln.identity for ln in rep_alt.lines]
    for a, b in zip(rep_def.lines, rep_alt.lines):
        if a.identity is not None:
            assert a.converted != b.converted
            assert a.raw == b.raw
    assert rep_def.curve_revision != rep_alt.curve_revision


def test_piecewise_linear_clamps_and_interpolates():
    curve = [(100, 1.0), (200, 3.0)]
    assert piecewise_linear(curve, 50) == 1.0
    assert piecewise_linear(curve, 250) == 3.0
    assert abs(piecewise_linear(curve, 150) - 2.0) < 1e-9


# --------------------------------------------------------------------------
# beyond guarantee: documented ambiguity, never a guess
# --------------------------------------------------------------------------
def test_beyond_guarantee_keeps_ambiguity_with_witness():
    run, wit = sheet_with_checkable_ambiguity(INSERTION_B, {0: ("甲", "丁"), 2: ("甲", "丁")})
    assert not run.reconstruction.complete
    assert set(run.reconstruction.ambiguous) == {"甲", "丁"}
    # never a wrong name
    for s in run.reconstruction.fixed:
        assert run.reconstruction.fixed[s] == INSERTION_B[s]
    assert wit["completions"]


def test_beyond_guarantee_never_wrong_on_exhaustive_scan():
    """On a heavier erasure, any named slot is still correct for *every* insertion."""
    mc = {0: ("甲", "丁", "乙"), 1: ("甲", "丁", "乙"), 2: ("甲", "丁", "乙")}
    sess = Session.new()
    for pm in itertools.permutations(PIDS):
        perm = dict(zip(SLOTS, pm))
        reg, rig, bench = _fresh(perm, "BG")
        log = SamplingLog()
        for b, call in preset_beats():
            log.add(bench.record_beat(b, call, sess, missing_slots=mc.get(b, ())))
        vlog = log.verify(sess, reg, bench.sign_key())
        r = reconstruct(vlog.beats, PIDS, SLOTS, vlog.authenticated)
        for s in r.fixed:
            assert r.fixed[s] == perm[s], (pm, s)


# --------------------------------------------------------------------------
# adaptive rollcall
# --------------------------------------------------------------------------
def test_rollcall_no_missing():
    res, run = run_rollcall(INSERTION_B, Session.new(), "R", budget=5)
    assert run.reconstruction.complete
    assert run.reconstruction.fixed == INSERTION_B
    assert len(res.turns) <= 5


def test_rollcall_survives_full_dark_beat():
    res, run = run_rollcall(
        INSERTION_B, Session.new(), "R", budget=5, missing_by_beat={0: SLOTS}
    )
    assert run.reconstruction.complete
    assert run.reconstruction.fixed == INSERTION_B


def test_rollcall_all_insertions_within_budget():
    """The adaptive roll-caller recovers every physical insertion in 5 beats."""
    sess = Session.new()
    for pm in itertools.permutations(PIDS):
        perm = dict(zip(SLOTS, pm))
        res, run = run_rollcall(perm, sess, "REX", budget=5)
        assert run.reconstruction.complete and run.reconstruction.fixed == perm, pm
        assert len(res.turns) <= 5


def test_rollcall_only_asks_still_ambiguous():
    res, run = run_rollcall(INSERTION_B, Session.new(), "R", budget=5)
    # Each later call is drawn only from probes that were still candidates for
    # some ambiguous slot at the previous turn -- never from already-pinned
    # probes (wasteful re-asking).
    for prev, cur in zip(res.turns, res.turns[1:]):
        candidate_pool = {p for s in prev.recon.ambiguous for p in prev.recon.ambiguous[s]}
        assert set(cur.call) <= candidate_pool, (cur.call, candidate_pool)
        assert prev.recon.ambiguous  # it only asked because something remained


# --------------------------------------------------------------------------
# budget / impossibility
# --------------------------------------------------------------------------
def test_budget_smaller_than_probes():
    beats = preset_beats()
    assert len(beats) == 5 and len(PIDS) == 6
    # A naive one-probe-per-beat schedule would need 6 beats.
    assert len(beats) < len(PIDS)


def test_preplan_schedule_is_fixed():
    """The preplanned strategy exposes its schedule and never adapts."""
    s = PreplanStrategy()
    bs1 = s.schedule()
    bs2 = s.schedule()
    assert bs1 == bs2 == preset_beats()


# ------------------------------------------------------------------------------------------
# wiring sheet over-budget: recovery sheet plus documented ambiguity
# ------------------------------------------------------------------------------------------
def test_recovery_sheet_then_overrun():
    """Guarantee holds in-contract; beyond it we emit ambiguity, not a guess."""
    # in contract: one cell per slot
    run_in = _run({"甲": "P-A1", "乙": "P-B2", "丙": "P-C3", "丁": "P-D4", "戊": "P-E5", "己": "P-F6"})
    assert run_in.reconstruction.complete
    # beyond: documented
    run_out, wit = sheet_with_checkable_ambiguity(INSERTION_B, {0: ("甲", "丁"), 2: ("甲", "丁")})
    assert not run_out.reconstruction.complete
    assert wit["ambiguous_slots"]
