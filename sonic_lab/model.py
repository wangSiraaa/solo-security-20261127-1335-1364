"""Fixed configuration: bands, rooms, seats, partitions, levels, contract.

All quantities are synthetic and offline.  Levels are *measured mean-square
power over a beat window*, in dB.  "Isolation" is the per-band, per-seat
cross-room attenuation (dB) from a source in the neighbouring room to a receiver
seat.

Physical picture
----------------
Two teaching audio labs, ROOM_A (early class) and ROOM_B (second class), share a
movable partition built of two independently positionable boards ("两扇活动隔
板").  Each lab has two designated listening seats; one seat per room sits close
to the partition ("near" -> worse isolation), the other is well placed ("good").
Four designated seats: A1, A2, B1, B2.

Course audio (discrete beat model)
----------------------------------
The lesson is a grid of N_BEATS beats.  Each beat of a course is exactly one of

    mask  : the PUBLIC masking chord -- every band at mask_level_db
    priv  : this course's PRIVATE tone -- one band at L_PRIV
    rest  : silence

The two coursewares share the IDENTICAL public mask template (same melody),
phase-staggered by an offset; their private patterns differ (band + beats).

Contract
--------
At a seat of course X, on X's private beats, X's own private tone must be
audible.  Wherever the OTHER course Y emits its private tone (direct or echo
tail), X must be playing its public mask on that same beat so the leak is
masked; then Y's private pattern is not distinguishable to X.  Quantitatively:

    OWN   : own private power >= (interference at own private band) + TAU_OWN
    LEAK  : leaked Y-private power <= (own mask + noise at Y's band) - TAU_LEAK

Echo tail
---------
Every emission deposits energy into later beats: a list of (delay_beats,
gain_db) taps (delay >= 1).  Longest tap delay = D.  Consequences:
    * B.priv direct+tail beats must all fall on A's mask beats (else unmasked);
      likewise A.priv onto B.mask.
    * a course's own private beats must be spaced > D, so a pulse's tail has
      decayed before the next pulse (self-interference / "多隔一拍").
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

# --------------------------------------------------------------------------- #
# Bands / noise
# --------------------------------------------------------------------------- #
FS = 16000
BAND_CENTERS = [125, 250, 500, 1000, 2000, 4000]
BAND_NAMES = ["125", "250", "500", "1k", "2k", "4k"]
NB = len(BAND_CENTERS)
NOISE_DB = [-78.0, -77.0, -76.0, -75.0, -76.0, -78.0]

# --------------------------------------------------------------------------- #
# Isolation model
# --------------------------------------------------------------------------- #
ISO_SEALED = [15.5, 18.0, 22.0, 25.0, 28.0, 30.0]
PART_LOSS = [9.0, 7.0, 4.0, 3.0, 2.0, 1.0]
SEAT_PENALTY = {
    "good": [1.0, 1.0, 0.0, 0.0, 0.0, 0.0],
    "near": [6.0, 5.0, 4.0, 3.0, 2.0, 1.0],
}
LEGAL_CONFIGS = [("sealed", "sealed"), ("open", "sealed"), ("sealed", "open")]


def partition_loss(f: int, config: Tuple[str, str]) -> float:
    if config not in LEGAL_CONFIGS:
        raise ValueError(f"illegal partition configuration: {config!r}")
    return sum(PART_LOSS[f] for p in config if p == "open")


# --------------------------------------------------------------------------- #
# Seats
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Seat:
    id: str
    room: str        # "A" / "B"
    seat_class: str  # "good" / "near"


SEATS: Dict[str, Seat] = {
    "A1": Seat("A1", "A", "good"),
    "A2": Seat("A2", "A", "near"),
    "B1": Seat("B1", "B", "good"),
    "B2": Seat("B2", "B", "near"),
}
ALL_SEATS = ["A1", "A2", "B1", "B2"]
ROOM_SEATS = {"A": ["A1", "A2"], "B": ["B1", "B2"]}

# --------------------------------------------------------------------------- #
# Contract + levels
# --------------------------------------------------------------------------- #
BEAT_S = 0.25
TONE_FRAC = 0.80          # pulse/chord fills 80% of its beat (20% release)
N_BEATS = 16              # beats per course loop

L_PRIV_DB = 0.0           # private tone level (per band, mean-square over beat)
L_MASK_DB = -10.0         # public mask chord level (per band)
TAU_OWN_DB = 10.0         # own private must beat own interference by this much
TAU_LEAK_DB = 3.0         # neighbour private must stay this far under masking

# Echo tail tap lists: (delay_beats, gain_db) with delay >= 1.
TAIL_SHORT = ((1, -12.0),)
TAIL_LONG = ((1, -6.0), (2, -14.0))   # one board's measured tail lengthened

# Error model (two response sets, both bounded)
RESP_ERROR_DB = 1.0
BIAS_FIXED_DB = 2.0        # >= 2*RESP_ERROR_DB: covers both sets (offline)
BIAS_ADAPTIVE_DB = 1.0     # on-site re-measured, smaller buffer
TAIL_MARGIN_DB = 2.0       # extra tail uncertainty in the bound
FIXED_UNCERTAINTY_DB = 1.0 # extra buffer a FIXED timetable must keep


# --------------------------------------------------------------------------- #
# Excitement: a realised simultaneous lesson on the shared beat grid
# --------------------------------------------------------------------------- #
@dataclass
class Excitement:
    mask: Dict[str, Tuple[int, ...]]
    priv: Dict[str, Tuple[int, ...]]
    priv_band: Dict[str, int]
    tail: Tuple[Tuple[int, float], ...] = tuple(TAIL_SHORT)
    mask_level_db: float = L_MASK_DB

    def roles(self, course: str) -> Dict[int, str]:
        r = {k: "rest" for k in range(N_BEATS)}
        for b in self.mask[course]:
            r[b % N_BEATS] = "mask"
        for b in self.priv[course]:
            r[b % N_BEATS] = "priv"
        return r


def tail_delays(tail: Sequence[Tuple[int, float]]) -> List[int]:
    """All beat delays carrying private/mask energy (0 = direct)."""
    return [0] + sorted({d for d, _ in tail if d >= 1})


def expand(beats: Sequence[int], tail: Sequence[Tuple[int, float]]
           ) -> set:
    """All beats hit by emissions on `beats`, direct and tails (mod N_BEATS)."""
    return {(b + d) % N_BEATS for b in beats for d in tail_delays(tail)}


def min_gap(beats: Sequence[int]) -> int:
    bs = sorted({b % N_BEATS for b in beats})
    if len(bs) <= 1:
        return N_BEATS
    gaps = [(bs[(i + 1) % len(bs)] - bs[i]) % N_BEATS for i in range(len(bs))]
    return min(gaps)


def make_layout(mask_template: Sequence[int], offset: int,
                priv_a_rel: Sequence[int], priv_b_rel: Sequence[int],
                priv_band: Dict[str, int],
                tail: Sequence[Tuple[int, float]] = TAIL_SHORT,
                mask_level_db: float = L_MASK_DB) -> Excitement:
    """Assemble an Excitement from a shared public mask template.

    The two coursewares share the SAME public mask template (identical public
    material).  A course's realised public mask is the template minus its own
    private beats; on its private beats it plays the private tone instead (so
    its own private tone is not buried under its own mask chord).  Course B's
    private pattern is shifted by `offset` so the two private sets interleave.
    Beats outside the template are shared rests.
    """
    template = {b % N_BEATS for b in mask_template}
    priv_a = tuple(sorted({b % N_BEATS for b in priv_a_rel}))
    priv_b = tuple(sorted({(b + offset) % N_BEATS for b in priv_b_rel}))
    mask_a = tuple(sorted(template - set(priv_a)))
    mask_b = tuple(sorted(template - set(priv_b)))
    return Excitement(mask={"A": mask_a, "B": mask_b},
                      priv={"A": priv_a, "B": priv_b},
                      priv_band=dict(priv_band), tail=tuple(tail),
                      mask_level_db=mask_level_db)


def interlock_ok(exc: Excitement) -> Tuple[bool, str]:
    """Structural feasibility of a layout (independent of levels/isolation).

    Each course's private beats must be hand-masked by the OTHER course's public
    mask (direct + echo-tail arrival), and vice versa; the private beats of the
    two courses must not coincide; a course's own private beats must be spaced
    beyond its longest tail so one pulse's tail decays before the next.
    """
    A, B = set(exc.mask["A"]), set(exc.mask["B"])
    pa, pb = set(exc.priv["A"]), set(exc.priv["B"])
    if not pa or not pb:
        return False, "each course needs >=1 private beat"
    if A & pa:
        return False, "A mask/priv overlap"
    if B & pb:
        return False, "B mask/priv overlap"
    if not expand(exc.priv["B"], exc.tail) <= A:
        return False, "B private (with tail) not masked by A"
    if not expand(exc.priv["A"], exc.tail) <= B:
        return False, "A private (with tail) not masked by B"
    if pa & pb:
        return False, "cross-class private pulses coincide"
    dmax = max(tail_delays(exc.tail))
    if min_gap(exc.priv["A"]) <= dmax:
        return False, "A private spacing <= tail length"
    if min_gap(exc.priv["B"]) <= dmax:
        return False, "B private spacing <= tail length"
    return True, "ok"
