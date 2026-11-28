"""计分室：认证材料先到，罚秒与同成绩晋级名额到场才开封。

输入：
* 认证台的完整身份组合（计分室不自行认棒）；
* 两段接力罚秒（按棒次登记，只影响对应队伍）；
* 同成绩并列时的晋级名额 quota。
输出可复读下一轮名单：确定名次、并列、须加赛的两队都显式列出，
并对每条结论给出依赖的组合范围与确定性。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


def _team_finish_order(cert_dict: dict) -> dict[str, list[str]]:
    """每种完整组合下，按终点事件顺序得到的队伍过线序列。"""
    team_of = {leg["leg_id"]: leg["team"] for leg in cert_dict["legs"]}
    orders: dict[str, list[str]] = {}
    for idx, combo in enumerate(cert_dict["complete_combinations"]):
        seq = [team_of[combo[event_id]]
               for event_id, _ in sorted(combo.items(), key=lambda kv: kv[0])]
        orders[f"combo_{idx}"] = seq
    return orders


def effective_times(cert_dict: dict, order: list[str],
                    penalties: dict[str, float]) -> dict[str, float]:
    """罚秒只在每队最后一棒（终点事件）上累计，不改变中间棒归属。"""
    raw_rank = {team: idx for idx, team in enumerate(order)}
    return {team: raw_rank[team] + penalties.get(team, 0.0) for team in raw_rank}


@dataclass
class RosterEntry:
    kind: str          # qualified / tied / runoff / pending
    team: str | None
    detail: dict

    def to_dict(self) -> dict:
        return {"kind": self.kind, "team": self.team, **self.detail}


def _stable_id(payload: dict) -> str:
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()[:12]


def decide(cert_dict: dict, penalties: dict[str, float], quota: int,
           tie_tol: float = 1e-9) -> dict:
    orders = _team_finish_order(cert_dict)
    teams = sorted({leg["team"] for leg in cert_dict["legs"]})
    per_combo: dict[str, dict] = {}
    all_rank_pos: dict[str, set[int]] = {team: set() for team in teams}

    for name, order in orders.items():
        times = effective_times(cert_dict, order, penalties)
        ranked = sorted(times, key=lambda t: (times[t], t))
        for pos, team in enumerate(ranked, start=1):
            all_rank_pos[team].add(pos)
        per_combo[name] = {"finish_order": order, "effective_times": times,
                           "ranked": ranked}

    # 逐位置裁决：该位置在所有尚存组合下队伍相同才算确定名次。
    n_places = len(teams) * (len(next(iter(orders.values()))) // len(teams))
    place_teams: dict[int, set[str]] = {}
    for order in orders.values():
        for pos, team in enumerate(order, start=1):
            place_teams.setdefault(pos, set()).add(team)
    fixed: dict[int, str] = {
        pos: next(iter(ts)) for pos, ts in place_teams.items() if len(ts) == 1
    }
    all_rank_pos: dict[str, set[int]] = {team: set() for team in teams}
    for pos, ts in place_teams.items():
        for team in ts:
            all_rank_pos[team].add(pos)

    # 并列判据：每一种组合内，有效时间相等（同成绩）才是真并列；
    # 不能拿跨组合的最小边界时间硬凑。
    tie_pairs: set[tuple[str, str]] = set()
    for info in per_combo.values():
        times = info["effective_times"]
        vals = sorted(times.values())
        for t1, v1 in times.items():
            for t2, v2 in times.items():
                if t1 < t2 and abs(v1 - v2) <= tie_tol:
                    tie_pairs.add((t1, t2))
    tied_teams = {t for pair in tie_pairs for t in pair}

    entries: list[RosterEntry] = []
    qualified_teams: list[str] = []
    runoff_teams: set[str] = set()

    # 逐位置裁决；身份未定的位置先挂 pending：第一名确定不影响后续编排。
    # 接力名次只看每队最后一棒（终点事件），取每队在各组合中最后出现的位置。
    final_positions: dict[str, dict[str, int]] = {}
    for name, order in orders.items():
        last: dict[str, int] = {}
        for pos, team in enumerate(order, start=1):
            last[team] = pos
        final_positions[name] = last
    final_place_teams: dict[int, set[str]] = {}
    for last in final_positions.values():
        for team, pos in last.items():
            final_place_teams.setdefault(pos, set()).add(team)
    # 终点名次按“每队最终有效时间”排序后的位置处理。
    team_final_times: dict[str, dict[str, float]] = {}
    for name, info in per_combo.items():
        team_final_times[name] = {
            team: max(p for p, t in enumerate(info["finish_order"], start=1)
                      if t == team) + penalties.get(team, 0.0)
            for team in teams
        }
    ranked_by_combo = {
        name: sorted(times, key=lambda t: (times[t], t))
        for name, times in team_final_times.items()
    }
    rank_pos: dict[str, set[int]] = {team: set() for team in teams}
    for ranked in ranked_by_combo.values():
        for pos, team in enumerate(ranked, start=1):
            rank_pos[team].add(pos)

    for pos in range(1, len(teams) + 1):
        teams_here = {ranked[pos - 1] for ranked in ranked_by_combo.values()}
        if len(teams_here) == 1:
            team = next(iter(teams_here))
            times_here = {team_final_times[name][team] for name in team_final_times}
            if pos <= quota:
                if team in tied_teams:
                    entries.append(RosterEntry("tied", team, {
                        "place": pos, "reason": "罚秒后有效成绩相同",
                        "times_by_combo": sorted(times_here)}))
                else:
                    qualified_teams.append(team)
                    entries.append(RosterEntry("qualified", team, {
                        "place": pos, "certain": True,
                        "times_by_combo": sorted(times_here)}))
            else:
                entries.append(RosterEntry("eliminated", team, {
                    "place": pos, "certain": True}))
        else:
            possible = sorted(teams_here)
            # 待核位置：名次不能确定，任何可能落在该位的队都不能直接晋级，
            # 统一进加赛集合；第一名若已确定则其编排不受影响。
            for team in possible:
                runoff_teams.add(team)
            entries.append(RosterEntry("pending", None, {
                "place": pos, "teams_possible": possible,
                "reason": "身份组合未唯一，该名次待核；编排按实际名额继续"}))

    runoff_final = sorted(runoff_teams - set(qualified_teams) - tied_teams)
    # 同成绩并列且名额覆盖：并列队同样晋级，名单保留 tied 标注与并列说明。
    if tied_teams and len(qualified_teams) + len(tied_teams) <= quota:
        qualified_teams = sorted(set(qualified_teams) | tied_teams)
    payload = {
        "certification_combinations": len(cert_dict["complete_combinations"]),
        "penalties_sealed_until_finish": penalties,
        "quota": quota,
        "per_combo_analysis": per_combo,
        "roster": [e.to_dict() for e in entries],
        "qualified": sorted(qualified_teams),
        "tied": sorted(tied_teams),
        "runoff_teams": runoff_final,
        "instruction": (
            "下一轮先收 qualified；tied 按赛会并列规则共享/抽签；"
            "runoff_teams 安排加赛后补齐剩余名额"
        ),
    }
    payload["roster_id"] = _stable_id(payload)
    return payload
