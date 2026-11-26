"""End-to-end scenarios from the brief, assembled into runnable objects.

The "one free cell, two recipients" scenario starts from:

* three independent cells; 甲's page 1 already sits in 甲's own cell,
* two other cells are already occupied (so exactly one free cell remains),
* 甲's page 2 is not printed yet; 乙's single sheet is not printed yet.

We build that with the ordinary :class:`ReleaseService` primitives, then hand
the service to the :class:`~secureprint.planner.Planner` for the two arrival
changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from .planner import Party, Planner
from .service import ReleaseService


def build_one_free_cell_world(svc: ReleaseService, *, jia_job: str, yi_job: str) -> dict:
    """Construct the exact starting state described in the exercise.

    Cells: ``JIA`` (甲's own, holds page 1), ``OCC1``/``OCC2`` (already full,
    unrelated), ``FREE`` (the one remaining empty cell).  That leaves exactly
    one free *safe* cell.
    """
    svc.add_cell("JIA", owner="甲")
    svc.add_cell("OCC1")
    svc.add_cell("OCC2")
    svc.add_cell("FREE")

    # commitments are signed before any paper exists
    svc.sign_commitment("甲", jia_job, in_person=True)
    svc.sign_commitment("乙", yi_job, in_person=True)
    svc.submit_job(jia_job, "甲", pages=2, in_person=True)
    svc.submit_job(yi_job, "乙", pages=1, in_person=True)

    # 甲 page 1: already printed, both faces confirmed, in 甲's own cell
    svc.place_confirmed_sheet("JIA-P1", jia_job, 1, "甲", "JIA")

    # two unrelated occupied cells (someone else's paper, not yet collected)
    svc.place_confirmed_sheet("OCC1-P1", _foreign_job(svc), 1, "丙", "OCC1")
    svc.place_confirmed_sheet("OCC2-P1", _foreign_job2(svc), 1, "丁", "OCC2")

    return {
        "free": svc.free_cells(),
        "cells": svc.cells(),
    }


def _foreign_job(svc: ReleaseService) -> str:
    if "FOREIGN1" not in svc._jobs:
        svc.sign_commitment("丙", "FOREIGN1", in_person=True)
        svc.submit_job("FOREIGN1", "丙", pages=1, in_person=True)
    return "FOREIGN1"


def _foreign_job2(svc: ReleaseService) -> str:
    if "FOREIGN2" not in svc._jobs:
        svc.sign_commitment("丁", "FOREIGN2", in_person=True)
        svc.submit_job("FOREIGN2", "丁", pages=1, in_person=True)
    return "FOREIGN2"


@dataclass
class ArrivalChange:
    label: str
    arrival: Dict[str, float]
    note: str


def default_parties(jia_job: str = "JIA", yi_job: str = "YI") -> Dict[str, Party]:
    return {
        "甲": Party(
            name="甲",
            job_id=jia_job,
            comes_from=2.0,
            comes_to=8.0,
            window=1.0,
            must_collect_all=True,
        ),
        "乙": Party(
            name="乙",
            job_id=yi_job,
            comes_from=0.0,
            comes_to=6.0,
            window=1.0,
            must_collect_all=False,
        ),
    }


def run_two_arrival_changes(svc: ReleaseService, *, serial_seq=None) -> dict:
    """Simulate the planner under two different temporary arrival changes.

    Both start from the identical world (one free cell, 甲 page 1 already in
    甲's cell).  The only difference is when 甲 / 乙 actually show up, and the
    two changes make the *recommended* order flip:

    * 甲晚来、乙按期  -> 先办乙 (甲-first would leave 乙 empty-handed)
    * 乙改期、甲按期  -> 先补甲 (乙-first would leave 甲's set incomplete)
    """
    parties = default_parties()
    changes = [
        ArrivalChange(
            label="甲晚到、乙按期",
            arrival={"甲": 8.0, "乙": 0.0},
            note="甲拖到窗口末尾才出现，乙现在就到",
        ),
        ArrivalChange(
            label="乙改期、甲按期",
            arrival={"甲": 2.0, "乙": 5.0},
            note="乙临时改到较晚，甲按原计划出现",
        ),
    ]
    out: Dict[str, dict] = {}
    for ch in changes:
        planner = Planner(svc, list(parties.values()), serial_seq=serial_seq)
        # choose() restores state, so each change is evaluated independently
        decision = planner.choose(ch.arrival)
        out[ch.label] = {
            "note": ch.note,
            "arrival": dict(ch.arrival),
            "decision": decision,
        }
    return out
