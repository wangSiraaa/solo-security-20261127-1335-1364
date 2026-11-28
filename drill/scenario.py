"""Scenario construction.

Builds a concrete two-feed / multi-chassis classroom drill with two assignment
books (作业簿) that cross-reference each other.  Everything is deterministic.
"""
from __future__ import annotations

from typing import List

from .credentials import classroom_key as _key
from .model import (
    Cabinet,
    DrillConfig,
    Feed,
    PageImage,
    Submission,
    Topology,
)


def _book_page(book: str, points_to: List[int]) -> str:
    # an index page encodes the data pages it must point at
    return '{"book": "%s", "points_to": %s}' % (book, points_to)


def build_standard() -> DrillConfig:
    feeds = (Feed("A", capacity=120.0), Feed("B", capacity=120.0))
    cabinets = (
        Cabinet("R1", ("A",), base_load=40.0, startup_peak=60.0, flush_cost=8.0),
        Cabinet("R2", ("B",), base_load=40.0, startup_peak=60.0, flush_cost=8.0),
        Cabinet("R3", ("A", "B"), base_load=35.0, startup_peak=50.0, flush_cost=8.0),
    )
    topology = Topology(feeds=feeds, cabinets=cabinets)

    keys = {"C101": _key("C101"), "C102": _key("C102")}

    subs: List[Submission] = []
    # C101 cross-book order on the dual-fed chassis: survives feed loss, must
    # be the FIRST thing saved while residual energy lasts.
    subs.append(_make_sub(
        classroom="C101", assignment="HW-A", txid="txA1", cabinet="R3",
        cross_volume=True,
        pages=[
            PageImage("vol1", 11, "data", "A-part1", 1),
            PageImage("vol2", 12, "data", "A-part2", 1),
            PageImage("vol1", 13, "index", _book_page("bookA", [11]), 1),
        ],
        submitted_tick=1,
    ))
    # C101 second order pinned to the A-only chassis -> dies with feed A.
    # Accepted + partly written, no commit: a genuine uncommitted "partial".
    subs.append(_make_sub(
        classroom="C101", assignment="HW-B", txid="txA2", cabinet="R1",
        cross_volume=False,
        pages=[
            PageImage("vol1", 21, "data", "B-part1", 1),
            PageImage("vol1", 22, "index", _book_page("bookA", [21]), 1),
        ],
        submitted_tick=1,
    ))
    # C102 cross-book order on the B-only chassis: also survives feed loss.
    subs.append(_make_sub(
        classroom="C102", assignment="HW-A", txid="txB1", cabinet="R2",
        cross_volume=True,
        pages=[
            PageImage("vol1", 31, "data", "B-part1-c102", 1),
            PageImage("vol2", 32, "data", "B-part2-c102", 1),
            PageImage("vol2", 33, "index", _book_page("bookB", [32]), 1),
        ],
        submitted_tick=2,
    ))
    # C102 third order on R3 (paid min-service) -> runs out of energy after the
    # cross orders, crossing the boundary -> pending-confirmation receipt.
    subs.append(_make_sub(
        classroom="C102", assignment="HW-C", txid="txB2", cabinet="R3",
        cross_volume=False,
        pages=[
            PageImage("vol2", 41, "data", "C-part1-c102", 1),
            PageImage("vol2", 42, "index", _book_page("bookB", [41]), 1),
        ],
        submitted_tick=3,
    ))

    return DrillConfig(
        topology=topology,
        battery_energy=50.0,
        present_feeds_after_loss=("B",),
        feed_loss_tick=3,
        kill_tick=14,
        min_service={"cabinet": "R3", "classroom": "C102", "ticks": 12},
        classroom_keys=keys,
        submissions=subs,
        chassis_flush_order=("R1", "R2", "R3"),
        shed_order=("R3", "R2", "R1"),
        receipt_policy="commit",
        startup_schedule={"R1": 0, "R2": 0, "R3": 4},
        max_ticks=32,
        flushes_per_tick=3,
        flush_ticks=1,
    )


def _make_sub(classroom: str, assignment: str, txid: str, cabinet: str,
              cross_volume: bool, pages: List[PageImage],
              submitted_tick: int) -> Submission:
    return Submission(
        classroom=classroom, assignment=assignment, txid=txid,
        cabinet=cabinet, submitted_tick=submitted_tick,
        cross_volume=cross_volume, pages=pages,
    )


def build_shared_feed_topology() -> Topology:
    """Fault: branch B, which used to carry only R2, is re-wired to feed R2 *and*
    R3 (R3 loses its dual-feed option).  Used to find the startup promise that
    could be honoured in the base wiring but can no longer be honoured here."""
    return Topology(
        feeds=(Feed("A", capacity=120.0), Feed("B", capacity=120.0)),
        cabinets=(
            Cabinet("R1", ("A",), base_load=40.0, startup_peak=60.0, flush_cost=8.0),
            Cabinet("R2", ("B",), base_load=40.0, startup_peak=60.0, flush_cost=8.0),
            Cabinet("R3", ("B",), base_load=35.0, startup_peak=50.0, flush_cost=8.0),
        ),
    )
