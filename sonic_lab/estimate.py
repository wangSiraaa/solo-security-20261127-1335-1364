"""Auditor's test tape -> per-band crosstalk UPPER bounds.

The auditor owns a test tape: a known tone per band, played in the neighbouring
room at a known level, measured at each received seat.  They never see the true
room response; they form a conservative *lower bound* on isolation (equivalently
an upper bound on crosstalk) that must hold for the independent response set
used to render class audio.

Two response sets exist, both within +/- RESP_ERROR_DB of nominal isolation:
    SET_A  auditor's tape response   (used for measurement)
    SET_B  independent simulator     (used to render 4-seat waveforms)

Because |A - B| <= 2*RESP_ERROR_DB, subtracting BIAS_DB = 2*RESP_ERROR_DB from
the measured isolation guarantees the bound covers SET_B:

    iso_lower = measured_iso_A - BIAS_DB  <=  iso_true_B

The echo tail is a list of (delay_beats, gain_db) taps.  It is not part of the
steady test-tape isolation, so leaks add the direct leak plus each tail tap in
power, plus TAIL_MARGIN_DB of uncertainty.  Boards' legal positions are folded
into the nominal isolation via model.partition_loss.
"""

from __future__ import annotations

import random
from typing import Dict, Sequence, Tuple

from .model import (
    ALL_SEATS, BAND_CENTERS, BIAS_FIXED_DB, ISO_SEALED, RESP_ERROR_DB,
    SEAT_PENALTY, SEATS, TAIL_MARGIN_DB, partition_loss,
)
from .signals import db_from_pow, pow_from_db


def nominal_iso(f: int, seat: str, config: Tuple[str, str]) -> float:
    s = SEATS[seat]
    return (ISO_SEALED[f]
            - partition_loss(f, config)
            - SEAT_PENALTY[s.seat_class][f])


def _errors(seed: int) -> Dict[Tuple[int, str], float]:
    rng = random.Random(seed)
    return {(f, seat): rng.uniform(-RESP_ERROR_DB, RESP_ERROR_DB)
            for f in range(len(BAND_CENTERS)) for seat in ALL_SEATS}


SET_A_ERROR = _errors(20241128)
SET_B_ERROR = _errors(1487)


def true_iso(f: int, seat: str, config: Tuple[str, str], response_set: str) -> float:
    err = SET_A_ERROR if response_set == "A" else SET_B_ERROR
    return nominal_iso(f, seat, config) + err[(f, seat)]


def measured_iso(f: int, seat: str, config: Tuple[str, str]) -> float:
    """Isolation the auditor reads off the test tape (SET_A truth)."""
    return true_iso(f, seat, config, "A")


def iso_lower_bounds(config: Tuple[str, str],
                     bias_db: float = BIAS_FIXED_DB
                     ) -> Dict[Tuple[int, str], float]:
    """Conservative per (band, seat) isolation lower bound from the test tape."""
    return {(f, seat): measured_iso(f, seat, config) - bias_db
            for f in range(len(BAND_CENTERS)) for seat in ALL_SEATS}


def leak_upper_db(f: int, seat: str, src_level_db: float,
                  iso_lower: Dict[Tuple[int, str], float],
                  tail: Sequence[Tuple[int, float]],
                  tail_margin_db: float = TAIL_MARGIN_DB) -> float:
    """Upper bound on a neighbour emission's leaked level at a seat (dB).

    Direct leak + every echo-tail tap, summed in power, plus margin.
    """
    direct = src_level_db - iso_lower[(f, seat)]
    total = pow_from_db(direct)
    for _delay, gain_db in tail:
        total += pow_from_db(direct + gain_db + tail_margin_db)
    return db_from_pow(total)
