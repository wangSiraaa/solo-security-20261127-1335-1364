from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

from .geometry import bounds_contain, fmt
from .model import Board, Hole, Rect, Shape
from .svgio import shape_path, write_drill_csv, write_svg


def _public_shapes(board: Board, shapes: Iterable[Shape]) -> List[Shape]:
    result: List[Shape] = []
    for shape in shapes:
        if bounds_contain(board.public_bounds, shape):
            result.append(shape)
        else:
            raise ValueError(
                f"exported geometry leaves fixed public area: {shape_path(shape)}"
            )
    return result


def _mask_shapes(board: Board, layer: str) -> List[Rect]:
    margin = 1.0
    return [
        Rect(
            item.x - margin,
            item.y - margin,
            item.w + 2 * margin,
            item.h + 2 * margin,
            layer,
        )
        for item in board.openings
        if item.layer == layer and bounds_contain(board.public_bounds, item)
    ]


def _excellon(holes: Sequence[Hole]) -> str:
    plated = [hole for hole in holes if hole.plated]
    nonplated = [hole for hole in holes if not hole.plated]

    def block(title: str, tools: List[Hole], plated_block: bool) -> List[str]:
        diameters = sorted({hole.diameter for hole in tools})
        tool_numbers = {diameter: index + 1 for index, diameter in enumerate(diameters)}
        lines = [f"; {title}", "M48", "FMAT,2"]
        for diameter in diameters:
            lines.append(f"T{tool_numbers[diameter]:02d}C{fmt(diameter)}")
        lines.append("%")
        if not plated_block:
            lines.append("; NON-PLATED TOOLS")
        for hole in tools:
            lines.append(f"T{tool_numbers[hole.diameter]:02d}")
            lines.append(f"X{_inch(hole.x)}Y{_inch(hole.y)}")
        lines.append("T00")
        return lines

    all_lines = ["; drill coordinates; units=inch; no object names"]
    all_lines.extend(block("PLATED", plated, True))
    if nonplated:
        all_lines.extend(block("NON-PLATED", nonplated, False))
    all_lines.append("M30")
    return "\n".join(all_lines) + "\n"


def _inch(millimeters: float) -> str:
    return f"{millimeters / 25.4:05.4f}".replace(".", "")


def export_public_package(board: Board, directory: str | Path) -> Dict[str, str]:
    """Write only the fixed, releasable interface area and anonymous drill data."""
    out = Path(directory)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    for shape in board.private_routes:
        if bounds_contain(board.public_bounds, shape):
            raise ValueError("a private route enters the public release area")

    _x, _y, width, height = board.public_bounds
    hashes: Dict[str, str] = {}
    for layer in board.layers:
        copper = _public_shapes(
            board,
            [item for item in board.copper + board.pads if item.layer == layer],
        )
        mask = _mask_shapes(board, layer)
        copper_path = out / f"{layer}-copper.svg"
        mask_path = out / f"{layer}-soldermask.svg"
        write_svg(
            copper_path,
            width,
            height,
            [(shape_path(shape), "#c87533") for shape in copper],
        )
        write_svg(
            mask_path,
            width,
            height,
            [(shape_path(shape), "#000000") for shape in mask],
        )

    public_holes = tuple(
        Hole(
            name="",
            x=hole.x,
            y=hole.y,
            diameter=hole.diameter,
            plated=hole.plated,
            layers=hole.layers,
        )
        for hole in board.holes
        if board.public_bounds[0] <= hole.x <= board.public_bounds[0] + board.public_bounds[2]
        and board.public_bounds[1] <= hole.y <= board.public_bounds[1] + board.public_bounds[3]
    )
    drill_csv = out / "drill.csv"
    drill_xln = out / "drill.xln"
    write_drill_csv(drill_csv, public_holes)
    drill_xln.write_text(_excellon(public_holes), encoding="utf-8")

    terminals = out / "public-terminals.csv"
    terminals.write_text(
        "terminal,x,y,layer\n"
        + "".join(
            f"{terminal.name},{fmt(terminal.x)},{fmt(terminal.y)},{terminal.layer}\n"
            for terminal in board.terminals
            if board.public_bounds[0] <= terminal.x <= board.public_bounds[0] + board.public_bounds[2]
            and board.public_bounds[1] <= terminal.y <= board.public_bounds[1] + board.public_bounds[3]
        ),
        encoding="utf-8",
    )

    for path in sorted(out.iterdir()):
        if path.is_file():
            hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = out / "SHA256SUMS"
    manifest.write_text(
        "".join(f"{digest}  {name}\n" for name, digest in hashes.items()),
        encoding="utf-8",
    )
    hashes[manifest.name] = hashlib.sha256(manifest.read_bytes()).hexdigest()
    return hashes


def write_stack_overlay(board: Board, path: str | Path) -> None:
    """Local/fabrication diagnostic drawing; this file is not an artwork deliverable."""
    _x, _y, width, height = board.public_bounds
    paths: List[Tuple[str, str]] = []
    for shape in board.copper:
        paths.append((shape_path(shape), "#d68f4a" if shape.layer == "top" else "#5b8db8"))
    for shape in board.pads:
        paths.append((shape_path(shape), "#9c5a1f" if shape.layer == "top" else "#2f5f85"))
    for hole in board.holes:
        radius = hole.diameter / 2
        circle = f"M{fmt(hole.x-radius)},{fmt(hole.y)}a{fmt(radius)},{fmt(radius)} 0 1,0 {fmt(2*radius)},0a{fmt(radius)},{fmt(radius)} 0 1,0 {fmt(-2*radius)},0Z"
        paths.append((circle, "#111111" if hole.plated else "#ffffff"))
    write_svg(path, width, height, paths)
