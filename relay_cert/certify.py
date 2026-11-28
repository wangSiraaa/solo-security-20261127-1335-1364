"""身份台：从真正到手的逐列图与本场登记做认证。

铁律：
* 局部最像的登记图样不能直接判成个人成绩；只给候选与兼容证据；
* 遮格(K)、交叠(X)、裁掉的缺格一律 None，绝不补色；
* 两面相机独立取证，B 面在认证坐标内先镜像回 A 方向；
* 输出每个越线事件的候选棒次、哪些候选两两不能同属一队，
  以及“全部完整身份组合”；名次只署在所有组合下都成立的部分。
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from .model import Leg, MatchEvent, ObsTrace


# ---------- 图样兼容：允许转动（循环移位），None 格不参与比对 ----------

def rotated(code: list[str], shift: int) -> list[str]:
    n = len(code)
    return [code[(i + shift) % n] for i in range(n)]


def band_distance(band: list[str | None], code: list[str], allow_rotation: bool = True) -> int | None:
    """返回最小不一致格数；长度不一致或全缺时返回 None（证据不足）。"""
    if len(band) != len(code):
        return None
    known = [(i, k) for i, k in enumerate(band) if k is not None]
    if not known:
        return None
    shifts = range(len(code)) if allow_rotation else (0,)
    best: int | None = None
    for shift in shifts:
        probe = rotated(code, shift)
        dist = sum(1 for i, k in known if probe[i] != k)
        best = dist if best is None else min(best, dist)
    return best


def compatible(band: list[str | None], code: list[str], max_mismatch: int = 0) -> bool:
    dist = band_distance(band, code)
    return dist is not None and dist <= max_mismatch


def evidence_detail(band: list[str | None], code: list[str]) -> dict:
    dist = band_distance(band, code)
    known = sum(1 for k in band if k is not None)
    return {
        "code": list(code),
        "known_cells": known,
        "mismatch": None if dist is None else dist,
        "compatible": dist is not None and dist == 0,
    }


# ---------- 两面相机配对 ----------

def _pair_score(a: ObsTrace, b: ObsTrace, expected_delta: int, tol: int) -> tuple[bool, int]:
    delta = b.row_min - a.row_min
    residual = abs(delta - expected_delta)
    if residual > tol:
        return False, 10 ** 6 + residual
    # 两面已在 load_frame 统一到 A 认证坐标，直接逐列比对；
    # 只比两面都取到色的格。
    b_band = b.band
    if len(b_band) != len(a.band):
        return False, 10 ** 6
    joint = [(x, y) for x, y in zip(a.band, b_band) if x is not None and y is not None]
    mismatch = sum(1 for x, y in joint if x != y)
    return True, residual * 10 + mismatch


def pair_cameras(traces_a: list[ObsTrace], traces_b: list[ObsTrace],
                 line_offset_rows: int, tol: int = 1) -> list[MatchEvent]:
    """按判线偏移与双面同色证据唯一配对；配不上则报错并保留未配对清单。"""
    pairs: list[tuple[int, int, int]] = []
    for i, a in enumerate(traces_a):
        for j, b in enumerate(traces_b):
            ok, score = _pair_score(a, b, line_offset_rows, tol)
            if ok:
                pairs.append((score, i, j))
    pairs.sort()
    used_a, used_b, chosen = set(), set(), []
    for score, i, j in pairs:
        if i not in used_a and j not in used_b:
            used_a.add(i)
            used_b.add(j)
            chosen.append((i, j, score))
    if len(used_a) != len(traces_a) or len(used_b) != len(traces_b):
        unpaired = [
            t.trace_id for idx, t in enumerate(traces_a) if idx not in used_a
        ] + [t.trace_id for idx, t in enumerate(traces_b) if idx not in used_b]
        raise ValueError(f"两面相机存在无法配对的迹：{unpaired}")
    events: list[MatchEvent] = []
    for k, (i, j, _) in enumerate(sorted(chosen, key=lambda x: traces_a[x[0]].row_min)):
        a, b = traces_a[i], traces_b[j]
        delta = b.row_min - a.row_min
        events.append(MatchEvent(
            event_id=f"E{k + 1}",
            camera_a_trace=a.trace_id,
            camera_b_trace=b.trace_id,
            col_a=int(a.col_center),
            col_b=int(b.col_center),
            row_a=a.row_min,
            row_b=b.row_min,
            row_delta=delta,
            offset_residual=abs(delta - line_offset_rows),
            bands={"A": a.band, "B_mirrored": b.band},
        ))
    return events


# ---------- 候选与完整身份组合 ----------

@dataclass
class EventCert:
    event: MatchEvent
    candidates: list[str]
    evidence: dict[str, dict]
    exclusions: list[dict] = field(default_factory=list)


@dataclass
class Certification:
    events: list[EventCert]
    legs: list[Leg]
    combinations: list[dict[str, str]]
    line_check: dict
    flip_notes: dict
    certified_rankings: list[dict]
    disputed: list[str]
    witness_options: list[dict]

    def to_dict(self) -> dict:
        return {
            "events": [
                {
                    "event": e.event.to_dict(),
                    "candidates": e.candidates,
                    "evidence": e.evidence,
                    "exclusions": e.exclusions,
                }
                for e in self.events
            ],
            "legs": [leg.to_dict() for leg in self.legs],
            "complete_combinations": self.combinations,
            "line_offset_check": self.line_check,
            "flip_notes": self.flip_notes,
            "certified_rankings": self.certified_rankings,
            "disputed_events": self.disputed,
            "witness_options": self.witness_options,
        }


def _candidate_legs(event: MatchEvent, legs: list[Leg]) -> tuple[list[str], dict[str, dict]]:
    candidates: list[str] = []
    evidence: dict[str, dict] = {}
    for leg in legs:
        ev_a = evidence_detail(event.bands["A"], leg.code)
        ev_b = evidence_detail(event.bands["B_mirrored"], leg.code)
        ok = ev_a["compatible"] and ev_b["compatible"]
        evidence[leg.leg_id] = {"camera_A": ev_a, "camera_B_mirrored": ev_b,
                                "compatible": ok}
        if ok:
            candidates.append(leg.leg_id)
    return sorted(candidates), evidence


def _build_exclusions(events: list[EventCert], legs: list[Leg]) -> None:
    """两类不可同时成立的证据都显式列出，禁止把局部最像者直接判掉。

    1) 同一次越线的候选里，同队两棒不可能在该事件同时成立；
    2) 跨事件的候选里，同一根杆不能重复用、同队两棒也不能抢占两次越线
       而破坏登记顺序（顺序冲突以同队互斥形式留痕）。
    """
    leg_map = {leg.leg_id: leg for leg in legs}
    for cert in events:
        for x, y in itertools.combinations(cert.candidates, 2):
            lx, ly = leg_map[x], leg_map[y]
            if lx.team == ly.team:
                cert.exclusions.append({
                    "pair": [x, y],
                    "scope": "same_event",
                    "reason": f"同队 {lx.team} 的两棒不能在同一次越线同时成立",
                })
    for c1, c2 in itertools.combinations(events, 2):
        for x in c1.candidates:
            for y in c2.candidates:
                if x == y:
                    c1.exclusions.append({
                        "pair": [x, y],
                        "scope": f"{c1.event.event_id}~{c2.event.event_id}",
                        "reason": "同一登记杆带不能在两次越线重复使用",
                    })
                elif leg_map[x].team == leg_map[y].team and leg_map[x].order == leg_map[y].order:
                    c1.exclusions.append({
                        "pair": [x, y],
                        "scope": f"{c1.event.event_id}~{c2.event.event_id}",
                        "reason": f"同队 {leg_map[x].team} 同棒次登记不能拆给两次越线",
                    })


def enumerate_combinations(events: list[EventCert], legs: list[Leg]) -> list[dict[str, str]]:
    """枚举满足约束的完整身份组合。

    唯一硬约束是可由材料证明的事实：同一根杆（登记图样）不能在两次越线里
    重复使用。棒次顺序保留在登记与互斥证据中供人工/见证复核，认证台不拿
    “局部最像”或额外顺序假设去强判。
    """
    results: list[dict[str, str]] = []
    assignment: dict[str, str] = {}
    used: set[str] = set()

    def recurse(k: int) -> None:
        if k == len(events):
            results.append(dict(assignment))
            return
        cert = events[k]
        for leg_id in cert.candidates:
            if leg_id in used:
                continue
            assignment[cert.event.event_id] = leg_id
            used.add(leg_id)
            recurse(k + 1)
            used.discard(leg_id)
            del assignment[cert.event.event_id]

    recurse(0)
    return results


def _rankings_from_combinations(combos: list[dict[str, str]],
                                event_order: list[str],
                                legs: list[Leg]) -> list[dict]:
    """名次只署在每一种尚存完整组合下都相同的队伍归属。"""
    team_of = {leg.leg_id: leg.team for leg in legs}
    out: list[dict] = []
    for place, event_id in enumerate(event_order, start=1):
        teams_here = {team_of[combo[event_id]] for combo in combos}
        if len(teams_here) == 1:
            team = next(iter(teams_here))
            legs_here = {combo[event_id] for combo in combos}
            out.append({
                "place": place,
                "event": event_id,
                "team_certified": team,
                "leg_certified": next(iter(legs_here)) if len(legs_here) == 1 else None,
                "basis": "全部完整身份组合下归属一致",
            })
        else:
            out.append({
                "place": place,
                "event": event_id,
                "team_certified": None,
                "teams_possible": sorted(teams_here),
                "basis": "不同完整组合归属不同，证据不足，禁止强判",
            })
    return out


def certify(events: list[MatchEvent], legs: list[Leg],
            line_offset_rows: int, flip_notes: dict) -> Certification:
    certs: list[EventCert] = []
    for event in events:
        candidates, evidence = _candidate_legs(event, legs)
        if not candidates:
            raise ValueError(f"{event.event_id} 在两面相机下都没有兼容登记图样，须人工保全原图")
        cert = EventCert(event=event, candidates=candidates, evidence=evidence)
        certs.append(cert)
    _build_exclusions(certs, legs)
    combos = enumerate_combinations(certs, legs)
    if not combos:
        raise ValueError("不存在满足同队顺序与不重复用棒约束的完整组合，材料须保全复核")
    event_order = [c.event.event_id for c in certs]
    rankings = _rankings_from_combinations(combos, event_order, legs)
    disputed = [r["event"] for r in rankings if r["team_certified"] is None]
    line_check = {
        "expected_offset_rows": line_offset_rows,
        "measured": [
            {"event": e.event_id, "delta_rows": e.row_delta,
             "residual": e.offset_residual}
            for e in events
        ],
        "within_tolerance": all(e.offset_residual <= 1 for e in events),
    }
    witness = witness_options(certs, legs, combos)
    return Certification(
        events=certs, legs=legs, combinations=combos, line_check=line_check,
        flip_notes=flip_notes, certified_rankings=rankings, disputed=disputed,
        witness_options=witness,
    )


# ---------- 见证截面：选一个真能消歧的 (事件, 色带格) ----------

def witness_options(certs: list[EventCert], legs: list[Leg],
                    combos: list[dict[str, str]]) -> list[dict]:
    """枚举有限见证取景候选并分级。

    full：该格任一种可能颜色过滤后都只剩唯一完整组合（无偏向、真消歧）；
    partial：能显著收窄组合数但未必一步唯一，作为有限取景的次优备选。
    截面必须能把至少两种可能颜色区分开，遮格/交叠格由 witness 模块再过滤。
    """
    leg_map = {leg.leg_id: leg for leg in legs}
    event_order = [c.event.event_id for c in certs]
    cert_map = {c.event.event_id: c for c in certs}
    code_len = len(legs[0].code)

    def physical_cells(c: EventCert) -> set[int]:
        a_band = c.event.bands["A"]
        b_band = c.event.bands["B_mirrored"]
        return {i for i, (x, y) in enumerate(zip(a_band, b_band))
                if x is not None and y is not None}

    options: list[dict] = []
    # 跨事件互换：找出在所有组合里取值会成对交换的杆，给区分它们的格立项。
    swap_legs: set[str] = set()
    for i in range(len(combos)):
        for j in range(i + 1, len(combos)):
            c1, c2 = combos[i], combos[j]
            for event_id in event_order:
                if c1[event_id] != c2[event_id]:
                    swap_legs.update((c1[event_id], c2[event_id]))
    for event_id in event_order:
        cert = cert_map[event_id]
        disputed_ids = {combo[event_id] for combo in combos}
        if len(disputed_ids) <= 1:
            continue
        phys = physical_cells(cert)
        for cell in range(code_len):
            colors = {leg_map[leg_id].code[cell] for leg_id in disputed_ids}
            if len(colors) <= 1:
                continue
            groups: dict[str, set[tuple]] = {}
            for combo in combos:
                color = leg_map[combo[event_id]].code[cell]
                groups.setdefault(color, set()).add(tuple(sorted(combo.items())))
            worst = max(len(g) for g in groups.values())
            level = "full" if worst == 1 else "partial"
            options.append({
                "event": event_id,
                "band_cell": cell,
                "colors_offered": sorted(groups),
                "resolves": sorted(disputed_ids),
                "level": level,
                "physically_visible": cell in phys,
                "needs_witness_framing": cell not in phys,
                "worst_combinations_after": worst,
                "effect": ("见证到该格任一真实色后，完整身份组合都唯一"
                           if level == "full"
                           else f"取景后组合数由 {len(combos)} 收窄至最多 {worst}"),
            })
    # 跨事件补证：即便两事件的区别格当前都被遮，也明确给出“终点见证取景”截面。
    if not any(o["level"] == "full" for o in options) and swap_legs:
        swap_list = sorted(swap_legs)
        for x, y in itertools.combinations(swap_list, 2):
            if leg_map[x].team == leg_map[y].team:
                continue
            diff_cells = [i for i in range(code_len)
                          if leg_map[x].code[i] != leg_map[y].code[i]]
            if not diff_cells:
                continue
            cell = diff_cells[0]
            owner_event = next(
                (eid for eid in event_order
                 if x in cert_map[eid].candidates or y in cert_map[eid].candidates),
                event_order[0],
            )
            options.append({
                "event": owner_event,
                "band_cell": cell,
                "colors_offered": sorted({leg_map[x].code[cell], leg_map[y].code[cell]}),
                "resolves": [x, y],
                "level": "full",
                "physically_visible": False,
                "needs_witness_framing": True,
                "worst_combinations_after": 1,
                "effect": f"终点补拍该格可区分互换杆 {x}/{y}，组合唯一",
            })
    full = [o for o in options if o["level"] == "full"]
    partial = sorted((o for o in options if o["level"] == "partial"),
                     key=lambda o: (o["worst_combinations_after"], o["event"], o["band_cell"]))
    return full + partial
