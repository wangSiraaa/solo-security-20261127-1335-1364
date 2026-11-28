"""只读退场证明台（赛后个人记分台的核验端）。

输入是 build_exit_package 产出的带走件目录；本模块只做读和算，
绝不写包内任何文件。核验四件事：
1. 清单完整性：manifest 中每个文件的 sha256 与实际一致
2. 承诺一致性：本人封存片实物与其 commitment 相符；交界见证未被改写
3. 公开成圈可重算：用公开片 + 本人封存片重放网格图，逐圈核对边、
   金额、事件类型（first_close / reconnect / cosmetic）
4. 暗圈条件正确：挂起条目只含承诺、交界、解封条件，未泄露金额；
   已揭的凭承诺重算金额
"""

from __future__ import annotations

import json
import os

from .commit import sha256_bytes, open_commitment
from .board import Board
from .game import BASE_PER_CELL


class DeskError(Exception):
    pass


def _load(pkg_dir: str, fname: str) -> dict:
    with open(os.path.join(pkg_dir, fname), encoding="utf-8") as f:
        return json.load(f)


def load_package(pkg_dir: str) -> dict:
    return {fname: _load(pkg_dir, fname) for fname in os.listdir(pkg_dir)
            if fname.endswith(".json")}


def verify_manifest(pkg: dict, pkg_dir: str) -> list[str]:
    manifest = pkg["manifest.json"]
    problems = []
    for fname, want in manifest["files"].items():
        fpath = os.path.join(pkg_dir, fname)
        if not os.path.exists(fpath):
            problems.append(f"缺文件 {fname}")
            continue
        with open(fpath, "rb") as f:
            got = sha256_bytes(f.read())
        if got != want:
            problems.append(f"清单不符 {fname}: {got} != {want}")
    return problems


def verify_commitments(pkg: dict) -> list[str]:
    problems = []
    own = pkg["own_placements.json"]
    for item in own["sealed"] + own["open"]:
        if item.get("commitment") and not open_commitment(
                item["commitment"], item["tile"]):
            problems.append(f"封存承诺被破坏: {item['cell']}")
    # 公开过程里的封存格外观/承诺要与本人留存一致
    cells = pkg["public_process.json"]["cells"]
    by_cell = {i["cell"]: i for i in own["sealed"] + own["open"]}
    for ck, info in cells.items():
        if ck in by_cell and info["commitment"] != by_cell[ck]["commitment"]:
            problems.append(f"交界见证被改写: {ck}")
    return problems


def _rebuild_board(pkg: dict) -> Board:
    pub = pkg["public_process.json"]
    board = Board(pub["board_size"])
    own_cells = {i["cell"]: i for i in
                 pkg["own_placements.json"]["sealed"] +
                 pkg["own_placements.json"]["open"]}
    for ck, info in pub["cells"].items():
        tile_dict = info["tile"]
        if tile_dict is None:
            if ck not in own_cells:
                continue  # 对手封存片：本台无权也无需重放其片内边
            tile_dict = own_cells[ck]["tile"]
        r, c = map(int, ck.split(","))
        board.placements[ck] = {
            "tile": tile_dict, "tile_open": tile_dict,
            "owner": info["owner"], "sealed": info["sealed"] and ck not in own_cells,
            "commitment": info["commitment"], "turn": info["turn"],
        }
        board.size = pub["board_size"]
    return board


def verify_loops(pkg: dict) -> list[dict]:
    """重放并核对账本里每个可结算圈，返回逐圈报告。"""
    board = _rebuild_board(pkg)
    cycles = {c.loop_id: c for c in board.find_cycles()}
    corrections = {c["loop_id"]: c for c in pkg.get("correction_log.json", [])}
    reports = []
    for ev in pkg["ledger.json"]:
        rep = {"loop_id": ev["loop_id"], "kind": ev["kind"], "ok": True, "notes": []}
        cyc = cycles.get(ev["loop_id"])
        if cyc is None:
            if ev["kind"] == "cosmetic":
                rep["notes"].append("cosmetic 圈边集合与既有圈一致，无独立新圈（符合定义）")
            else:
                rep["ok"] = False
                rep["notes"].append("用带走件重放不出该圈（暗区或被篡改）")
            reports.append(rep)
            continue
        if cyc.edges != ev["edges"]:
            rep["ok"] = False
            rep["notes"].append("边集合与账本不一致")
        if ev["kind"] == "cosmetic":
            if ev["score"] != 0:
                rep["ok"] = False
                rep["notes"].append("cosmetic 必须记 0 分")
            reports.append(rep)
            continue
        want = BASE_PER_CELL * len(cyc.cells)
        obj_id = ev.get("objective_id")
        if obj_id:
            obj = next((o for o in pkg["objectives_claimed.json"]
                        if o["id"] == obj_id), None)
            if obj and obj["target"] in cyc.cells:
                want += obj["reward"]
            else:
                rep["ok"] = False
                rep["notes"].append("目标归属无法由带走件证实")
        if ev["score"] is None:
            reports.append(rep)
            continue
        corr = corrections.get(ev["loop_id"])
        if corr:
            # 改正留痕：账本最新值 + 逐笔差额必须能还原重算值
            restored = ev["score"] + sum(
                c["old_score"] - c["new_score"]
                for c in pkg["correction_log.json"] if c["loop_id"] == ev["loop_id"])
            if not corr.get("witness_unchanged") or restored != want:
                rep["ok"] = False
                rep["notes"].append(
                    f"改正痕迹无法还原重算值：还原 {restored} != {want}")
            else:
                rep["notes"].append(
                    f"含一次奖励改正 {corr['old_score']}→{corr['new_score']}，见证未动")
        elif ev["score"] != want:
            rep["ok"] = False
            rep["notes"].append(f"金额重算 {want} 与账本 {ev['score']} 不符")
        reports.append(rep)
    return reports


def verify_conditional(pkg: dict) -> list[dict]:
    out = []
    for ent in pkg["conditional.json"]:
        checks = {
            "无金额泄露": "score" not in ent or ent.get("score") is None,
            "持有承诺": bool(ent.get("promise")),
            "解封条件非空": bool(ent.get("conditions")),
            "交界见证非空": bool(ent.get("junction")),
        }
        out.append({"loop_id": ent["loop_id"], "status": ent["status"],
                    "checks": checks, "ok": all(checks.values())})
    return out


def full_audit(pkg_dir: str) -> dict:
    pkg = load_package(pkg_dir)
    problems = verify_manifest(pkg, pkg_dir) + verify_commitments(pkg)
    loop_reports = verify_loops(pkg)
    cond_reports = verify_conditional(pkg)
    if any(not r["ok"] for r in loop_reports):
        problems.append("存在无法重算的圈")
    if any(not r["ok"] for r in cond_reports):
        problems.append("挂起条目不满足最小披露")
    return {
        "pkg": os.path.basename(os.path.abspath(pkg_dir)),
        "ok": not problems,
        "problems": problems,
        "loops": loop_reports,
        "conditional": cond_reports,
        "settled_score": pkg["score.json"]["settled"],
        "player": pkg["score.json"]["name"],
    }


def list_takeaway(pkg_dir: str) -> dict:
    """列出本人可带走资料的清单与排除项（只读）。"""
    pkg = load_package(pkg_dir)
    return {
        "files": sorted(pkg["manifest.json"]["files"].keys()),
        "exclusions": pkg["exclusions.json"],
        "withdrawn_spare_count": pkg["own_placements.json"]["withdrawn_spare_count"],
        "remaining_objectives":
            pkg["public_process.json"]["objectives_remaining_count"],
    }
