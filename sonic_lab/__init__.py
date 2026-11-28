"""Synthetic classroom-isolation acceptance + two-course scheduling toolkit.

Everything here is offline and synthetic: no real voices or real rooms are
recorded.  Rooms, seats, partitions and the "auditor's test tape" are modelled,
and the contract (own pattern audible / neighbour's private pattern not
distinguishable) is *verified on rendered waveforms*, not merely asserted.

Sub-modules
-----------
model     : fixed configuration (bands, seats, partitions, contract, error model)
signals   : tone/noise synthesis, WAV I/O, Goertzel power measurement
estimate  : auditor's test-tape -> per-band crosstalk UPPER bounds
simulator : independent room simulator (2nd response set) -> 4-seat waveforms
schedule  : beat grid, masking curve, contract evaluation, timetable solver
pipeline  : orchestrates acceptance, both delivery modes, perturbations
"""

__all__ = [
    "model", "signals", "estimate", "simulator", "schedule", "pipeline",
]
