"""Entry-route planning for the reading room.

Routes are built only from material that was actually delivered:

* ``letter_text``   - the reliable, longer directions in the accompanying letter
* ``letter_map``    - the accompanying map's textual description (may carry the
                      organiser's step-free / accessible detour)
* ``common_map``    - the shared issue illustration's description (may carry
                      short passages that exist nowhere else)

Nothing is invented.  A route can be signed out only if the passages it uses
appear in one of the delivered descriptions.

Two access modes are supported:

* ``standard``   - any passage, including stair-only shortcuts
* ``step_free``  - every passage must be walkable/rollable; stair-only
                   passages are removed entirely, so a wheelchair reader is
                   never guided up stairs even if that is the shortest way.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field

from .wire import Edge, RouteDescription

STANDARD = "standard"
STEP_FREE = "step_free"
MODES = (STANDARD, STEP_FREE)

LETTER_TEXT = "letter_text"
LETTER_MAP = "letter_map"
COMMON_MAP = "common_map"

SOURCE_LABELS = {
    LETTER_TEXT: "随信文字方向",
    LETTER_MAP: "随信地图说明",
    COMMON_MAP: "公共图说明",
}


class RouteError(ValueError):
    """Raised when no evidenced route exists for the requested mode."""


@dataclass
class RoutedEdge:
    edge: Edge
    source: str

    @property
    def label(self) -> str:
        return SOURCE_LABELS.get(self.source, self.source)


@dataclass
class Route:
    start: str
    goal: str
    mode: str
    steps: list[RoutedEdge] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)

    @property
    def total_weight(self) -> float:
        return sum(s.edge.weight for s in self.steps)

    @property
    def has_stairs(self) -> bool:
        return any(s.edge.is_stairs for s in self.steps)

    @property
    def sources(self) -> list[str]:
        seen = []
        for step in self.steps:
            if step.source not in seen:
                seen.append(step.source)
        return seen

    def node_label(self, node: str) -> str:
        return self.labels.get(node, node)

    def describe(self) -> str:
        """Render the step-by-step entry guidance."""
        lines = []
        lines.append(
            f"入场指引（{self.start} → {self.goal}，"
            f"{'无障碍/轮椅' if self.mode == STEP_FREE else '通用'}路线）"
        )
        lines.append(f"起点：{self.node_label(self.start)}")
        current = self.start
        for i, step in enumerate(self.steps, 1):
            e = step.edge
            arrow = "→" if e.one_way else "↔"
            kind = "楼梯" if e.is_stairs else {
                "corridor": "走廊",
                "ramp": "坡道",
                "lift": "电梯",
                "level": "平层",
            }.get(e.access, e.access)
            note = f"（{e.note}）" if e.note else ""
            lines.append(
                f"  {i}. {self.node_label(e.u)} {arrow} {self.node_label(e.v)} "
                f"[{kind}] 依据：{step.label}{note}"
            )
            current = e.v
        lines.append(f"终点：{self.node_label(self.goal)}")
        if self.has_stairs:
            lines.append("注意：本路线含楼梯。")
        else:
            lines.append("本路线全程无楼梯，适合轮椅通行。")
        return "\n".join(lines)


def build_adjacency(
    descriptions: list[tuple[str, RouteDescription]],
    mode: str = STANDARD,
) -> dict[str, list[RoutedEdge]]:
    """Turn delivered descriptions into a directed adjacency map.

    Each description contributes its edges under its own source tag.  In
    ``STEP_FREE`` mode stair-only passages are dropped before routing so they
    can never appear in the result.
    """
    if mode not in MODES:
        raise RouteError(f"unknown mode {mode!r}")
    adj: dict[str, list[RoutedEdge]] = {}

    def add(source, edge: Edge):
        if mode == STEP_FREE and edge.is_stairs:
            return
        adj.setdefault(edge.u, []).append(RoutedEdge(edge, source))
        if not edge.one_way:
            reverse = Edge(
                u=edge.v,
                v=edge.u,
                access=edge.access,
                weight=edge.weight,
                one_way=False,
                note=edge.note,
            )
            adj.setdefault(reverse.u, []).append(RoutedEdge(reverse, source))

    for source, desc in descriptions:
        if desc is None:
            continue
        for edge in desc.edges:
            add(source, edge)
    return adj


def plan_route(
    descriptions: list[tuple[str, RouteDescription]],
    start: str,
    goal: str,
    mode: str = STANDARD,
    labels: dict[str, str] | None = None,
    allowed_sources: set[str] | None = None,
    priorities: dict[str, int] | None = None,
) -> Route:
    """Dijkstra over the delivered passages.

    ``allowed_sources`` optionally restricts routing to some provenance tags,
    e.g. ``{LETTER_TEXT}`` to prove the reliable accompanying-letter route is
    independently usable.

    ``priorities`` maps provenance tags to a preference rank (lower wins).
    Among routes of equal cost, the one drawing on higher-priority documents
    is chosen -- so the accompanying map's description shadows a generic
    shared illustration describing the same passage, without ever making a
    route unreachable.
    """
    if allowed_sources is not None:
        descriptions = [(s, d) for (s, d) in descriptions if s in allowed_sources]
    adj = build_adjacency(descriptions, mode)
    labels = labels or {}
    priorities = priorities or {}

    if start == goal:
        return Route(start, goal, mode, [], labels)
    if start not in adj:
        raise RouteError(
            f"起点 {labels.get(start, start)!r} 没有任何可依据的通道"
        )

    # Dijkstra with lexicographic key (cost, priority_penalty).
    counter = 0
    heap: list[tuple[float, int, int, str, list[RoutedEdge]]] = [
        (0.0, 0, counter, start, [])
    ]
    best: dict[str, tuple[float, int]] = {start: (0.0, 0)}
    while heap:
        cost, penalty, _, node, path = heapq.heappop(heap)
        if node == goal:
            return Route(start, goal, mode, path, labels)
        if (cost, penalty) > best.get(node, (float("inf"), float("inf"))):
            continue
        for routed in adj.get(node, []):
            nxt = routed.edge.v
            new_cost = cost + routed.edge.weight
            new_penalty = penalty + priorities.get(routed.source, 0)
            key = (new_cost, new_penalty)
            if key < best.get(nxt, (float("inf"), float("inf"))):
                best[nxt] = key
                counter += 1
                heapq.heappush(heap, (new_cost, new_penalty, counter, nxt, path + [routed]))

    mode_cn = "无障碍/轮椅" if mode == STEP_FREE else "通用"
    hint = ""
    if mode == STEP_FREE:
        hint = "（台阶通道已被剔除；若需到达请向馆员索取活动方无障碍绕行说明）"
    raise RouteError(
        f"在{mode_cn}模式下找不到从 {labels.get(start, start)!r} 到 "
        f"{labels.get(goal, goal)!r} 的有依据通道{hint}"
    )
