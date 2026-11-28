"""Independent room simulator.

Renders the four designated seats' waveforms for a simultaneous two-course
lesson using RESPONSE SET B, distinct from the auditor's SET_A measurement set,
so delivered audio is checked against a response the bound-building step never
saw.  All synthetic.

Per beat a course plays exactly one role:
    mask : public masking chord (every band at mask_level_db)
    priv : private tone (one band at L_PRIV)
    rest : silence
Each emission is convolved with the board echo tail (tap list).  A course's own
audio is heard at full level; its neighbour-room copy is attenuated per band by
true_iso[f, seat] (SET B).

Stems are BAND-SPLIT so each stem is a single sinusoid whose time-domain mean
square over a beat window is exactly its band power (no coherence ambiguity):

    own_priv                     (lives only in the observer's private band)
    own_mask_0 ... own_mask_5
    nb_priv                      (lives only in the neighbour's private band)
    nb_mask_0 ... nb_mask_5
    noise_0 ... noise_5
    mix  =  sample-exact sum of all of the above

Independent sources use small, distinct in-band frequency offsets so the
delivered mixture is a genuine mix and the stems are power-additive.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

from . import estimate
from .model import (
    BAND_CENTERS, BEAT_S, FS, N_BEATS, NOISE_DB, TONE_FRAC, Excitement,
)
from .signals import amp_duty, amp_full_window, beat_place, source_freq, tone

BEATLEN = int(round(BEAT_S * FS))
NB = len(BAND_CENTERS)
ON = int(round(TONE_FRAC * BEATLEN))


@dataclass
class CoursePlan:
    course: str
    seat: str
    priv_band: int
    roles: Dict[int, str]
    tail: Tuple[Tuple[int, float], ...]
    mask_level_db: float
    tag_priv: int
    tag_mask: int


def make_plan(course: str, seat: str, exc: Excitement) -> CoursePlan:
    return CoursePlan(course, seat, exc.priv_band[course], exc.roles(course),
                      tuple(exc.tail), exc.mask_level_db,
                      tag_priv=1 if course == "A" else 2,
                      tag_mask=3 if course == "A" else 4)


# --------------------------------------------------------------------------- #
# Emission buffers (single band each)
# --------------------------------------------------------------------------- #
def _add_tail(buf: List[float], src: Sequence[float],
              tail: Sequence[Tuple[int, float]]) -> List[float]:
    out = list(buf)
    for delay, gain_db in tail:
        d = delay * BEATLEN
        g = 10.0 ** (gain_db / 20.0)
        for i in range(len(out) - d):
            out[i + d] += g * src[i]
    return out


def _priv_buf(plan: CoursePlan, n: int) -> List[float]:
    f = source_freq(BAND_CENTERS[plan.priv_band], plan.tag_priv)
    seg = tone(f, ON / FS, amp_duty(0.0))
    buf = [0.0] * n
    for k, role in plan.roles.items():
        if role == "priv":
            beat_place(buf, seg, k * BEATLEN)
    return _add_tail(buf, buf, plan.tail)


def _mask_band_buf(plan: CoursePlan, f_idx: int, n: int) -> List[float]:
    f = source_freq(BAND_CENTERS[f_idx], plan.tag_mask + f_idx)
    seg = tone(f, ON / FS, amp_duty(plan.mask_level_db))
    buf = [0.0] * n
    for k, role in plan.roles.items():
        if role == "mask":
            beat_place(buf, seg, k * BEATLEN)
    return _add_tail(buf, buf, plan.tail)


def _noise_band(n: int, f_idx: int, seed: int) -> List[float]:
    import random
    rng = random.Random((seed * 131 + f_idx) & 0xFFFFFFFF)
    ndb = NOISE_DB[f_idx]
    amp = amp_full_window(ndb)
    ph = rng.uniform(0, 2 * math.pi)
    w = 2 * math.pi * source_freq(BAND_CENTERS[f_idx], 10 + f_idx) / FS
    return [amp * math.sin(w * k + ph) for k in range(n)]


def _add(dst: List[float], src: Sequence[float], g: float = 1.0) -> None:
    for i, x in enumerate(src):
        if i < len(dst):
            dst[i] += g * x


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def render(config: Tuple[str, str],
           plan_a: CoursePlan, plan_b: CoursePlan,
           response_set: str = "B",
           seed: int = 7) -> Dict[str, Dict[str, List[float]]]:
    n = N_BEATS * BEATLEN
    plans = {"A": plan_a, "B": plan_b}
    seat_rooms = {"A": ["A1", "A2"], "B": ["B1", "B2"]}

    priv_src = {c: _priv_buf(p, n) for c, p in plans.items()}
    mask_bands = {c: [_mask_band_buf(p, f, n) for f in range(NB)]
                  for c, p in plans.items()}

    out: Dict[str, Dict[str, List[float]]] = {}
    for room in ("A", "B"):
        other = "B" if room == "A" else "A"
        for seat in seat_rooms[room]:
            stems: Dict[str, List[float]] = {}
            stems["own_priv"] = list(priv_src[room])
            iso_p = estimate.true_iso(plans[other].priv_band, seat, config,
                                      response_set)
            nb_priv = [0.0] * n
            _add(nb_priv, priv_src[other], 10.0 ** (-iso_p / 20.0))
            stems["nb_priv"] = nb_priv

            seat_seed = seed + sum((i + 1) * ord(ch) for i, ch in enumerate(seat))
            for f in range(NB):
                stems["own_mask_%d" % f] = list(mask_bands[room][f])
                iso = estimate.true_iso(f, seat, config, response_set)
                nbm = [0.0] * n
                _add(nbm, mask_bands[other][f], 10.0 ** (-iso / 20.0))
                stems["nb_mask_%d" % f] = nbm
                stems["noise_%d" % f] = _noise_band(n, f, seat_seed)

            mix = [0.0] * n
            for nm, buf in stems.items():
                _add(mix, buf)
            stems["mix"] = mix
            out[seat] = stems
    return out
