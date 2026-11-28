from __future__ import annotations

import csv
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .geometry import describe_shape, fmt, point_in_shape, shapes_touch
from .model import Circle, Hole, IsolationRule, Shape, Terminal
from .svgio import read_drill_csv, read_svg_shapes


@dataclass
class Finding:
    severity: str
    code: str
    message: str


@dataclass
class ContinuityRow:
    a: str
    b: str
    connected: bool
    path: str


@dataclass
class Component:
    node: int
    terminal_names: List[str] = field(default_factory=list)
    shapes: List[Shape] = field(default_factory=list)
    holes: List[Hole] = field(default_factory=list)


@dataclass
class HoleReview:
    actual: Hole
    expected: Optional[Hole]
    offset: float
    entered: List[Shape]
    plated_mismatch: bool = False


@dataclass
class ReviewReport:
    status: str
    findings: List[Finding]
    continuity: List[ContinuityRow]
    components: List[Component]
    holes: List[HoleReview]

    def to_text(self) -> str:
        lines = [f"STATUS: {self.status}", "", "FINDINGS:"]
        if self.findings:
            lines.extend(f"- {item.severity} {item.code}: {item.message}" for item in self.findings)
        else:
            lines.append("- none")
        lines.extend(["", "CONTINUITY:"])
        lines.extend(
            f"- {row.a} -> {row.b}: {'CONDUCTS' if row.connected else 'OPEN'} | {row.path}"
            for row in self.continuity
        )
        lines.extend(["", "COMPONENTS:"])
        for index, component in enumerate(self.components, 1):
            shapes = "; ".join(describe_shape(shape) for shape in component.shapes)
            holes = "; ".join(
                f"hole({fmt(hole.x)},{fmt(hole.y)})d={fmt(hole.diameter)}"
                for hole in component.holes
            )
            terminals = ",".join(component.terminal_names) or "no public terminal"
            lines.append(
                f"- C{index} terminals=[{terminals}] holes=[{holes}] shapes=[{shapes}]"
            )
        return "\n".join(lines) + "\n"


class _DSU:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, node: int) -> int:
        while self.parent[node] != node:
            self.parent[node] = self.parent[self.parent[node]]
            node = self.parent[node]
        return node

    def union(self, a: int, b: int) -> None:
        ra = self.find(a)
        rb = self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _load_terminals(path: str | Path) -> List[Terminal]:
    terminals: List[Terminal] = []
    with open(path, "r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if set(reader.fieldnames or []) != {"terminal", "x", "y", "layer"}:
            raise ValueError("terminal CSV must have exactly terminal,x,y,layer columns")
        for row in reader:
            terminals.append(
                Terminal(
                    name=row["terminal"],
                    x=float(row["x"]),
                    y=float(row["y"]),
                    layer=row["layer"],
                )
            )
    return terminals


def load_package(directory: str | Path) -> Tuple[List[Shape], List[Hole], List[Terminal]]:
    root = Path(directory)
    shapes: List[Shape] = []
    for layer in ("top", "bottom"):
        shapes.extend(read_svg_shapes(root / f"{layer}-copper.svg", layer))
    return shapes, read_drill_csv(root / "drill.csv"), _load_terminals(root / "public-terminals.csv")


def _barrel(hole: Hole, layer: str) -> Circle:
    return Circle(hole.x, hole.y, hole.diameter / 2, layer)


def _edge_label(left: int, right: int, labels: Dict[Tuple[int, int], str]) -> str:
    return labels.get((min(left, right), max(left, right)), "connected")


def _shortest_path(
    graph: Dict[int, Set[int]], labels: Dict[Tuple[int, int], str], start: int, end: int
) -> Tuple[bool, List[int]]:
    queue = deque([(start, [start])])
    seen = {start}
    while queue:
        node, trail = queue.popleft()
        if node == end:
            return True, trail
        for nxt in sorted(graph[node]):
            if nxt not in seen:
                seen.add(nxt)
                queue.append((nxt, trail + [nxt]))
    return False, [start, end]


def review_fabrication(
    shapes: Sequence[Shape],
    actual_holes: Sequence[Hole],
    terminals: Sequence[Terminal],
    expected_holes: Sequence[Hole],
    required_pairs: Sequence[Tuple[str, str]],
    isolation: Sequence[IsolationRule] = (),
    internal_nets: Optional[Dict[str, str]] = None,
    grid_pitch: float = 10.0,
    drill_tolerance: float = 0.05,
    allowed_npth: Optional[Set[Tuple[float, float]]] = None,
    expected_open_pairs: Sequence[Tuple[str, str]] = (),
) -> ReviewReport:
    # Nodes: first every artwork shape, then one barrel node per actual hole.
    barrel_offset = len(shapes)
    dsu = _DSU(barrel_offset + len(actual_holes))
    graph: Dict[int, Set[int]] = {node: set() for node in range(barrel_offset + len(actual_holes))}
    edge_labels: Dict[Tuple[int, int], str] = {}

    def add_edge(a: int, b: int, label: str) -> None:
        if a == b:
            return
        dsu.union(a, b)
        graph[a].add(b)
        graph[b].add(a)
        key = (min(a, b), max(a, b))
        edge_labels.setdefault(key, label)

    # Same-layer copper and pad overlap. Names/net attributes do not create isolation.
    for i, left in enumerate(shapes):
        for j in range(i + 1, len(shapes)):
            right = shapes[j]
            if left.layer == right.layer and shapes_touch(left, right):
                add_edge(i, j, f"same-layer copper contact: {describe_shape(left)} / {describe_shape(right)}")

    # A real plated barrel is the only modeled cross-layer connection.
    for h, hole in enumerate(actual_holes):
        barrel_node = barrel_offset + h
        if not hole.plated:
            continue
        for index, shape in enumerate(shapes):
            if shape.layer in hole.layers and shapes_touch(_barrel(hole, shape.layer), shape):
                add_edge(
                    barrel_node,
                    index,
                    f"plated barrel ({fmt(hole.x)},{fmt(hole.y)}) contacts {describe_shape(shape)}",
                )

    terminal_nodes: Dict[str, int] = {}
    findings: List[Finding] = []
    for terminal in terminals:
        attached: List[int] = []
        for index, shape in enumerate(shapes):
            if shape.layer == terminal.layer and point_in_shape(terminal.x, terminal.y, shape):
                attached.append(index)
        for h, hole in enumerate(actual_holes):
            barrel = _barrel(hole, terminal.layer)
            if terminal.layer in hole.layers and point_in_shape(terminal.x, terminal.y, barrel):
                attached.append(barrel_offset + h)
        if not attached:
            findings.append(
                Finding("STOP", "TERMINAL_NOT_ATTACHED", f"{terminal.name} touches no delivered copper or barrel")
            )
            continue
        first = attached[0]
        terminal_nodes[terminal.name] = first
        for other in attached[1:]:
            add_edge(first, other, f"terminal {terminal.name} attaches to overlapping conductor")

    # Drill acceptance: matching anonymous coordinates, diameter, and plating status.
    hole_reviews: List[HoleReview] = []
    used: Set[int] = set()
    allowed_npth = allowed_npth or set()
    for actual in actual_holes:
        candidates = [
            (
                ((actual.x - expected.x) ** 2 + (actual.y - expected.y) ** 2) ** 0.5,
                index,
                expected,
            )
            for index, expected in enumerate(expected_holes)
            if index not in used
            and abs(actual.diameter - expected.diameter) <= drill_tolerance
        ]
        if not candidates:
            hole_reviews.append(HoleReview(actual, None, float("inf"), [], False))
            findings.append(Finding("STOP", "UNMATCHED_DRILL", f"no expected drill matches ({fmt(actual.x)},{fmt(actual.y)}) d={fmt(actual.diameter)}"))
            continue
        offset, index, expected = min(candidates, key=lambda item: item[0])
        used.add(index)
        mismatch = actual.plated != expected.plated
        entered: List[Shape] = []
        if not mismatch:
            expected_touch = {
                index
                for index, shape in enumerate(shapes)
                if shape.layer in expected.layers and shapes_touch(_barrel(expected, shape.layer), shape)
            }
            actual_touch = {
                index
                for index, shape in enumerate(shapes)
                if shape.layer in actual.layers and shapes_touch(_barrel(actual, shape.layer), shape)
            }
            entered = [shapes[index] for index in sorted(actual_touch - expected_touch)]
        review = HoleReview(actual, expected, offset, entered, mismatch)
        hole_reviews.append(review)
        if mismatch:
            allowed = (
                not actual.plated
                and expected.plated
                and (expected.x, expected.y) in allowed_npth
            )
            if allowed:
                findings.append(
                    Finding(
                        "INFO",
                        "ALLOWED_NPTH",
                        f"work-order NPTH at ({fmt(actual.x)},{fmt(actual.y)}) leaves layers electrically open",
                    )
                )
            else:
                findings.append(
                    Finding(
                        "STOP",
                        "PLATING_CHANGED",
                        f"hole at ({fmt(actual.x)},{fmt(actual.y)}) plating is {actual.plated}, expected {expected.plated}",
                    )
                )
        if offset > drill_tolerance:
            findings.append(
                Finding(
                    "STOP",
                    "DRILL_OFFSET",
                    f"hole moved {fmt(offset)} from ({fmt(expected.x)},{fmt(expected.y)}) to ({fmt(actual.x)},{fmt(actual.y)})",
                )
            )
        if entered:
            findings.append(
                Finding(
                    "STOP",
                    "HOLE_ENTERED_ADJACENT_GRID",
                    "barrel now contacts " + "; ".join(describe_shape(shape) for shape in entered),
                )
            )
    if len(used) != len(expected_holes):
        findings.append(Finding("STOP", "MISSING_DRILL", f"expected {len(expected_holes)} drills, matched {len(used)}"))

    # Isolation is proven from geometry, not inferred from a name.
    nets = internal_nets or {terminal.name: terminal.name for terminal in terminals}
    terminal_by_name = {terminal.name: terminal for terminal in terminals}
    for rule in isolation:
        a_nodes = [
            terminal_nodes[name]
            for name, net in nets.items()
            if net == rule.a and name in terminal_nodes
        ]
        b_nodes = [
            terminal_nodes[name]
            for name, net in nets.items()
            if net == rule.b and name in terminal_nodes
        ]
        if any(dsu.find(a) == dsu.find(b) for a in a_nodes for b in b_nodes):
            findings.append(
                Finding("STOP", "ISOLATION_LOST", f"contract-isolated {rule.a} and {rule.b} conduct")
            )

    rows: List[ContinuityRow] = []
    for a_name, b_name in required_pairs:
        if a_name not in terminal_nodes or b_name not in terminal_nodes:
            rows.append(ContinuityRow(a_name, b_name, False, "a terminal is not attached"))
            findings.append(Finding("STOP", "REQUIRED_PAIR_OPEN", f"{a_name} to {b_name} cannot be checked"))
            continue
        start = terminal_nodes[a_name]
        end = terminal_nodes[b_name]
        connected, trail = _shortest_path(graph, edge_labels, start, end)
        rows.append(
            ContinuityRow(
                a_name,
                b_name,
                connected,
                "direct attachment"
                if connected and len(trail) == 1
                else " -> ".join(
                    [a_name]
                    + [
                        _edge_label(trail[pos], trail[pos + 1], edge_labels)
                        for pos in range(len(trail) - 1)
                    ]
                    + [b_name]
                )
                if connected
                else "no geometric conductor path",
            )
        )
        if not connected:
            findings.append(Finding("STOP", "REQUIRED_PAIR_OPEN", f"{a_name} to {b_name} is open"))

    for a_name, b_name in expected_open_pairs:
        if a_name not in terminal_nodes or b_name not in terminal_nodes:
            findings.append(Finding("STOP", "EXPECTED_OPEN_UNCHECKED", f"{a_name} to {b_name} cannot be checked"))
            continue
        connected, trail = _shortest_path(graph, edge_labels, terminal_nodes[a_name], terminal_nodes[b_name])
        rows.append(
            ContinuityRow(
                a_name,
                b_name,
                connected,
                " -> ".join(
                    [a_name]
                    + [
                        _edge_label(trail[pos], trail[pos + 1], edge_labels)
                        for pos in range(len(trail) - 1)
                    ]
                    + [b_name]
                )
                if connected
                else "expected work-order open: no geometric conductor path",
            )
        )
        if connected:
            findings.append(Finding("STOP", "EXPECTED_OPEN_CONDUCTS", f"{a_name} to {b_name} should be open"))

    grouped: Dict[int, Component] = {}
    for name, node in terminal_nodes.items():
        root = dsu.find(node)
        grouped.setdefault(root, Component(root)).terminal_names.append(name)
    for index, shape in enumerate(shapes):
        root = dsu.find(index)
        grouped.setdefault(root, Component(root)).shapes.append(shape)
    for h, hole in enumerate(actual_holes):
        root = dsu.find(barrel_offset + h)
        grouped.setdefault(root, Component(root)).holes.append(hole)

    status = "STOP" if any(item.severity == "STOP" for item in findings) else "RELEASE"
    return ReviewReport(
        status=status,
        findings=findings,
        continuity=rows,
        components=sorted(grouped.values(), key=lambda item: item.node),
        holes=hole_reviews,
    )
