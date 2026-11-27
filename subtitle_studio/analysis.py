from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .package import Package
from .profiles import ClassicPolicy


@dataclass(frozen=True)
class BBox:
    min_x: int
    min_y: int
    max_x: int  # exclusive
    max_y: int  # exclusive

    @property
    def width(self) -> int:
        return self.max_x - self.min_x

    @property
    def height(self) -> int:
        return self.max_y - self.min_y

    def translated(self, dx: int, dy: int) -> "BBox":
        return BBox(self.min_x + dx, self.min_y + dy, self.max_x + dx, self.max_y + dy)

    def union(self, other: "BBox | None") -> "BBox":
        if other is None:
            return self
        return BBox(
            min(self.min_x, other.min_x),
            min(self.min_y, other.min_y),
            max(self.max_x, other.max_x),
            max(self.max_y, other.max_y),
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "min_x": self.min_x,
            "min_y": self.min_y,
            "max_x": self.max_x,
            "max_y": self.max_y,
            "width": self.width,
            "height": self.height,
        }


@dataclass(frozen=True)
class Bound:
    """Static expansion upper bound. finite=False means recursion/infinity."""

    work: int = 0
    cell_writes: int = 0
    ref_depth: int = 0
    bbox: BBox | None = None
    finite: bool = True

    @staticmethod
    def infinite() -> "Bound":
        return Bound(finite=False)

    def sequence_with(self, extra: "Bound") -> "Bound":
        if not self.finite or not extra.finite:
            return Bound.infinite()
        bbox = self.bbox.union(extra.bbox) if self.bbox is not None else extra.bbox
        return Bound(
            work=self.work + extra.work,
            cell_writes=self.cell_writes + extra.cell_writes,
            ref_depth=max(self.ref_depth, extra.ref_depth),
            bbox=bbox,
            finite=True,
        )

    def branch_max_with(self, other: "Bound") -> "Bound":
        # If either branch is recursive, the declaration has an infinite
        # possible expansion and cannot be classically admitted.
        if not self.finite or not other.finite:
            return Bound.infinite()
        bbox = self.bbox.union(other.bbox) if self.bbox is not None else other.bbox
        return Bound(
            work=max(self.work, other.work),
            cell_writes=max(self.cell_writes, other.cell_writes),
            ref_depth=max(self.ref_depth, other.ref_depth),
            bbox=bbox,
            finite=True,
        )

    def to_dict(self) -> dict[str, Any]:
        if not self.finite:
            return {"finite": False, "work": None, "cell_writes": None, "ref_depth": None, "bbox": None}
        return {
            "finite": True,
            "work": self.work,
            "cell_writes": self.cell_writes,
            "ref_depth": self.ref_depth,
            "bbox": self.bbox.to_dict() if self.bbox else None,
        }


def direct_dependencies(ops: list[dict[str, Any]]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()

    def walk(items: list[dict[str, Any]]) -> None:
        for op in items:
            if op["op"] == "ref":
                if op["ref"] not in seen:
                    seen.add(op["ref"])
                    found.append(op["ref"])
            elif op["op"] == "branch":
                for case in op["cases"]:
                    walk(case["ops"])

    walk(ops)
    return found


def cyclic_and_tainted(package: Package) -> tuple[set[str], list[list[str]]]:
    graph = {pid: direct_dependencies(ops) for pid, ops in package.primitives.items()}
    indices: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    sccs: list[list[str]] = []
    counter = 0

    def visit(node: str) -> None:
        nonlocal counter
        indices[node] = counter
        low[node] = counter
        counter += 1
        stack.append(node)
        on_stack.add(node)

        for nxt in graph[node]:
            if nxt not in indices:
                visit(nxt)
                low[node] = min(low[node], low[nxt])
            elif nxt in on_stack:
                low[node] = min(low[node], indices[nxt])

        if low[node] == indices[node]:
            component: list[str] = []
            while True:
                cur = stack.pop()
                on_stack.remove(cur)
                component.append(cur)
                if cur == node:
                    break
            sccs.append(component)

    for pid in package.primitives:
        if pid not in indices:
            visit(pid)

    cyclic: set[str] = set()
    for component in sccs:
        if len(component) > 1:
            cyclic.update(component)
        elif component[0] in graph and component[0] in graph[component[0]]:
            cyclic.add(component[0])

    # Propagate infinity backwards: anything depending on a cycle is tainted.
    reverse: dict[str, list[str]] = {pid: [] for pid in package.primitives}
    for pid, deps in graph.items():
        for dep in deps:
            reverse[dep].append(pid)

    tainted = set(cyclic)
    queue = list(cyclic)
    while queue:
        bad = queue.pop()
        for parent in reverse.get(bad, []):
            if parent not in tainted:
                tainted.add(parent)
                queue.append(parent)

    return tainted, sccs


def ops_upper_bound(
    ops: list[dict[str, Any]],
    resolve_ref: Callable[[str], Bound],
) -> Bound:
    total = Bound()

    for op in ops:
        kind = op["op"]
        if kind == "block":
            w, h = op["w"], op["h"]
            area = w * h
            op_bound = Bound(
                # One dispatch + one unit per painted cell.
                work=1 + area,
                cell_writes=area,
                ref_depth=0,
                bbox=BBox(op["x"], op["y"], op["x"] + w, op["y"] + h),
            )
        elif kind == "ref":
            callee = resolve_ref(op["ref"])
            if not callee.finite:
                op_bound = Bound.infinite()
            else:
                bbox = callee.bbox.translated(op["x"], op["y"]) if callee.bbox else None
                op_bound = Bound(
                    # The ref dispatch itself is also charged.
                    work=1 + callee.work,
                    cell_writes=callee.cell_writes,
                    ref_depth=1 + callee.ref_depth,
                    bbox=bbox,
                )
        else:  # branch
            alternative_bound = Bound()
            for case in op["cases"]:
                case_bound = ops_upper_bound(case["ops"], resolve_ref)
                alternative_bound = alternative_bound.branch_max_with(case_bound)
                if not alternative_bound.finite:
                    break
            if not alternative_bound.finite:
                op_bound = Bound.infinite()
            else:
                # One branch dispatch plus the worst selected alternative.
                op_bound = Bound(
                    work=1 + alternative_bound.work,
                    cell_writes=alternative_bound.cell_writes,
                    ref_depth=alternative_bound.ref_depth,
                    bbox=alternative_bound.bbox,
                    finite=True,
                )

        total = total.sequence_with(op_bound)
        if not total.finite:
            return Bound.infinite()

    return total


@dataclass
class GlyphAdmission:
    char: str
    root: str
    advance: int
    admitted: bool
    reasons: list[str]
    bound: Bound
    dependencies: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "char": self.char,
            "root": self.root,
            "advance": self.advance,
            "admitted": self.admitted,
            "reasons": self.reasons,
            "bound": self.bound.to_dict(),
            "dependencies": self.dependencies,
        }


@dataclass
class AdmissionReport:
    admission_id: str
    package_id: str
    content_hash: str
    policy: dict[str, Any]
    glyphs: dict[str, GlyphAdmission] = field(default_factory=dict)
    classic_glyphs: list[str] = field(default_factory=list)
    rejected: dict[str, list[str]] = field(default_factory=dict)
    cyclic_primitives: list[str] = field(default_factory=list)
    sccs: list[list[str]] = field(default_factory=list)
    fallback_char: str | None = None
    fallback_admitted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "admission_id": self.admission_id,
            "package_id": self.package_id,
            "content_hash": self.content_hash,
            "policy": self.policy,
            "classic_glyphs": self.classic_glyphs,
            "rejected": self.rejected,
            "cyclic_primitives": self.cyclic_primitives,
            "sccs": self.sccs,
            "fallback_char": self.fallback_char,
            "fallback_admitted": self.fallback_admitted,
            "glyphs": {c: g.to_dict() for c, g in self.glyphs.items()},
        }


def _closure(root: str, graph: dict[str, list[str]]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    stack = [root]
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        out.append(node)
        stack.extend(graph.get(node, ()))
    return sorted(out)


def analyze_package(
    package: Package,
    policy: ClassicPolicy | None = None,
    admission_id: str | None = None,
) -> AdmissionReport:
    policy = policy or ClassicPolicy()
    tainted, sccs = cyclic_and_tainted(package)
    graph = {pid: direct_dependencies(ops) for pid, ops in package.primitives.items()}
    memo: dict[str, Bound] = {}

    def resolve(pid: str) -> Bound:
        if pid in tainted:
            return Bound.infinite()
        if pid in memo:
            return memo[pid]
        # A non-tainted graph is a DAG.  Memoization also keeps shared
        # primitives accounted at every reference occurrence.
        bound = ops_upper_bound(package.primitives[pid], resolve)
        memo[pid] = bound
        return bound

    # Prime every primitive so the report can expose bounds if desired.
    for pid in package.primitives:
        resolve(pid)

    from .canonical import canonical_hash

    aid = admission_id or "adm_" + canonical_hash(
        {"hash": package.content_hash, "policy": policy.__dict__}
    )[:20]
    report = AdmissionReport(
        admission_id=aid,
        package_id=package.package_id,
        content_hash=package.content_hash,
        policy=dict(policy.__dict__),
        cyclic_primitives=sorted(tainted),
        sccs=[c for c in sccs if len(c) > 1 or (c and c[0] in graph and c[0] in graph[c[0]])],
        fallback_char=package.fallback_char,
    )

    for char, glyph in sorted(package.glyphs.items()):
        bound = resolve(glyph.root)
        reasons: list[str] = []
        if not bound.finite:
            reasons.append("cyclic_or_recursive_dependency")
        else:
            if bound.work > policy.max_work:
                reasons.append(f"static_work_bound_{bound.work}_gt_{policy.max_work}")
            if bound.cell_writes > policy.max_cell_writes:
                reasons.append(
                    f"static_cell_bound_{bound.cell_writes}_gt_{policy.max_cell_writes}"
                )
            if glyph.advance > 4:
                reasons.append(f"advance_{glyph.advance}_gt_4")
            if bound.bbox is not None:
                bb = bound.bbox
                if policy.require_non_negative_origin and (bb.min_x < 0 or bb.min_y < 0):
                    reasons.append("static_bbox_has_negative_origin")
                if bb.width > policy.max_bbox_width:
                    reasons.append(f"static_width_{bb.width}_gt_{policy.max_bbox_width}")
                if bb.height > policy.max_bbox_height:
                    reasons.append(f"static_height_{bb.height}_gt_{policy.max_bbox_height}")

        admitted = not reasons
        admission = GlyphAdmission(
            char=char,
            root=glyph.root,
            advance=glyph.advance,
            admitted=admitted,
            reasons=reasons,
            bound=bound,
            dependencies=_closure(glyph.root, graph),
        )
        report.glyphs[char] = admission
        if admitted:
            report.classic_glyphs.append(char)
        else:
            report.rejected[char] = reasons

    if package.fallback_char is not None:
        fb = report.glyphs.get(package.fallback_char)
        report.fallback_admitted = bool(fb and fb.admitted)

    return report
