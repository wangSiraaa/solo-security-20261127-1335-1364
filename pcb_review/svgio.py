from __future__ import annotations

import csv
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

from .geometry import fmt
from .model import Circle, Hole, Rect, Shape

SVG_NS = "http://www.w3.org/2000/svg"


def rect_path(rect: Rect) -> str:
    x2 = rect.x + rect.w
    y2 = rect.y + rect.h
    return (
        f"M{fmt(rect.x)},{fmt(rect.y)} "
        f"H{fmt(x2)} V{fmt(y2)} H{fmt(rect.x)} Z"
    )


def circle_path(circle: Circle) -> str:
    left = circle.x - circle.r
    right = circle.x + circle.r
    return (
        f"M{fmt(left)},{fmt(circle.y)} "
        f"A{fmt(circle.r)},{fmt(circle.r)} 0 1,0 {fmt(right)},{fmt(circle.y)} "
        f"A{fmt(circle.r)},{fmt(circle.r)} 0 1,0 {fmt(left)},{fmt(circle.y)} Z"
    )


def shape_path(shape: Shape) -> str:
    if isinstance(shape, Rect):
        return rect_path(shape)
    return circle_path(shape)


def write_svg(
    path: str | Path,
    width: float,
    height: float,
    paths: Sequence[Tuple[str, str]],
) -> None:
    ET.register_namespace("", SVG_NS)
    svg = ET.Element(
        f"{{{SVG_NS}}}svg",
        {
            "viewBox": f"0 0 {fmt(width)} {fmt(height)}",
            "width": fmt(width),
            "height": fmt(height),
        },
    )
    group = ET.SubElement(svg, f"{{{SVG_NS}}}g")
    for index, (d, fill) in enumerate(paths, 1):
        ET.SubElement(
            group,
            f"{{{SVG_NS}}}path",
            {"id": f"p{index}", "fill": fill, "d": d},
        )
    ET.ElementTree(svg).write(path, encoding="utf-8", xml_declaration=True)


def _tokenize_path(d: str) -> List[Tuple[str, List[float]]]:
    parts = re.findall(r"[A-Za-z]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", d)
    commands: List[Tuple[str, List[float]]] = []
    index = 0
    while index < len(parts):
        command = parts[index]
        index += 1
        if not re.fullmatch(r"[A-Za-z]", command):
            raise ValueError(f"path command expected in {d!r}")
        values: List[float] = []
        while index < len(parts) and not re.fullmatch(r"[A-Za-z]", parts[index]):
            values.append(float(parts[index]))
            index += 1
        commands.append((command, values))
    return commands


def read_svg_shapes(path: str | Path, layer: str) -> List[Shape]:
    root = ET.parse(path).getroot()
    shapes: List[Shape] = []
    for element in root.iter():
        tag = element.tag.split("}", 1)[-1]
        if tag != "path":
            continue
        d = element.attrib.get("d", "")
        shapes.extend(_path_to_shapes(d, layer))
    return shapes


def _path_to_shapes(d: str, layer: str) -> List[Shape]:
    commands = _tokenize_path(d)
    if not commands:
        return []
    if [command for command, _ in commands] == ["M", "H", "V", "H", "Z"]:
        m, h1, v1, h2, _z = commands
        x, y = m[1]
        x2 = h1[1][0]
        y2 = v1[1][0]
        if abs(h2[1][0] - x) > 1e-9:
            raise ValueError(f"rect path must return to its start x: {d!r}")
        return [Rect(min(x, x2), min(y, y2), abs(x2 - x), abs(y2 - y), layer)]
    if [command for command, _ in commands] == ["M", "A", "A", "Z"]:
        m, arc1, arc2, _ = commands
        start_x, start_y = m[1]
        rx, ry, _rotation, _large, _sweep, end_x, end_y = arc1[1]
        if abs(rx - ry) <= 1e-9:
            center_x = (start_x + end_x) / 2
            center_y = start_y
            if abs(start_y - end_y) <= 1e-9 and abs(center_x - start_x) <= rx + 1e-9:
                return [Circle(center_x, center_y, rx, layer)]
    raise ValueError(f"review tool only accepts generated rect/circle paths: {d!r}")


def write_drill_csv(path: str | Path, holes: Iterable[Hole]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["x", "y", "diameter", "plated"])
        for hole in holes:
            writer.writerow(
                [fmt(hole.x), fmt(hole.y), fmt(hole.diameter), "yes" if hole.plated else "no"]
            )


def read_drill_csv(path: str | Path) -> List[Hole]:
    holes: List[Hole] = []
    with open(path, "r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        expected = {"x", "y", "diameter", "plated"}
        if set(reader.fieldnames or []) != expected:
            raise ValueError("drill CSV must have exactly x,y,diameter,plated columns")
        for index, row in enumerate(reader, 1):
            holes.append(
                Hole(
                    name=f"H{index}",
                    x=float(row["x"]),
                    y=float(row["y"]),
                    diameter=float(row["diameter"]),
                    plated=row["plated"].lower() in {"yes", "true", "1", "y"},
                )
            )
    return holes
