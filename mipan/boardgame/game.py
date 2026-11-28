"""对局状态机：落片、换片分类、成圈结算、揭片与退场打包。

三种成圈事件的区分（题面要求）：
- first_close 本回合首次闭合：落片/换片后出现的新圈，且没有本手拆开的旧圈
- reconnect   原圈被拆开重新接上：本手先拆断过旧圈，新圈与被拆旧圈共格
- cosmetic    仅改外观：换片前后圈的边集合完全不变（含外观相同配对不同却
               恰好保持同构连通的情形），记 0 分

可见性：
- public 全由公开片组成，任何人可重算，当场结算
- self   只含自己的封存片，本人持完整备件可重算，当场结算（对手只见承诺）
- dark   含对手封存片，挂起为条件条目，只给承诺与解封条件，封盘揭密后再结
"""

from __future__ import annotations

import os
import json
from dataclasses import dataclass, field, asdict

from .tile import Tile, tile_to_dict, tile_from_dict, random_tile
from .board import Board, Cycle, board_to_dict, board_from_dict, key, from_key
from .commit import digest, sha256_bytes

BASE_PER_CELL = 10
PLAYERS = (0, 1)


@dataclass
class Objective:
    id: str
    target: str          # 格点 "r,c"
    reward: int
    claimed_by: int | None = None
    claimed_turn: int | None = None


@dataclass
class GameState:
    name: str
    board_size: int
    players: list[str]
    hands: dict[int, list[dict]]
    board: Board
    turn: int = 0
    next_player: int = 0
    objectives: list[Objective] = field(default_factory=list)
    turn_log: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    score: dict[int, int] = field(default_factory=lambda: {0: 0, 1: 0})
    conditional: list[dict] = field(default_factory=list)   # 挂起的暗圈
    violations: list[dict] = field(default_factory=list)
    broken_loops: dict[str, dict] = field(default_factory=dict)  # 拆断待认亲的旧圈
    status: str = "ongoing"

    # ---------- 存档 ----------
    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "board_size": self.board_size,
            "players": self.players,
            "hands": {str(k): v for k, v in self.hands.items()},
            "board": board_to_dict(self.board),
            "turn": self.turn,
            "next_player": self.next_player,
            "objectives": [asdict(o) for o in self.objectives],
            "turn_log": self.turn_log,
            "events": self.events,
            "score": {str(k): v for k, v in self.score.items()},
            "conditional": self.conditional,
            "violations": self.violations,
            "broken_loops": self.broken_loops,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "GameState":
        objs = [Objective(**o) for o in d["objectives"]]
        return cls(
            name=d["name"],
            board_size=d["board_size"],
            players=list(d["players"]),
            hands={int(k): list(v) for k, v in d["hands"].items()},
            board=board_from_dict(d["board"]),
            turn=d["turn"],
            next_player=d["next_player"],
            objectives=objs,
            turn_log=d["turn_log"],
            events=d["events"],
            score={int(k): v for k, v in d["score"].items()},
            conditional=d["conditional"],
            violations=d["violations"],
            broken_loops=d.get("broken_loops", {}),
            status=d["status"],
        )

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path: str) -> "GameState":
        with open(path, encoding="utf-8") as f:
            return cls.from_dict(json.load(f))


def new_game(name: str, size: int, hand_size: int, seed: int | None = None,
             objectives: list[Objective] | None = None) -> GameState:
    import random
    rng = random.Random(seed)
    hands = {pid: [tile_to_dict(random_tile(rng)) for _ in range(hand_size)] for pid in PLAYERS}
    gs = GameState(
        name=name, board_size=size,
        players=["甲", "乙"], hands=hands,
        board=Board(size),
        objectives=objectives or [],
    )
    return gs


# ---------- 可见性 ----------

def _sealed_owners(gs: GameState, cells: list[str]) -> set[int]:
    owners = set()
    for ck in cells:
        p = gs.board.placements.get(ck)
        if p and p["sealed"]:
            owners.add(p["owner"])
    return owners


def _visibility(gs: GameState, cyc: Cycle, actor: int) -> str:
    owners = _sealed_owners(gs, cyc.cells)
    if not owners:
        return "public"
    if actor in owners:
        # 同时含对手封存片时，本人也无法重算，只能挂起
        return "dark" if owners - {actor} else "self"
    return "dark"


def _boundary_junction(gs: GameState, cyc: Cycle) -> list[str]:
    """公开交界处：圈边里一端在封存格、一端在公开格（或棋盘外不可见段）的端口。"""
    out = set()
    sealed_cells = {ck for ck in cyc.cells if gs.board.placements[ck]["sealed"]}
    for e in cyc.edges:
        a, b = e.split("|")
        ca, cb = a.split("/")[0], b.split("/")[0]
        if (ca in sealed_cells) != (cb in sealed_cells):
            out.add(a if ca in sealed_cells else b)
    return sorted(out)


def _secret_payload(gs: GameState, cyc: Cycle, score_value: int) -> dict:
    tiles = {}
    for ck in cyc.cells:
        p = gs.board.placements[ck]
        if p["sealed"]:
            tiles[ck] = p["tile"]
    return {
        "loop_id": cyc.loop_id,
        "edges": cyc.edges,
        "cells": cyc.cells,
        "score": score_value,
        "sealed_tiles": tiles,
    }


# ---------- 计分与目标 ----------

def _claim_objective(gs: GameState, cells: list[str], actor: int) -> Objective | None:
    for obj in gs.objectives:
        if obj.claimed_by is None and obj.target in cells:
            obj.claimed_by = actor
            obj.claimed_turn = gs.turn
            return obj
    return None


def _loop_score(gs: GameState, cells: list[str], actor: int) -> tuple[int, Objective | None]:
    obj = _claim_objective(gs, cells, actor)
    val = BASE_PER_CELL * len(cells) + (obj.reward if obj else 0)
    return val, obj


# ---------- 落片 / 换片 ----------

def _find_hand_tile(gs: GameState, actor: int, tile: Tile) -> int:
    for i, d in enumerate(gs.hands[actor]):
        if d == tile_to_dict(tile):
            return i
    raise ValueError("备件里没有这一片（检查类型/旋转/外观）")


def _classify(gs: GameState, before: list[Cycle], after: list[Cycle],
              cell: str, is_replace: bool) -> list[tuple[Cycle, str, str | None]]:
    """返回 [(新圈, 事件类型, 亲缘旧圈id)]，仅含本手相关的圈。

    被拆旧圈登记在 gs.broken_loops（跨回合保留，直到亲缘被认领），
    因此"先拆断、若干手后再接回"也能判成 reconnect，而不是 first_close。
    """
    before_ids = {c.loop_id: c for c in before}
    after_ids = {c.loop_id: c for c in after}
    # 本手拆断的旧圈（换片格上的旧圈在换片后消失），登记待认领
    for c in before:
        if c.loop_id not in after_ids and cell in c.cells:
            gs.broken_loops[c.loop_id] = {"cells": c.cells, "broken_turn": gs.turn}
    result = []
    for cyc in after:
        if cyc.loop_id in before_ids:
            if is_replace and cell in cyc.cells:
                result.append((cyc, "cosmetic", None))
            continue
        lineage = next((lid for lid, info in gs.broken_loops.items()
                        if set(info["cells"]) & set(cyc.cells)), None)
        if lineage:
            gs.broken_loops.pop(lineage, None)  # 亲缘已认领
            result.append((cyc, "reconnect", lineage))
        else:
            result.append((cyc, "first_close", None))
    return result


def place_or_replace(gs: GameState, actor: int, r: int, c: int, tile: Tile,
                     sealed: bool) -> dict:
    if gs.status != "ongoing":
        raise ValueError("对局已封盘")
    if actor != gs.next_player:
        raise ValueError(f"轮到 {gs.players[gs.next_player]}，不是 {gs.players[actor]}")
    hand_idx = _find_hand_tile(gs, actor, tile)
    ck = key(r, c)
    is_replace = ck in gs.board.placements
    before = gs.board.find_cycles()

    commitment = None
    if sealed:
        commitment = digest(tile_to_dict(tile))

    replaced_brief = None
    if is_replace:
        old = gs.board.replace(r, c, tile, actor, sealed, commitment, gs.turn)
        replaced_brief = {
            "cell": ck, "owner": old["owner"], "sealed": old["sealed"],
            "appearance": (old["tile"] or old["tile_open"])["appearance"],
            "commitment": old["commitment"],
        }
    else:
        gs.board.place(r, c, tile, actor, sealed, commitment, gs.turn)

    gs.hands[actor].pop(hand_idx)
    after = gs.board.find_cycles()
    classified = _classify(gs, before, after, ck, is_replace)

    turn_events = []
    for cyc, kind, lineage in classified:
        vis = _visibility(gs, cyc, actor)
        obj = None
        score_value = 0
        if kind == "cosmetic":
            ev = _make_event(gs, actor, ck, cyc, kind, vis, 0, None, None, lineage)
        else:
            score_value, obj = _loop_score(gs, cyc.cells, actor)
            payload = _secret_payload(gs, cyc, score_value)
            promise = digest(payload) if vis == "dark" else None
            ev = _make_event(gs, actor, ck, cyc, kind, vis, score_value, obj, promise, lineage)
            ev["junction"] = _boundary_junction(gs, cyc)
            ev["conditions"] = sorted(
                x for x in cyc.cells
                if gs.board.placements[x]["sealed"] and gs.board.placements[x]["owner"] != actor
            )
            if vis == "dark":
                ev["score"] = None  # 金额封进承诺，公开摘录不给数
                gs.conditional.append(
                    _conditional_entry(ev, payload["sealed_tiles"]))
            else:
                gs.score[actor] += score_value
        gs.events.append(ev)
        turn_events.append(ev)

    record = {
        "turn": gs.turn,
        "player": actor,
        "kind": "replace" if is_replace else "place",
        "cell": ck,
        "sealed": sealed,
        "appearance": tile.appearance,
        "tile": None if sealed else tile_to_dict(tile),
        "commitment": commitment,
        "replaced": replaced_brief,
        "events": [_public_event(e) for e in turn_events],
        "settled_delta": {str(actor): sum(
            e["score"] for e in turn_events if e["score"] is not None)},
    }
    gs.turn_log.append(record)
    gs.turn += 1
    gs.next_player = 1 - actor
    return record


def _make_event(gs, actor, cell, cyc: Cycle, kind, vis, score_value,
                obj: Objective | None, promise, lineage) -> dict:
    return {
        "turn": gs.turn,
        "actor": actor,
        "cell": cell,
        "loop_id": cyc.loop_id,
        "kind": kind,
        "visibility": vis,
        "edges": cyc.edges,
        "cells": cyc.cells,
        "score": score_value,
        "objective_id": obj.id if obj else None,
        "promise": promise,
        "lineage": lineage,
        "junction": [],
        "conditions": [],
    }


def _public_event(ev: dict) -> dict:
    """对手可见的事件摘录：暗圈不给金额，只给承诺与条件。"""
    out = dict(ev)
    if ev["visibility"] == "dark":
        out["score"] = None
    return out


def _conditional_entry(ev: dict, sealed_tiles: dict) -> dict:
    return {
        "loop_id": ev["loop_id"],
        "turn": ev["turn"],
        "actor": ev["actor"],
        "kind": ev["kind"],
        "cells": ev["cells"],
        "junction": ev["junction"],
        "conditions": ev["conditions"],
        "promise": ev["promise"],
        # 承诺载荷的组成部分：仅进本人的退场包，公开视图仍只见承诺
        "sealed_tiles": sealed_tiles,
        "status": "pending",
    }


# ---------- 揭片 ----------

def unseal(gs: GameState, actor: int, r: int, c: int) -> dict:
    if gs.status != "ongoing":
        raise ValueError("对局已封盘")
    ck = key(r, c)
    p = gs.board.placements.get(ck)
    if not p or not p["sealed"]:
        raise ValueError("该格不是封存片")
    if p["owner"] != actor:
        raise PermissionError("只能揭自己的封存片")
    from .commit import open_commitment
    tile = tile_from_dict(p["tile"])
    if not open_commitment(p["commitment"], tile_to_dict(tile)):
        v = {"turn": gs.turn, "cell": ck, "reason": "承诺与实物不符"}
        gs.violations.append(v)
        raise ValueError("承诺校验失败：封存片被篡改")
    p["sealed"] = False
    p["tile_open"] = p["tile"]

    settled_now = []
    for ent in gs.conditional:
        if ent["status"] != "pending" or ck not in ent["cells"]:
            continue
        # 重算该圈：边集合应不变，可见性可能升级
        cyc = next((x for x in gs.board.find_cycles() if x.loop_id == ent["loop_id"]), None)
        if cyc is None:
            ent["status"] = "void_loop_missing"
            gs.violations.append({"turn": gs.turn, "loop_id": ent["loop_id"],
                                  "reason": "揭片后圈不存在"})
            continue
        if _sealed_owners(gs, cyc.cells) - {ent["actor"]}:
            continue                        # 仍含对手封存片，保持 pending
        score_value, payload = _reverify(gs, ent, cyc)
        if score_value is None:
            ent["status"] = "violation"
            gs.violations.append({"turn": gs.turn, "loop_id": ent["loop_id"],
                                  "reason": "得分承诺重算不一致", "payload": payload})
            continue
        ent["status"] = "settled"
        ent["settled_turn"] = gs.turn
        ent["score"] = score_value
        gs.score[ent["actor"]] += score_value
        # 同步原始事件
        for ev in gs.events:
            if ev["loop_id"] == ent["loop_id"]:
                ev["visibility"] = "self" if _sealed_owners(gs, cyc.cells) else "public"
                ev["score"] = score_value
        settled_now.append({"loop_id": ent["loop_id"], "score": score_value})
    # 已结/违规条目移出挂起列表（结果已写进事件与比分），只留 pending
    gs.conditional = [e for e in gs.conditional if e["status"] == "pending"]

    record = {
        "turn": gs.turn, "player": actor, "kind": "unseal", "cell": ck,
        "tile": tile_to_dict(tile), "commitment": p["commitment"],
        "settled_now": settled_now,
    }
    gs.turn_log.append(record)
    gs.turn += 1
    gs.next_player = 1 - actor
    return record


def _reverify(gs: GameState, ent: dict, cyc: Cycle):
    """用当前棋盘重算承诺载荷，核对承诺，返回 (金额, 载荷)。

    承诺是在封存时刻做的，载荷里的 sealed_tiles 是当时的封存片实物；
    揭片后这些片已公开，需从原始事件里取回当时留存的片字典再算哈希。
    """
    base = BASE_PER_CELL * len(cyc.cells)
    obj_reward = 0
    for obj in gs.objectives:
        if obj.claimed_turn == ent["turn"] and obj.target in cyc.cells:
            obj_reward += obj.reward
    score_value = base + obj_reward
    sealed_tiles = ent.get("sealed_tiles", {})
    payload = {
        "loop_id": cyc.loop_id,
        "edges": cyc.edges,
        "cells": cyc.cells,
        "score": score_value,
        "sealed_tiles": sealed_tiles,
    }
    if digest(payload) != ent["promise"]:
        return None, payload
    return score_value, payload


def seal_game(gs: GameState) -> None:
    """封盘：所有封存片必须揭完，挂起圈须全部了结。"""
    if any(p["sealed"] for p in gs.board.placements.values()):
        raise ValueError("仍有封存片未揭")
    if gs.conditional:
        raise ValueError("仍有挂起暗圈未结算")
    gs.status = "sealed"


# ---------- 公开视图与退场打包 ----------

def public_view(gs: GameState) -> dict:
    """任一选手当场可见的信息：公开片、封存格外观+承诺、公开事件摘录、比分。"""
    cells = {}
    for ck, p in gs.board.placements.items():
        cells[ck] = {
            "owner": p["owner"], "sealed": p["sealed"],
            "appearance": (p["tile"] or p["tile_open"])["appearance"],
            "tile": None if p["sealed"] else p["tile_open"],
            "commitment": p["commitment"], "turn": p["turn"],
        }
    return {
        "name": gs.name, "status": gs.status, "turn": gs.turn,
        "players": gs.players, "board_size": gs.board_size,
        "cells": cells,
        "turn_log": [_sanitize_record(r) for r in gs.turn_log],
        "score": {str(k): v for k, v in gs.score.items()},
        "conditional": [{k: v for k, v in e.items() if k != "sealed_tiles"}
                        for e in gs.conditional],
        "objectives_claimed": [asdict(o) for o in gs.objectives if o.claimed_by is not None],
        "objectives_remaining_count": sum(1 for o in gs.objectives if o.claimed_by is None),
    }


def _sanitize_record(r: dict) -> dict:
    out = dict(r)
    if r.get("sealed") and r.get("kind") in ("place", "replace"):
        out["tile"] = None
    if r.get("kind") == "unseal":
        out["tile"] = None  # 他人视角的揭片记录只留承诺核对痕迹
    return out


def build_exit_package(gs: GameState, pid: int, out_dir: str) -> str:
    """退场人带走件（Mode A）。

    包含：规则、公开过程、本人封存片实物、本人可核的账本（含条件条目）、
    已领目标、封存边界见证、本人比分。明确不含：对手备件、本人未出备件
    （仅登记数量后撤回）、未领目标细节、未落片格信息。
    """
    os.makedirs(out_dir, exist_ok=True)
    own_sealed, own_open = [], []
    for ck, p in gs.board.placements.items():
        item = {"cell": ck, "tile": p["tile"] or p["tile_open"],
                "commitment": p["commitment"], "turn": p["turn"]}
        if p["owner"] == pid:
            (own_sealed if p["sealed"] else own_open).append(item)

    ledger = []
    for ev in gs.events:
        if ev["visibility"] == "dark" and ev["actor"] != pid:
            continue  # 别人的暗圈与本人无关
        ledger.append(_public_event(ev) if ev["visibility"] == "dark" else dict(ev))

    files = {
        "rules.json": {
            "mode": "A_public_commitment",
            "base_per_cell": BASE_PER_CELL,
            "visibility": {"public": "当场结算", "self": "本人可重算，当场结算",
                           "dark": "挂起，封盘揭密后凭承诺结算"},
            "event_kinds": ["first_close", "reconnect", "cosmetic"],
        },
        "public_process.json": public_view(gs),
        "own_placements.json": {"sealed": own_sealed, "open": own_open,
                                "withdrawn_spare_count": len(gs.hands[pid])},
        "ledger.json": ledger,
        "conditional.json": [dict(e) for e in gs.conditional if e["actor"] == pid],
        "objectives_claimed.json": [asdict(o) for o in gs.objectives
                                    if o.claimed_by is not None],
        "boundary_witness.json": _boundary_witness(gs, pid),
        "score.json": {"settled": {str(k): v for k, v in gs.score.items()},
                       "player": pid, "name": gs.players[pid]},
        "exclusions.json": {
            "opponent_spares": "不包含",
            "own_unplaced_tiles": "不包含，仅登记数量",
            "unclaimed_objectives": "不包含细节，公开视图仅有剩余计数",
            "empty_cells": "不包含",
        },
    }
    manifest = {"files": {}, "player": pid, "name": gs.players[pid],
                "match": gs.name}
    for fname, payload in files.items():
        fpath = os.path.join(out_dir, fname)
        blob = json.dumps(payload, ensure_ascii=False, indent=2,
                          sort_keys=True).encode("utf-8")
        with open(fpath, "wb") as f:
            f.write(blob)
        manifest["files"][fname] = sha256_bytes(blob)
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2, sort_keys=True)
    return out_dir


def _boundary_witness(gs: GameState, pid: int) -> list[dict]:
    """本人封存格与公开世界交界的端口见证（公开可观察，不含片内秘密）。"""
    out = []
    for ck, p in gs.board.placements.items():
        if p["owner"] == pid and p["sealed"]:
            r, c = from_key(ck)
            ports = []
            from .board import DIRS
            for dr, dc, side, nside in DIRS:
                nk = key(r + dr, c + dc)
                n = gs.board.placements.get(nk)
                ports.append({"side": side, "neighbor": nk if n else None,
                              "neighbor_sealed": bool(n and n["sealed"])})
            out.append({"cell": ck, "commitment": p["commitment"],
                        "appearance": p["tile"]["appearance"], "ports": ports})
    return out
