from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path

from pcb_review.geometry import fmt
from pcb_review.model import Board, Hole, IsolationRule, Rect, Circle, Terminal
from pcb_review.plot import export_public_package, write_stack_overlay
from pcb_review.svgio import write_drill_csv

ROOT = Path(__file__).resolve().parent
BUILD = ROOT / "build"
PITCH = 10.0
CELL = 9.0
PAD_R = 4.5
VIA_D = 2.4
DRIFT_X = 4.4


def base_board(name: str, private_variant: str) -> Board:
    board = Board(
        name=name,
        grid_pitch=PITCH,
        public_bounds=(0.0, 0.0, 40.0, 30.0),
        isolation=[IsolationRule("P", "Q")],
    )
    # Public, releasable interface reroute geometry. Cell spacing is one grid unit.
    board.copper = [
        # P top: P_A and P_B are deliberately a same-layer continuity pair.
        Rect(0.5, 0.5, CELL, CELL, "top"),
        Rect(0.5, 10.5, CELL, CELL, "top"),
        Rect(0.5, 20.5, CELL, CELL, "top"),
        Rect(0.5, 9.5, CELL, 1.0, "top"),
        Rect(0.5, 19.5, CELL, 1.0, "top"),
        # P bottom: isolated from the top after its cross-layer barrel is removed.
        Rect(0.5, 0.5, CELL, CELL, "bottom"),
        Rect(0.5, 10.5, CELL, CELL, "bottom"),
        Rect(4.75, 5.0, 0.5, 5.5, "bottom"),
        # Q top and bottom, joined only by the Q plated via.
        Rect(10.5, 0.5, CELL, CELL, "top"),
        Rect(10.5, 10.5, CELL, CELL, "bottom"),
        Rect(14.75, 5.0, 0.5, 5.5, "bottom"),
    ]
    board.pads = [
        Circle(5.0, 5.0, PAD_R, "top"),
        Circle(5.0, 5.0, PAD_R, "bottom"),
        Circle(15.0, 5.0, PAD_R, "top"),
        Circle(15.0, 5.0, PAD_R, "bottom"),
    ]
    board.openings = [
        Rect(2.0, 2.0, 6.0, 6.0, "top"),
        Rect(2.0, 2.0, 6.0, 6.0, "bottom"),
        Rect(12.0, 2.0, 6.0, 6.0, "top"),
        Rect(12.0, 12.0, 6.0, 6.0, "bottom"),
    ]
    board.holes = [
        Hole("P_VIA", 5.0, 5.0, VIA_D, True),
        Hole("Q_VIA", 15.0, 5.0, VIA_D, True),
    ]
    board.terminals = [
        Terminal("P_A", 5.0, 5.0, "top", "P"),
        Terminal("P_C", 5.0, 15.0, "bottom", "P"),
        Terminal("P_B", 5.0, 25.0, "top", "P"),
        Terminal("Q_A", 15.0, 5.0, "top", "Q"),
        Terminal("Q_B", 15.0, 15.0, "bottom", "Q"),
    ]

    # Private routes stay beyond x=40 and never enter the exported public package.
    if private_variant == "serpentine":
        board.private_routes = [
            Rect(41.0, 2.0, 8.0, 2.0, "top"),
            Rect(47.0, 2.0, 2.0, 8.0, "top"),
            Rect(41.0, 8.0, 8.0, 2.0, "top"),
            Rect(41.0, 14.0, 2.0, 12.0, "bottom"),
            Rect(41.0, 24.0, 8.0, 2.0, "bottom"),
        ]
    elif private_variant == "diagonal-staircase":
        board.private_routes = [
            Rect(41.0, 2.0, 4.0, 2.0, "top"),
            Rect(43.0, 4.0, 4.0, 2.0, "top"),
            Rect(45.0, 6.0, 4.0, 2.0, "top"),
            Rect(41.0, 14.0, 6.0, 2.0, "bottom"),
            Rect(43.0, 16.0, 6.0, 2.0, "bottom"),
            Rect(45.0, 18.0, 6.0, 2.0, "bottom"),
        ]
    else:
        raise ValueError(f"unknown private variant: {private_variant}")
    return board


def write_actual_drill(path: Path, board: Board, p_plated: bool, drift: float = 0.0) -> list[Hole]:
    holes = []
    for hole in board.holes:
        x = hole.x + drift if hole.name == "P_VIA" else hole.x
        plated = p_plated if hole.name == "P_VIA" else hole.plated
        holes.append(replace(hole, x=x, plated=plated))
    write_drill_csv(path, holes)
    return holes


def write_work_order(path: Path, title: str, scope: list[str]) -> None:
    path.write_text(
        "# Work order\n\n" + "\n".join(f"- {item}" for item in scope) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    if BUILD.exists():
        shutil.rmtree(BUILD)
    BUILD.mkdir(parents=True)

    original = base_board("teaching-interface-restored", "serpentine")
    npth = base_board("teaching-interface-npth-workorder", "serpentine")
    npth.holes[0] = Hole("P_VIA", 5.0, 5.0, VIA_D, False)
    private_b = base_board("teaching-interface-private-staircase", "diagonal-staircase")

    internal = BUILD / "internal"
    internal.mkdir()
    for board in (original, npth, private_b):
        name = board.name
        with open(internal / f"{name}.json", "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "name": name,
                    "grid_pitch": PITCH,
                    "public_bounds": {"x": 0, "y": 0, "w": 40, "h": 30},
                    "copper": [rect.__dict__ for rect in board.copper],
                    "pads": [pad.__dict__ for pad in board.pads],
                    "openings": [opening.__dict__ for opening in board.openings],
                    "private_routes": [route.__dict__ for route in board.private_routes],
                    "holes": [hole.__dict__ for hole in board.holes],
                    "terminals": [terminal.__dict__ for terminal in board.terminals],
                    "isolation": [rule.__dict__ for rule in board.isolation],
                },
                handle,
                indent=2,
            )

    package = BUILD / "public-package"
    hashes = export_public_package(original, package)
    export_public_package(private_b, BUILD / "private-b-package")
    write_stack_overlay(original, BUILD / "local-stack-diagnostic.svg")

    work_orders = BUILD / "work-orders"
    work_orders.mkdir()
    write_work_order(
        work_orders / "restored-scope.md",
        "Restored two-via board",
        [
            "P_VIA is plated and joins P_A/P_C.",
            "P_A/P_B must remain connected by top-layer copper.",
            "Q_A/Q_B must remain connected by the plated Q_VIA.",
            "P and Q remain contract-isolated.",
        ],
    )
    write_work_order(
        work_orders / "npth-scope.md",
        "Single-via isolation work order",
        [
            "Change only P_VIA to non-plated positioning hole; artwork is unchanged.",
            "P_A/P_B must remain connected by top-layer copper.",
            "P_A/P_C is expected open and is not a delivery continuity requirement.",
            "Q_A/Q_B must remain connected; P and Q remain isolated.",
        ],
    )
    write_work_order(
        work_orders / "fixture-drift-scope.md",
        "One-fixture drift hold",
        [
            "Only board B P_VIA fixture may be shifted.",
            "Review must stop if the shifted barrel reaches an adjacent grid cell.",
            "Unshifted board A remains releasable.",
        ],
    )

    fabrication = BUILD / "fabrication"
    fabrication.mkdir()
    actual_drills = {
        "boardA": (
            fabrication / "boardA-nominal-drill.csv",
            write_actual_drill(fabrication / "boardA-nominal-drill.csv", original, True, 0.0),
        ),
        "boardB": (
            fabrication / "boardB-drifted-drill.csv",
            write_actual_drill(fabrication / "boardB-drifted-drill.csv", original, True, DRIFT_X),
        ),
        "npth-board": (
            fabrication / "npth-drill.csv",
            write_actual_drill(fabrication / "npth-drill.csv", npth, False, 0.0),
        ),
    }

    for board_name, (actual_drill, actual_holes) in actual_drills.items():
        board_dir = fabrication / board_name
        shutil.copytree(package, board_dir / "delivered-package")
        shutil.copy(actual_drill, board_dir / "actual-drill.csv")
        actual_board = replace(original, name=f"{board_name}-actual-stack", holes=actual_holes)
        write_stack_overlay(actual_board, board_dir / "actual-stack-diagnostic.svg")

    # The fixture's own actual-drill names are coordinate rows only; retain a local
    # mapping in its report; the deliverable drill.csv intentionally has no hole names.
    with open(BUILD / "fixture-expected-result.json", "w", encoding="utf-8") as handle:
        json.dump(
            {
                "nominal": "RELEASE",
                "npth": "RELEASE for scoped pairs; P cross-layer pair open as ordered",
                "single_fixture_drift": "STOP; P plated barrel enters adjacent Q grid cell and shorts P to Q",
                "private_shape_equivalence": "public package hashes identical for both internal route variants",
                "public_hash": hashes,
            },
            handle,
            indent=2,
        )


if __name__ == "__main__":
    main()
