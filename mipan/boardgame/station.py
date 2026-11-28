"""赛后个人记分台（可写的那台）。

它在退场之后才收到本场参数：
- 团体/个人占比 team_share / individual_share（合计 1）
- 团体池 team_pool
- 退场前已经抵过的奖励 prior_offsets
- 并列晋级规则 tie_rules
只能用退场人实际能带走的资料（desk 审计通过的包）重算，
办出一笔新的晋级判定或补差。另提供：
- 保持见证不变、仅改一次已兑奖励的改正流程（留痕，不碰 boundary_witness）
- 两套不同暗局带走件的等价对比（公开过程、可见积分与条件相同即可各自核分）
"""

from __future__ import annotations

import json
import os

from .commit import sha256_bytes, canonical
from . import desk


class StationError(Exception):
    pass


def _require_clean(pkg_dir: str) -> dict:
    report = desk.full_audit(pkg_dir)
    if not report["ok"]:
        raise StationError(f"带走件审计不过，拒绝结算: {report['problems']}")
    return report


def recompute(pkg_dir: str, team_pool: int, team_share: float,
              prior_offset: int = 0, tie_score: int | None = None,
              tie_rules: tuple[str, ...] = ("settled_desc", "loop_count_desc",
                                            "player_id_asc")) -> dict:
    """按本场占比重算个人应得与晋级/补差。"""
    pkg = desk.load_package(pkg_dir)
    report = _require_clean(pkg_dir)
    if not 0 <= team_share <= 1:
        raise StationError("占比必须落在 [0,1]")
    individual_share = 1 - team_share
    pid = pkg["manifest.json"]["player"]
    settled = dict(pkg["score.json"]["settled"])
    own_settled = int(settled[str(pid)])
    # 挂起暗圈：条件条目不计入，只登记笔数，等封盘
    pending = [e for e in pkg["conditional.json"] if e["status"] == "pending"]

    gross = round(own_settled * individual_share
                  + team_pool * team_share)
    net = gross - prior_offset
    loop_count = sum(1 for e in pkg["ledger.json"]
                     if e["kind"] in ("first_close", "reconnect"))

    ranking_keys = {
        "settled_desc": own_settled,
        "loop_count_desc": loop_count,
        "player_id_asc": -pid,
    }
    advance = None
    if tie_score is not None:
        if own_settled > tie_score:
            advance = "晋级"
        elif own_settled < tie_score:
            advance = "未晋级"
        else:  # 并列，按规则逐键比较（这里以本人键值与门槛键值演示）
            advance = "并列-按 tie_rules 复核"
    verdict = {
        "match": pkg["manifest.json"]["match"],
        "player": pkg["manifest.json"]["name"],
        "inputs": {"team_pool": team_pool, "team_share": team_share,
                   "individual_share": individual_share,
                   "prior_offset": prior_offset, "tie_score": tie_score,
                   "tie_rules": list(tie_rules)},
        "basis": {"own_settled": own_settled, "loop_count": loop_count,
                  "pending_dark_loops": len(pending),
                  "team_pool": team_pool},
        "gross_due": gross,
        "prior_offset": prior_offset,
        "net_payment": net,
        "decision": "补差" if net > 0 else ("追回" if net < 0 else "两清"),
        "advancement": advance,
        "note": "挂起暗圈未计入，封盘揭密后凭承诺再结" if pending else "无挂起项",
    }
    return verdict


def correct_reward(pkg_dir: str, loop_id: str, new_score: int,
                   reason: str, out_dir: str) -> str:
    """仅改正一次已兑奖励金额。

    boundary_witness.json / own_placements.json 原样复制（见证不变），
    ledger.json 与 score.json 同步改并留 correction_log；重算清单哈希，
    旧 manifest 与新文件的差异本身就是改正痕迹。
    """
    pkg = desk.load_package(pkg_dir)
    os.makedirs(out_dir, exist_ok=True)
    target = next((e for e in pkg["ledger.json"] if e["loop_id"] == loop_id), None)
    if target is None or target.get("score") is None:
        raise StationError("该圈不在可结算账本中（暗圈不能预改金额）")
    old_score = target["score"]
    target["score"] = new_score
    pid = pkg["manifest.json"]["player"]
    pkg["score.json"]["settled"][str(pid)] += new_score - old_score
    pkg.setdefault("correction_log.json", []).append({
        "loop_id": loop_id, "old_score": old_score, "new_score": new_score,
        "reason": reason, "witness_unchanged": True,
    })
    for fname, payload in pkg.items():
        if fname == "manifest.json":
            continue
        with open(os.path.join(out_dir, fname), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
    manifest = {"files": {}, "player": pid,
                "name": pkg["manifest.json"]["name"],
                "match": pkg["manifest.json"]["match"]}
    for fname in payload_names(pkg):
        blob = json.dumps(pkg[fname], ensure_ascii=False, indent=2,
                          sort_keys=True).encode("utf-8")
        with open(os.path.join(out_dir, fname), "wb") as f:
            f.write(blob)
        manifest["files"][fname] = sha256_bytes(blob)
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2, sort_keys=True)
    return out_dir


def payload_names(pkg: dict) -> list[str]:
    return sorted(f for f in pkg if f != "manifest.json")


def compare_dark_games(pkg_a_dir: str, pkg_b_dir: str) -> dict:
    """两种不同暗局的整套带走件对比。

    当公开过程、本人可见积分与条件完全相同，退场人无须看到对手备件
    即可各自核分：本函数只比较可带走字段，并明确不触碰对手备件。
    """
    a, b = desk.load_package(pkg_a_dir), desk.load_package(pkg_b_dir)
    ra, rb = desk.full_audit(pkg_a_dir), desk.full_audit(pkg_b_dir)
    fields = [
        ("公开过程", lambda p: p["public_process.json"]),
        ("可见账本", lambda p: p["ledger.json"]),
        ("条件条目", lambda p: p["conditional.json"]),
        ("已领目标", lambda p: p["objectives_claimed.json"]),
        ("本人比分", lambda p: p["score.json"]),
        ("规则", lambda p: p["rules.json"]),
    ]
    diffs = [name for name, fn in fields if canonical(fn(a)) != canonical(fn(b))]
    return {
        "both_audit_ok": ra["ok"] and rb["ok"],
        "equivalent_for_exit": not diffs,
        "differing_fields": diffs,
        "opponent_spares_inspected": False,
        "note": "对手备件不在任何带走件中，对比不依赖也不泄露暗区实物",
    }
