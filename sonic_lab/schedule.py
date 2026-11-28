"""Contract evaluation + timetable solver.

Works from the auditor's UPPER leak bounds (estimate.py), never from the true
room responses.  For a partition configuration, private-band assignment and a
beat layout it:

  1. assembles a realisable Excitement on the shared beat grid,
  2. renders the four seats with the *independent* simulator (SET B),
  3. measures the contract on the rendered stems:

       OWN   at observer's own private band, on its private beats:
             own private power >= (own mask + neighbour-mask leak + noise) + TAU_OWN
       LEAK  at the source's private band, on the source's private beats:
             leaked neighbour-private power <= (own mask + noise) - TAU_LEAK
       MIX   the delivered mix equals the sum of the stems (sample-exact).

     plus a bound-based prediction and the structural interlock/tail rules.

Tail clearance ("多隔一拍")
---------------------------
A course's own private beats must be spaced more than the longest tail delay so
one pulse's tail has decayed before the next.  Lengthening a board's tail
(e.g. replacing one panel) raises that minimum spacing by a beat.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from . import estimate
from .model import (
    BAND_CENTERS, L_MASK_DB, L_PRIV_DB, N_BEATS, NB, NOISE_DB, TAIL_MARGIN_DB,
    TAIL_SHORT, TAU_LEAK_DB, TAU_OWN_DB, Excitement, expand, interlock_ok,
    make_layout, min_gap, tail_delays,
)
from .signals import db_from_pow, mean_square_beats, pow_from_db
from .simulator import BEATLEN, CoursePlan, make_plan, render


def required_min_gap(tail: Sequence[Tuple[int, float]]) -> int:
    return max(tail_delays(tail)) + 1


# --------------------------------------------------------------------------- #
# Prediction from bounds (never touches true responses)
# --------------------------------------------------------------------------- #
def predict(exc: Excitement, config: Tuple[str, str],
            iso_lower: Dict[Tuple[int, str], float],
            seat_a: str, seat_b: str) -> Dict[str, float]:
    """Bound-based own-SNR and leak-detect margins (no rendering).

    Different bands do not mask each other.  At an observer:
      OWN   at its private band f_own: private (0 dB) vs own mask + neighbour
            MASK leak at f_own + noise.
      DETECT at the source's private band f_src: neighbour PRIVATE leak vs
            own mask at f_src + noise.
    """
    fa, fb = exc.priv_band["A"], exc.priv_band["B"]
    Lm = exc.mask_level_db

    def own_snr(seat: str, f_own: int) -> float:
        # On its own private beats a course plays the private tone, not its own
        # mask; interference is the neighbour's MASK chord leaking into f_own
        # (different source band) plus noise.
        nb_mask = pow_from_db(estimate.leak_upper_db(
            f_own, seat, Lm, iso_lower, exc.tail))
        noise = pow_from_db(NOISE_DB[f_own])
        return db_from_pow(pow_from_db(L_PRIV_DB)) - db_from_pow(nb_mask + noise)

    def detect(seat: str, f_src: int) -> float:
        # On the source's private beats the observer is playing its mask chord
        # at f_src; the leaked private must sit under mask+noise there.
        leak = pow_from_db(estimate.leak_upper_db(
            f_src, seat, L_PRIV_DB, iso_lower, exc.tail))
        masker = pow_from_db(Lm) + pow_from_db(NOISE_DB[f_src])
        return db_from_pow(leak) - db_from_pow(masker)

    snr_a, snr_b = own_snr(seat_a, fa), own_snr(seat_b, fb)
    det_a, det_b = detect(seat_a, fb), detect(seat_b, fa)
    return {
        "own_snr_A": snr_a, "own_snr_B": snr_b,
        "detect_A": det_a, "detect_B": det_b,
        "own_slack": min(snr_a - TAU_OWN_DB, snr_b - TAU_OWN_DB),
        "leak_slack": min(-TAU_LEAK_DB - det_a, -TAU_LEAK_DB - det_b),
        "margin": min(snr_a - TAU_OWN_DB, snr_b - TAU_OWN_DB,
                      -TAU_LEAK_DB - det_a, -TAU_LEAK_DB - det_b),
    }


# --------------------------------------------------------------------------- #
# Measured acceptance on rendered stems
# --------------------------------------------------------------------------- #
def _stem_ms(stems: Dict[str, List[float]], stem: str,
             beats: Sequence[int]) -> float:
    return mean_square_beats(stems[stem], BEATLEN, beats)


def _band_ms(stems: Dict[str, List[float]], stem_prefix: str, f: int,
             beats: Sequence[int]) -> float:
    """Mean-square of a single-band stem over the given beat windows."""
    return mean_square_beats(stems["%s_%d" % (stem_prefix, f)], BEATLEN, beats)


def measure_seat(stems: Dict[str, List[float]], plan_obs: CoursePlan,
                 plan_src: CoursePlan) -> Dict[str, float]:
    """Measure the contract on band-split stems.

    OWN    at the observer's private band, on its private beats:
           own private power vs (neighbour MASK leak at that band + noise).
    DETECT at the source's private band, on the source's private beats:
           leaked neighbour private vs (own mask at that band + noise).
    Because each stem is a single sinusoid, time-domain mean square over a beat
    window IS its band power -- no coherence/sidelobe ambiguity.
    """
    f_own = plan_obs.priv_band
    f_src = plan_src.priv_band
    own_beats = [k for k, r in plan_obs.roles.items() if r == "priv"]
    src_beats = [k for k, r in plan_src.roles.items() if r == "priv"]

    own_sig = _stem_ms(stems, "own_priv", own_beats)
    own_int = (_band_ms(stems, "nb_mask", f_own, own_beats)
               + _band_ms(stems, "noise", f_own, own_beats))
    own_snr = db_from_pow(own_sig) - db_from_pow(own_int)

    nb_leak = _stem_ms(stems, "nb_priv", src_beats)
    mask_noise = (_band_ms(stems, "own_mask", f_src, src_beats)
                  + _band_ms(stems, "noise", f_src, src_beats))
    detect = db_from_pow(nb_leak) - db_from_pow(mask_noise)
    return {"own_snr": own_snr, "detect": detect,
            "n_own": len(own_beats), "n_src": len(src_beats)}


def _mix_err(seats: Dict[str, Dict[str, List[float]]], seat: str) -> float:
    s = seats[seat]
    stems = [nm for nm in s if nm != "mix"]
    err = 0.0
    for i in range(len(s["mix"])):
        tot = sum(s[nm][i] for nm in stems)
        err = max(err, abs(s["mix"][i] - tot))
    return err


def verify_slot(exc: Excitement, config: Tuple[str, str],
                seat_a: str, seat_b: str,
                response_set: str = "B") -> Dict[str, object]:
    pa = make_plan("A", seat_a, exc)
    pb = make_plan("B", seat_b, exc)
    seats = render(config, pa, pb, response_set=response_set)
    ma = measure_seat(seats[seat_a], pa, pb)
    mb = measure_seat(seats[seat_b], pb, pa)
    ok_own = (ma["own_snr"] >= TAU_OWN_DB) and (mb["own_snr"] >= TAU_OWN_DB)
    ok_leak = (ma["detect"] <= -TAU_LEAK_DB) and (mb["detect"] <= -TAU_LEAK_DB)
    mix_ok = _mix_err(seats, seat_a) < 1e-9 and _mix_err(seats, seat_b) < 1e-9
    return {
        "config": config, "seat_a": seat_a, "seat_b": seat_b,
        "measured": {"A": ma, "B": mb},
        "ok_own": ok_own, "ok_leak": ok_leak, "mix_consistent": mix_ok,
        "ok": ok_own and ok_leak and mix_ok,
        "seats": seats, "plans": (pa, pb),
    }


# --------------------------------------------------------------------------- #
# Timetable solver
# --------------------------------------------------------------------------- #
@dataclass
class Slot:
    offset: int
    config: Tuple[str, str]
    seat_a: str
    seat_b: str
    exc: Excitement
    predicted_margin: float
    verified: Optional[Dict[str, object]] = None

    @property
    def ok(self) -> bool:
        return self.verified is not None and bool(self.verified["ok"])


@dataclass
class Courseware:
    name: str
    mask_template: Tuple[int, ...]
    priv_a_rel: Tuple[int, ...]
    priv_b_rel: Tuple[int, ...]
    priv_band: Dict[str, int]


def solve_timetable(courseware: Courseware,
                    configs: Sequence[Tuple[str, str]],
                    seat_pairs: Sequence[Tuple[str, str]],
                    require_verify: bool = True,
                    response_set: str = "B",
                    tail: Optional[Sequence[Tuple[int, float]]] = None,
                    ) -> List[Slot]:
    """Enumerate legal boards x seat pairs x beat offsets; keep feasible slots,
    best predicted margin first.  Structural interlock incl. tail rules."""
    tail = tuple(TAIL_SHORT) if tail is None else tuple(tail)
    slots: List[Slot] = []
    for config in configs:
        iso_lower = estimate.iso_lower_bounds(config)
        for seat_a, seat_b in seat_pairs:
            for offset in range(N_BEATS):
                exc = make_layout(courseware.mask_template, offset,
                                  courseware.priv_a_rel, courseware.priv_b_rel,
                                  courseware.priv_band, tail=tail,
                                  mask_level_db=L_MASK_DB)
                ok, _ = interlock_ok(exc)
                if not ok:
                    continue
                pred = predict(exc, config, iso_lower, seat_a, seat_b)
                if pred["margin"] < 0:
                    continue
                slot = Slot(offset, config, seat_a, seat_b, exc, pred["margin"])
                if require_verify:
                    slot.verified = verify_slot(exc, config, seat_a, seat_b,
                                                response_set)
                    if not slot.verified["ok"]:
                        continue
                slots.append(slot)
    slots.sort(key=lambda s: -s.predicted_margin)
    return slots


# --------------------------------------------------------------------------- #
# Courseware construction with interlock
# --------------------------------------------------------------------------- #
def design_courseware(name: str, priv_band: Dict[str, int],
                      n_priv: int = 2,
                      tail: Sequence[Tuple[int, float]] = TAIL_SHORT,
                      ) -> Courseware:
    """Auto-design a shared mask template + two private patterns that satisfy
    interlock for the given tail.

    Private pulses are placed at the tightest legal spacing, gap = dmax + 1, so
    any lengthening of a board's tail (larger dmax) makes the old spacing
    illegal and forces one extra rest beat between pulses -- the "多隔一拍"
    effect.  Course B's pulses sit half a loop away from A's.  The shared public
    mask template is the union of both private sets plus their echo tails; each
    course drops its OWN private beats from that template and plays the private
    tone there.
    """
    dmax = max(tail_delays(tail))
    gap = dmax + 1
    half = N_BEATS // 2
    priv_a = tuple(sorted({(i * gap) % N_BEATS for i in range(n_priv)}))
    priv_b = tuple(sorted({(half + i * gap) % N_BEATS for i in range(n_priv)}))
    template = tuple(sorted(expand(priv_a, tail) | expand(priv_b, tail)))
    return Courseware(name, template, priv_a, priv_b, dict(priv_band))
