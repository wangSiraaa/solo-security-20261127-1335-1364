"""棋盘网格图与成圈检测。

节点是带端口的格点 (row, col, side)，side 为该格朝向外侧的一边。
两片相邻时，格间边把 A 的东侧端口与 B 的西侧端口焊在一起；片内边
由 Tile.pairs() 给出。所有节点度数不超过 2，连通分量要么是开路
（路径），要么是圈。
"""

from __future__ import annotations

from dataclasses import dataclass
from .tile import Tile, tile_from_dict

# 方向 (dr, dc, 本格侧, 邻格侧)：北、东、南、西
DIRS = ((-1, 0, 0, 2), (0, 1, 1, 3), (1, 0, 2, 0), (0, -1, 3, 1))


def key(r: int, c: int) -> str:
    return f"{r},{c}"


def from_key(k: str) -> tuple[int, int]:
    r, c = k.split(",")
    return int(r), int(c)


@dataclass(frozen=True)
class Node:
    r: int
    c: int
    side: int

    def nid(self) -> str:
        return f"{self.r},{self.c}/{self.side}"


@dataclass
class Cycle:
    loop_id: str          # 边集合的稳定指纹（换片前后可据此认亲）
    edges: list[str]      # 排序后的节点对
    cells: list[str]      # 圈经过的格


class Board:
    def __init__(self, size: int, placements: dict[str, dict] | None = None):
        self.size = size
        # placements[cell] = {"tile": {...}, "owner": int, "sealed": bool,
        #                     "commitment": str|None, "turn": int}
        self.placements: dict[str, dict] = placements or {}

    # ---- 落片 ----
    def place(self, r: int, c: int, tile: Tile, owner: int, sealed: bool,
              commitment: str | None, turn: int) -> None:
        if not (0 <= r < self.size and 0 <= c < self.size):
            raise ValueError("格点超出棋盘")
        ck = key(r, c)
        if ck in self.placements:
            raise ValueError("该格已有路径片（换片请用 replace）")
        self.placements[ck] = {
            "tile": tile.__dict__.copy() if sealed else None,
            "tile_open": None if sealed else tile.__dict__.copy(),
            "owner": owner,
            "sealed": sealed,
            "commitment": commitment,
            "turn": turn,
        }

    def replace(self, r: int, c: int, tile: Tile, owner: int, sealed: bool,
                commitment: str | None, turn: int) -> dict:
        ck = key(r, c)
        if ck not in self.placements:
            raise ValueError("该格为空，不能替换（请用 place）")
        old = self.placements.pop(ck)
        self.place(r, c, tile, owner, sealed, commitment, turn)
        return old

    def tile_at(self, r: int, c: int) -> Tile | None:
        p = self.placements.get(key(r, c))
        if not p:
            return None
        d = p["tile"] if p["sealed"] else p["tile_open"]
        return tile_from_dict(d) if d else None

    # ---- 建图 ----
    def _adjacency(self) -> dict[str, set[str]]:
        adj: dict[str, set[str]] = {}

        def link(a: Node, b: Node) -> None:
            x, y = a.nid(), b.nid()
            adj.setdefault(x, set()).add(y)
            adj.setdefault(y, set()).add(x)

        for ck, p in self.placements.items():
            r, c = from_key(ck)
            tile = self.tile_at(r, c)
            if tile is None:
                continue
            # 片内边
            for s1, s2 in tile.pairs():
                link(Node(r, c, s1), Node(r, c, s2))
            # 格间边
            for dr, dc, side, nside in DIRS:
                nr, nc = r + dr, c + dc
                if key(nr, nc) in self.placements:
                    link(Node(r, c, side), Node(nr, nc, nside))
        return adj

    def find_cycles(self) -> list[Cycle]:
        adj = self._adjacency()
        seen: set[str] = set()
        out: list[Cycle] = []
        for start in adj:
            if start in seen:
                continue
            # 沿分量走（度数 <= 2）
            comp_nodes: list[str] = []
            frontier = [start]
            seen.add(start)
            while frontier:
                u = frontier.pop()
                comp_nodes.append(u)
                for v in adj[u]:
                    if v not in seen:
                        seen.add(v)
                        frontier.append(v)
            comp_edges: set[str] = set()
            is_cycle = True
            for u in comp_nodes:
                if len(adj[u]) != 2:
                    is_cycle = False
                    break
                for v in adj[u]:
                    comp_edges.add("|".join(sorted((u, v))))
            if is_cycle and comp_nodes:
                edges = sorted(comp_edges)
                cells = sorted({n.split("/")[0] for n in comp_nodes})
                lid = _loop_id(edges)
                out.append(Cycle(loop_id=lid, edges=edges, cells=cells))
        out.sort(key=lambda x: x.loop_id)
        return out


def _loop_id(edges: list[str]) -> str:
    import hashlib
    h = hashlib.sha256()
    for e in edges:
        h.update(e.encode())
        h.update(b";")
    return h.hexdigest()[:12]


def board_to_dict(b: Board) -> dict:
    return {"size": b.size, "placements": b.placements}


def board_from_dict(d: dict) -> Board:
    return Board(size=int(d["size"]), placements={k: dict(v) for k, v in d["placements"].items()})
