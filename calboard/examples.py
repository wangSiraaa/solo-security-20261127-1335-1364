"""Bundled example boards and calibration programs.

These are the fixtures the exercise scenarios are built from.  They are pure
data; the CLI and tests consume them directly.
"""

from __future__ import annotations

from .model import Board


def _board(name: str, domains, cells, wiring, links, reset_policy,
           initial_rounds=None) -> Board:
    return Board.from_dict(
        {
            "name": name,
            "domains": domains,
            "cells": cells,
            "wiring": [{"from": a, "to": b} for a, b in wiring],
            "links": [{"a": a, "b": b} for a, b in links],
            "reset_policy": reset_policy,
            "initial_rounds": initial_rounds or {},
        }
    )


# ---------------------------------------------------------------------------
# Board A: two domains, one retained public cell, one raw tenant cell.
# Models "a local reset clears one half of the calibration board".
# ---------------------------------------------------------------------------
BOARD_TWO_DOMAIN = _board(
    name="two_domain_local_reset",
    domains=["left", "right"],
    cells=[
        # public calibration results, each cleared by its own domain
        {"name": "left_cal", "domain": "left", "kind": "public", "clear_with": ["left"],
         "initial": {"value": None, "gen": None}},
        {"name": "right_cal", "domain": "right", "kind": "public", "clear_with": ["right"],
         "initial": {"value": None, "gen": None}},
        # a public result that survives resets (retained)
        {"name": "retained_cal", "domain": "right", "kind": "public", "retained": True,
         "initial": {"value": 100, "gen": "gen0"}},
        # tenant raw metered value -- must never leave the board
        {"name": "tenant_raw", "domain": "left", "kind": "raw", "retained": True,
         "initial": {"value": 424242, "gen": "gen0"}},
    ],
    wiring=[],
    links=[("left", "right")],
    reset_policy={
        "power_on": {"order": ["left", "right"], "max_stagger": 2, "latency": 1},
        "warm": {"order": ["left", "right"], "max_stagger": 1, "latency": 1},
        "local_power_loss": {"order": ["left"], "max_stagger": 0, "latency": 1},
        "local_right": {"order": ["right"], "max_stagger": 0, "latency": 1},
    },
)

BOARD_TWO_DOMAIN_PROGRAM = {
    "reset_kind": "power_on",
    "round_gen": "gen1",
    "want": ["left_cal", "right_cal", "retained_cal"],
    "writes": {"left_cal": 512, "right_cal": 77, "retained_cal": 100},
}


# ---------------------------------------------------------------------------
# Board B: retained cell holding the *same number* as the new round but an
# older generation.  Numeric equality must not be treated as freshness.
# ---------------------------------------------------------------------------
BOARD_STALE_RETAINED = _board(
    name="stale_retained_equal_value",
    domains=["a", "b"],
    cells=[
        {"name": "a_cal", "domain": "a", "kind": "public", "clear_with": ["a"],
         "initial": {"value": None, "gen": None}},
        {"name": "b_cal", "domain": "b", "kind": "public", "clear_with": ["b"],
         "initial": {"value": None, "gen": None}},
        # retained, same numeric value the program will write this round,
        # but tagged with the previous round.
        {"name": "carried_cal", "domain": "b", "kind": "public", "retained": True,
         "initial": {"value": 900, "gen": "gen0"}},
    ],
    wiring=[],
    links=[("a", "b")],
    reset_policy={
        "power_on": {"order": ["a", "b"], "max_stagger": 1, "latency": 1},
    },
)

BOARD_STALE_RETAINED_PROGRAM = {
    "reset_kind": "power_on",
    "round_gen": "gen1",
    "want": ["a_cal", "carried_cal"],
    # note: carried_cal is NOT in writes, yet its old value (900) equals the
    # value a fresh calibration would produce.
    "writes": {"a_cal": 900, "b_cal": 900},
}


# ---------------------------------------------------------------------------
# Board C: two public maintenance ports.  Port 1 must publish early from a
# single healthy domain; port 2 needs the full cross-domain calibration.
# ---------------------------------------------------------------------------
BOARD_TWO_PORTS = _board(
    name="two_ports_local_reset",
    domains=["main", "aux"],
    cells=[
        {"name": "main_cal", "domain": "main", "kind": "public", "clear_with": ["main"],
         "initial": {"value": None, "gen": None}},
        {"name": "aux_cal", "domain": "aux", "kind": "public", "clear_with": ["aux"],
         "initial": {"value": None, "gen": None}},
        # independent public measurement held by the healthy (main) domain,
        # retained across an aux-only local reset.
        {"name": "main_public_meas", "domain": "main", "kind": "public",
         "retained": True, "initial": {"value": 55, "gen": "gen0"}},
    ],
    wiring=[],
    links=[("main", "aux")],
    reset_policy={
        # reset only aux; main stays up
        "local_aux": {"order": ["aux"], "max_stagger": 0, "latency": 2},
        "power_on": {"order": ["main", "aux"], "max_stagger": 2, "latency": 1},
    },
)

# Port 1: independent public measurement from the healthy domain, early tick.
# Port 2: full cross-domain calibration, needs the late aux domain.
BOARD_TWO_PORTS_PROGRAM = {
    "reset_kind": "local_aux",
    "round_gen": "gen1",
    "want": ["main_public_meas", "aux_cal"],
    "writes": {"aux_cal": 808},
    "windows": [
        # port 1 publishes early from the healthy domain alone
        {"at": 3, "window": "port1", "want": ["main_public_meas"], "mode": "fixed"},
        # port 2 requires the full calibration, which settles later
        {"at": 3, "window": "port2", "want": ["main_public_meas", "aux_cal"],
         "mode": "fixed"},
    ],
}


# ---------------------------------------------------------------------------
# Board D: two maintenance ports where port 2 demands an earlier tick than
# port 1 can manage; port 1's earlier publication must survive port 2's miss.
# ---------------------------------------------------------------------------
BOARD_EARLY_SECOND_PORT = _board(
    name="early_second_port",
    domains=["fast", "slow"],
    cells=[
        {"name": "fast_cal", "domain": "fast", "kind": "public", "clear_with": ["fast"],
         "initial": {"value": None, "gen": None}},
        {"name": "slow_cal", "domain": "slow", "kind": "public", "clear_with": ["slow"],
         "initial": {"value": None, "gen": None}},
    ],
    wiring=[],
    links=[("fast", "slow")],
    reset_policy={
        "power_on": {"order": ["fast", "slow"], "max_stagger": 1, "latency": 2},
    },
)

BOARD_EARLY_SECOND_PORT_PROGRAM = {
    "reset_kind": "power_on",
    "round_gen": "gen1",
    "want": ["fast_cal", "slow_cal"],
    "writes": {"fast_cal": 11, "slow_cal": 22},
    "windows": [
        # port1 opens late enough to publish the full calibration
        {"at": 4, "window": "port1", "want": ["fast_cal", "slow_cal"],
         "generation": "gen1", "mode": "fixed"},
        # port2 insists on an earlier tick and misses; port1 must not be
        # retracted because of port2.
        {"at": 2, "window": "port2", "want": ["fast_cal", "slow_cal"],
         "generation": "gen1", "mode": "fixed"},
    ],
}


EXAMPLES = {
    "two_domain_local_reset": (
        BOARD_TWO_DOMAIN,
        "Local reset clears one half; one retained public cell + one raw cell.",
    ),
    "stale_retained_equal_value": (
        BOARD_STALE_RETAINED,
        "Retained cell holds the new number under an old generation.",
    ),
    "two_ports_local_reset": (
        BOARD_TWO_PORTS,
        "Partial reset: port1 uses healthy-domain measurement, port2 full cal.",
    ),
    "early_second_port": (
        BOARD_EARLY_SECOND_PORT,
        "Second port's earlier deadline misses; first port's publication stands.",
    ),
}
