"""Calibration room: per-probe piecewise calibration curves.

Each probe has its own piecewise-linear calibration curve.  The room applies
the curve *belonging to the recognised probe identity* to that slot's integer
read for the day.  So:

* Changing the calibration curves alone changes the emitted values but never
  the identity->slot attribution (attribution came from contact evidence).
* Changing probe attestation material would change which identity is
  recognised -- but with the *same* roster and receipts the attribution is
  unchanged; only the conversion values differ.

A slot that could not be named keeps its raw reading and gets **no**
conversion -- no guessed name.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple


Curve = Sequence[Tuple[int, float]]  # [(raw_value, engineering_value), ...]


def piecewise_linear(curve: Curve, x: float) -> float:
    """Evaluate a piecewise-linear curve at ``x`` (clamped at the ends)."""
    pts = sorted(curve)
    if not pts:
        raise ValueError("empty curve")
    if x <= pts[0][0]:
        return pts[0][1]
    if x >= pts[-1][0]:
        return pts[-1][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= x <= x1:
            if x1 == x0:
                return y1
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return pts[-1][1]  # pragma: no cover


@dataclass
class CalibrationCurve:
    """A named probe's own calibration curve for the day's small integers."""

    probe_id: str
    points: Curve
    revision: str = "cal-2026-11-28"

    def convert(self, raw: int) -> float:
        return piecewise_linear(self.points, raw)

    def as_dict(self) -> dict:
        return {
            "probe_id": self.probe_id,
            "revision": self.revision,
            "points": [list(p) for p in self.points],
        }


DEFAULT_CURVES: Dict[str, Curve] = {
    "P-A1": [(128, 1.0), (180, 2.0), (250, 4.0)],
    "P-B2": [(128, 1.5), (180, 3.0), (250, 5.0)],
    "P-C3": [(128, 0.8), (180, 1.6), (250, 3.2)],
    "P-D4": [(128, 2.0), (180, 4.0), (250, 8.0)],
    "P-E5": [(128, 2.5), (180, 5.0), (250, 10.0)],
    "P-F6": [(128, 0.5), (180, 1.0), (250, 2.0)],
}

# A deliberately different set of segmented curves for the "switch the room's
# own curves only" check.
ALT_CURVES: Dict[str, Curve] = {
    "P-A1": [(128, 10.0), (180, 20.0), (250, 40.0)],
    "P-B2": [(128, 15.0), (180, 30.0), (250, 50.0)],
    "P-C3": [(128, 8.0), (180, 16.0), (250, 32.0)],
    "P-D4": [(128, 20.0), (180, 40.0), (250, 80.0)],
    "P-E5": [(128, 25.0), (180, 50.0), (250, 100.0)],
    "P-F6": [(128, 5.0), (180, 10.0), (250, 20.0)],
}


def default_curves() -> Dict[str, CalibrationCurve]:
    return {pid: CalibrationCurve(pid, pts) for pid, pts in DEFAULT_CURVES.items()}


def alt_curves() -> Dict[str, CalibrationCurve]:
    return {pid: CalibrationCurve(pid, pts, revision="cal-alt") for pid, pts in ALT_CURVES.items()}


@dataclass
class SlotReading:
    slot: str
    raw: int


@dataclass
class ReportLine:
    slot: str
    identity: Optional[str]
    raw: int
    converted: Optional[float]
    basis: str  # "read+certified" or "unresolved-raw-only"
    evidence: Optional[dict] = None

    def as_dict(self) -> dict:
        return {
            "slot": self.slot,
            "identity": self.identity,
            "raw": self.raw,
            "converted": self.converted,
            "basis": self.basis,
            "evidence": self.evidence,
        }


@dataclass
class CalibrationReport:
    lines: List[ReportLine]
    curve_revision: str
    unresolved_slots: List[str]

    def as_dict(self) -> dict:
        return {
            "lead": "探头认证站 · 校准读数单",
            "curve_revision": self.curve_revision,
            "lines": [ln.as_dict() for ln in self.lines],
            "unresolved_slots": list(self.unresolved_slots),
        }


def issue_report(
    readings: Sequence[SlotReading],
    reconstruction,
    curves: Dict[str, CalibrationCurve],
) -> CalibrationReport:
    """Apply each recognised probe's own curve to that slot's raw integer.

    Only slots named by the reconstruction get a conversion.  Ambiguous slots
    keep their raw integer and *no* identity -- never a guessed one.
    """
    lines: List[ReportLine] = []
    unresolved: List[str] = []
    revisions = {c.revision for c in curves.values()}
    rev = sorted(revisions)[0] if len(revisions) == 1 else "mixed"
    ev = reconstruction.evidence_dicts()
    for r in readings:
        if r.slot in reconstruction.fixed:
            pid = reconstruction.fixed[r.slot]
            conv = curves[pid].convert(r.raw)
            lines.append(
                ReportLine(r.slot, pid, r.raw, conv, "read+certified", ev.get(r.slot))
            )
        else:
            unresolved.append(r.slot)
            lines.append(
                ReportLine(
                    r.slot,
                    None,
                    r.raw,
                    None,
                    "unresolved-raw-only",
                    {
                        "ambiguous_candidates": list(
                            reconstruction.ambiguous.get(r.slot, ())
                        )
                    },
                )
            )
    return CalibrationReport(lines, rev, unresolved)
