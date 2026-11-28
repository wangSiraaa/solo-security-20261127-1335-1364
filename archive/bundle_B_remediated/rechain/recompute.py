# -*- coding: utf-8 -*-
"""
rechain.recompute —— 保管室独立复算编排。

* 每次复算开一个 mkdtemp 随机换名临时目录，环境变量清空到最小集，
  以子进程跑 rechain.runner，确保没有任何上一轮内存可借用；
* 读取子进程产出的结果清单，核对每个栏目用过的输入版本与文件哈希；
* 支持按栏目单局部沿用（carry）：沿用值必须与上一轮封存哈希一致，
  其余栏目按现行口径（勘误后）重算；
* 对照两轮结果，给出签认 / 无证签认结论。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional

MIN_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "PYTHONHASHSEED": "0"}


def _spawn(bundle_dir: str, notebook_rel: str, label: str,
           errata_src: Optional[str], carry_src: Optional[str],
           cells: Optional[List[str]]) -> Dict[str, Any]:
    workdir = tempfile.mkdtemp(prefix="recompute_")
    # 只拷封存包内容进随机目录（环境里不带任何历史产物）
    dst = os.path.join(workdir, "bundle")
    shutil.copytree(bundle_dir, dst)
    result_path = os.path.join(workdir, "result.json")
    cmd = [sys.executable, "-m", "rechain.runner",
           "--notebook", os.path.join(dst, "notebook_dir", notebook_rel),
           "--label", label, "--result", result_path]
    env = dict(MIN_ENV)
    if errata_src:
        shutil.copy2(errata_src, os.path.join(workdir, "erratum.json"))
        cmd += ["--errata", os.path.join(workdir, "erratum.json")]
    if carry_src:
        shutil.copy2(carry_src, os.path.join(workdir, "column_sheet.json"))
        cmd += ["--carry", os.path.join(workdir, "column_sheet.json")]
    if cells:
        cmd += ["--cells", ",".join(cells)]
    proc = subprocess.run(cmd, cwd=dst, env=env, capture_output=True,
                          text=True, timeout=120)
    try:
        result = json.load(open(result_path, encoding="utf-8"))
    except FileNotFoundError:
        result = {"failure": {"type": "RunnerCrash",
                              "message": proc.stderr.strip()[-2000:]}}
    result["_workdir"] = workdir
    result["_returncode"] = proc.returncode
    result["_stdout"] = proc.stdout
    result["_stderr"] = proc.stderr
    return result


def independent_recompute(bundle_dir: str, notebook_file: str,
                          label: str, erratum: Optional[str] = None,
                          carry: Optional[str] = None,
                          cells: Optional[List[str]] = None) -> Dict[str, Any]:
    seal = json.load(open(os.path.join(bundle_dir, "seal_manifest.json"),
                          encoding="utf-8"))
    result = _spawn(bundle_dir, notebook_file, label, erratum, carry, cells)

    # 复算室核对：
    #  * 原始输入（封存清单里带哈希的 load）必须与封存记录逐位一致；
    #  * 带版本中间表由 SAVE 格在本次重跑中重新生成，哈希可不同，
    #    其生成链上游的原始输入已逐位核对。
    produced = {f["rel_path"] for f in seal.get("regenerated_intermediates", [])}
    sealed = {os.path.normpath(f["rel_path"]): f.get("sha256")
              for f in seal["files"] if f.get("exists")}
    checks = []
    for acc in result.get("file_accesses", []):
        if acc["kind"] != "load":
            continue
        norm = os.path.normpath(acc["rel_path"])
        key = next((k for k in sealed if norm.endswith(k)), None)
        is_intermediate = any(norm.endswith(os.path.normpath(p)) for p in produced)
        same = bool(key and acc["sha256"] == sealed[key])
        checks.append({
            "rel_path": acc["rel_path"],
            "version_used": acc["version"],
            "class": "intermediate-regenerated" if is_intermediate else "raw-input",
            "sha256_observed": acc["sha256"],
            "sha256_sealed": sealed.get(key) if key else None,
            "match": same if not is_intermediate else True,
            "raw_identity_required": not is_intermediate,
            "note": ("中间表按现行口径重新生成，允许与第一轮不同"
                     if is_intermediate else
                     ("与封存逐位一致" if same else "原始输入哈希不符，拒绝签认")),
        })
    raw_ok = all(c["match"] for c in checks if c["raw_identity_required"])
    result["input_verification"] = checks
    result["seal_verdict"] = seal.get("verdict")
    result["signed"] = (
        result.get("failure") is None
        and seal.get("sealable", False)
        and raw_ok
        and all(o["exists"] for o in result.get("outputs", {}).get("files", {}).values())
    )
    return result


def compare_runs(round1: Dict[str, Any], round2: Dict[str, Any]) -> Dict[str, Any]:
    """两轮流图/表对照：哈希相同即未变，不同即勘误导致分开。"""
    o1 = round1.get("outputs", {}).get("files", {})
    o2 = round2.get("outputs", {}).get("files", {})
    cmp_rows = []
    for rel in sorted(set(o1) | set(o2)):
        h1, h2 = o1.get(rel, {}).get("sha256"), o2.get(rel, {}).get("sha256")
        cmp_rows.append({
            "rel_path": rel,
            "round1_sha256": h1,
            "round2_sha256": h2,
            "changed": h1 != h2,
            "round1_exists": bool(h1),
            "round2_exists": bool(h2),
        })
    return {"comparison": cmp_rows}


def verify_carry(round1: Dict[str, Any], column_sheet: Dict[str, Any]) -> List[Dict[str, Any]]:
    """栏目沿用核对：声明沿用的栏目，其依据哈希必须在上一轮结果中存在且一致。"""
    lineage = {c["variable"]: c for c in
               round1.get("outputs", {}).get("column_lineage", [])}
    verdicts = []
    for item in column_sheet.get("columns", []):
        if item["policy"] != "carry":
            continue
        ref = item.get("evidence_hash")
        found = any(c["content_hash"] == ref for c in lineage.values())
        verdicts.append({
            "column": item["name"],
            "policy": "carry",
            "evidence_hash": ref,
            "evidence_found_in_round1": found,
            "verdict": "允许局部沿用" if found else "无证，不得沿用，须重算",
        })
    return verdicts
