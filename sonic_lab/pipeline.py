"""Orchestration: acceptance, two delivery modes, perturbations, audio, report.

Two delivery modes (choose one; both serve the two classes on one ordinary
timetable that has feasible slots):

  FIXED      Offline-fixed boards + band allocation.  The rotating teacher can
             run the two classes together from the issued slots without network
             access.  Keeps an explicit uncertainty buffer
             (FIXED_UNCERTAINTY_DB): wider margins only.

  ONSITE     Short per-class test signals are accepted on site: seats, time
             slots and the shared mask are checked then answered.  Uses the
             tighter on-site bias (BIAS_ADAPTIVE_DB) so it can take
             narrow-margin classes, but never hands the new lesson recording to
             the neighbouring room (only test signals are played).

Both produce a playable two-track lesson per slot (track_A.wav, track_B.wav) and
a verification on the independent response set (SET B).  Determinism is enforced
by seeding.

Perturbations (each verified separately)
  * replace ONE board's measured tail with a longer one -> the early class may
    need one extra rest beat between pulses ("多隔一拍").
  * restore the boards, change only the SECOND class's usable seat -> the
    original slot and the deliverable audio are each re-checked.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from . import estimate, schedule
from .model import (
    BEAT_S, BIAS_ADAPTIVE_DB, FIXED_UNCERTAINTY_DB, L_MASK_DB, LEGAL_CONFIGS,
    N_BEATS, TAIL_LONG, TAIL_SHORT, TAU_LEAK_DB, TAU_OWN_DB, Excitement,
    interlock_ok, make_layout, tail_delays,
)
from .schedule import Courseware, Slot, design_courseware, predict, verify_slot
from .signals import normalize, write_wav


# --------------------------------------------------------------------------- #
# Audio delivery
# --------------------------------------------------------------------------- #
def render_tracks(slot: Slot, out_dir: str, response_set: str = "B"
                  ) -> Dict[str, str]:
    """Write a playable two-track lesson for one slot; return paths."""
    v = slot.verified or verify_slot(slot.exc, slot.config, slot.seat_a,
                                     slot.seat_b, response_set)
    seats = v["seats"]
    os.makedirs(out_dir, exist_ok=True)
    paths = {}
    for course, seat in (("A", slot.seat_a), ("B", slot.seat_b)):
        mix = normalize(seats[seat]["mix"], 0.98)
        p = os.path.join(out_dir, "track_%s.wav" % course)
        write_wav(p, mix)
        paths[course] = p
    return paths


# --------------------------------------------------------------------------- #
# Slot selection for a mode
# --------------------------------------------------------------------------- #
@dataclass
class Plan:
    mode: str
    slot: Optional[Slot]
    band_alloc: Optional[Dict[str, int]]
    reason: str = ""
    extra: Dict[str, object] = field(default_factory=dict)


def _candidate_coursewares(band_pairs: Sequence[Tuple[int, int]],
                           tail: Sequence[Tuple[int, float]]
                           ) -> List[Courseware]:
    out = []
    for fa, fb in band_pairs:
        out.append(design_courseware("bands_%d_%d" % (fa, fb),
                                     {"A": fa, "B": fb}, tail=tail))
    return out


def plan_lesson(mode: str,
                configs: Optional[Sequence[Tuple[str, str]]] = None,
                seat_pairs: Sequence[Tuple[str, str]] = (("A1", "B1"),
                                                         ("A1", "B2"),
                                                         ("A2", "B1"),
                                                         ("A2", "B2")),
                tail: Sequence[Tuple[int, float]] = TAIL_SHORT,
                band_pairs: Optional[Sequence[Tuple[int, int]]] = None,
                response_set: str = "B",
                require_verify: bool = True,
                ) -> Plan:
    """Pick the best feasible slot for the requested delivery mode.

    FIXED  : offline-fixed delivery keeps uncertainty headroom -- boards must be
             SEALED (both), and the predicted margin must clear the fixed
             buffer (FIXED_UNCERTAINTY_DB) on top of the wide bias.  This takes
             only wide-margin courses.
    ONSITE : on-site acceptance may open ONE board (legal positions) and uses
             the tighter adaptive bias, so it can take narrow-margin courses.
    """
    if mode not in ("fixed", "onsite"):
        raise ValueError("mode must be 'fixed' or 'onsite'")
    if band_pairs is None:
        band_pairs = [(fa, fb) for fa in range(6) for fb in range(6) if fa != fb]

    bias = estimate.BIAS_FIXED_DB if mode == "fixed" else BIAS_ADAPTIVE_DB
    buffer = FIXED_UNCERTAINTY_DB if mode == "fixed" else 0.0
    if configs is None:
        configs = [("sealed", "sealed")] if mode == "fixed" else LEGAL_CONFIGS

    best: Optional[Slot] = None
    best_bands = None
    for cw in _candidate_coursewares(band_pairs, tail):
        for config in configs:
            iso_lower = estimate.iso_lower_bounds(config, bias_db=bias)
            for seat_a, seat_b in seat_pairs:
                for offset in range(N_BEATS):
                    exc = make_layout(cw.mask_template, offset, cw.priv_a_rel,
                                      cw.priv_b_rel, cw.priv_band, tail=tail)
                    ok, _ = interlock_ok(exc)
                    if not ok:
                        continue
                    p = predict(exc, config, iso_lower, seat_a, seat_b)
                    if p["margin"] < buffer:
                        continue
                    if best is not None and p["margin"] <= best.predicted_margin:
                        continue
                    slot = Slot(offset, config, seat_a, seat_b, exc, p["margin"])
                    if require_verify:
                        slot.verified = verify_slot(exc, config, seat_a, seat_b,
                                                    response_set)
                        if not slot.verified["ok"]:
                            continue
                    best, best_bands = slot, dict(cw.priv_band)
    if best is None:
        return Plan(mode, None, None, reason="no feasible slot for this mode")
    return Plan(mode, best, best_bands,
                reason="ok", extra={"bias_db": bias, "buffer_db": buffer})


# --------------------------------------------------------------------------- #
# Perturbation A: lengthen ONE board's tail
# --------------------------------------------------------------------------- #
def perturb_tail(plan: Plan, band_pairs=None, response_set="B") -> Dict[str, object]:
    """Replace the measured tail of one board with a longer one.

    Re-solves the early class's rhythm under TAIL_LONG.  Reports whether the
    old spacing is still legal and whether one extra rest beat is needed."""
    old_gap = max(tail_delays(TAIL_SHORT)) + 1
    new_gap = max(tail_delays(TAIL_LONG)) + 1
    new_plan = plan_lesson(plan.mode, tail=TAIL_LONG, band_pairs=band_pairs,
                           response_set=response_set)
    extra_beat = new_gap > old_gap
    return {
        "perturbation": "board_tail_lengthened",
        "old_required_gap_beats": old_gap,
        "new_required_gap_beats": new_gap,
        "early_class_needs_extra_beat": extra_beat,
        "still_feasible": new_plan.slot is not None,
        "new_plan": _summarize(new_plan),
    }


# --------------------------------------------------------------------------- #
# Perturbation B: restore boards, change only class B's seat
# --------------------------------------------------------------------------- #
def perturb_seat(plan: Plan, new_seat_b: str, response_set="B"
                 ) -> Dict[str, object]:
    """Restore the boards, change only the SECOND class's usable seat.

    Re-checks the original slot (same config, bands, beat layout) for the new
    seat, and re-renders the deliverable audio for it."""
    if plan.slot is None:
        return {"perturbation": "seat_change", "error": "no base plan"}
    exc = plan.slot.exc
    cfg = plan.slot.config
    seat_a = plan.slot.seat_a
    old = plan.slot.verified or verify_slot(exc, cfg, seat_a, plan.slot.seat_b,
                                            response_set)
    new = verify_slot(exc, cfg, seat_a, new_seat_b, response_set)
    return {
        "perturbation": "seat_change",
        "old_seat_b": plan.slot.seat_b, "new_seat_b": new_seat_b,
        "original_slot_still_ok": bool(new["ok"]),
        "measured": new["measured"],
        "ok_own": new["ok_own"], "ok_leak": new["ok_leak"],
        "mix_consistent": new["mix_consistent"],
        "same_beat_layout": True,
        "note": ("audio must be re-certified for the new seat"
                 if not new["ok"] else "audio certified for the new seat"),
    }


def _summarize(plan: Plan) -> Dict[str, object]:
    if plan.slot is None:
        return {"slot": None, "reason": plan.reason}
    s = plan.slot
    v = s.verified or {}
    return {
        "config": list(s.config),
        "seats": [s.seat_a, s.seat_b],
        "offset_beats": s.offset,
        "band_alloc": plan.band_alloc,
        "predicted_margin_db": round(s.predicted_margin, 3),
        "measured": {k: {kk: round(vv, 2) for kk, vv in m.items()}
                     for k, m in v.get("measured", {}).items()},
        "contract_ok": bool(v.get("ok", False)),
    }


def summarize(plan: Plan) -> Dict[str, object]:
    d = {"mode": plan.mode, "reason": plan.reason}
    d.update(_summarize(plan))
    if plan.extra:
        d["extra"] = plan.extra
    return d
