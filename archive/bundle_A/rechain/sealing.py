# -*- coding: utf-8 -*-
"""
rechain.sealing —— 最小可重演执行链封存。

从笔记本代码里静态提取全部文件引用（LOAD/SAVE/PLOT 的相对路径），
按“原图所需”最小集合拷贝：笔记本本体、原始输入、前格生成并被后格
重新装入的带版本中间表、复算引擎自身源码。
任何引用了但资料中不存在的路径（典型：上一轮 tmp/ 临时汇总），
照实登记为 missing_dependency，封存结论为 NOT_SEALABLE。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from typing import Any, Dict, List

FILE_REF = re.compile(r"(?:LOAD|SAVE|PLOT)\s+(.+?)(?:\s+AS|\s+TO|\s+x=|$)")


# 借用旧内存/临时文件的单元路径前缀：不随资料封存，须在洁净环境重新确认
EXCLUDED_PREFIXES = ("tmp/",)


def extract_references(nb: Dict[str, Any]) -> List[Dict[str, str]]:
    refs: List[Dict[str, str]] = []
    for cell in nb["cells"]:
        for line in cell["code"].splitlines():
            code = line.split("//", 1)[0].strip()
            if not code:
                continue
            verb = code.split(None, 1)[0].upper()
            if verb not in ("LOAD", "SAVE", "PLOT"):
                continue
            patterns = {
                "LOAD": (r"LOAD\s+(\S+)", "load"),
                "SAVE": (r"SAVE\s+\w+\s+TO\s+(\S+)", "intermediate"),
                "PLOT": (r"PLOT\s+\w+\s+x=\w+\s+series=[\w,]+\s+AS\s+(\S+)", "output"),
            }
            regex, kind = patterns[verb]
            m = re.match(regex, code)
            if m:
                refs.append({"cell": cell["id"], "verb": verb,
                             "kind": kind, "rel_path": m.group(1)})
    return refs


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _engine_files(pkg_dir: str) -> List[str]:
    return [os.path.join("rechain", f) for f in sorted(os.listdir(pkg_dir))
            if f.endswith(".py")]


def seal(nb_path: str, out_bundle: str, engine_pkg: str) -> Dict[str, Any]:
    nb_dir = os.path.dirname(os.path.abspath(nb_path))
    with open(nb_path, encoding="utf-8") as f:
        nb = json.load(f)
    refs = extract_references(nb)
    declared_outputs = {d["rel_path"] for d in nb.get("declared_outputs", [])}
    # SAVE 到声明输出路径的归为 output；其余 SAVE 才是可重演链上的中间依赖
    for ref in refs:
        if ref["verb"] == "SAVE" and ref["rel_path"] in declared_outputs:
            ref["kind"] = "output"
        if ref["rel_path"] in declared_outputs:
            ref["kind"] = "output"

    os.makedirs(out_bundle, exist_ok=True)
    bundle_nb_dir = os.path.join(out_bundle, "notebook_dir")
    os.makedirs(bundle_nb_dir, exist_ok=True)

    manifest: Dict[str, Any] = {
        "notebook_id": nb["id"],
        "interface": nb["interface"],
        "declared_outputs": nb["declared_outputs"],
        "files": [],
        "missing_dependencies": [],
        "engine_files": [],
    }

    # 笔记本本体
    shutil.copy2(nb_path, os.path.join(bundle_nb_dir, os.path.basename(nb_path)))
    manifest["notebook_file"] = os.path.basename(nb_path)

    # 引用文件（最小集）；不存在的依赖同样登记，判 NOT_SEALABLE。
    # 声明输出（图/表）与 tmp 一概不封存：复算室只接收依据，不接收屏上产物。
    seen = set()
    for ref in refs:
        rel = ref["rel_path"]
        if rel in seen:
            continue
        seen.add(rel)
        src = os.path.normpath(os.path.join(nb_dir, rel))
        excluded = any(rel.startswith(p) or os.path.normpath(rel).split(os.sep)[0] == p.rstrip("/")
                       for p in EXCLUDED_PREFIXES)
        is_declared_output = rel in declared_outputs
        entry = {**ref, "exists": os.path.exists(src),
                 "excluded": excluded or is_declared_output}
        if is_declared_output:
            manifest.setdefault("excluded_outputs", []).append(
                {"rel_path": rel, "reason": "声明提报产物不封存，必须由复算室重新生成"})
        if ref["kind"] == "intermediate":
            # 前格生成的带版本中间表属于执行链产物：登记版本但不拷旧副本，
            # 洁净重跑由 SAVE 格重新生成（“带版本的中间依赖重新跑原笔记”）
            manifest.setdefault("regenerated_intermediates", []).append(
                {"rel_path": rel, "cell": ref["cell"],
                 "sha256_author_copy": entry.get("sha256"),
                 "reason": "中间依赖不封存旧副本，复算时由前格重新生成"})
        if excluded or not os.path.exists(src):
            reason = ("借用旧内存/临时文件的单元，不随资料封存，须在洁净环境重新确认"
                      if excluded else
                      "所交资料中不存在；无法从提交资料重现（疑为借用旧内存/临时文件）")
            manifest["missing_dependencies"].append(
                {"rel_path": rel, "cell": ref["cell"], "verb": ref["verb"],
                 "reason": reason})
        elif is_declared_output or ref["kind"] == "intermediate":
            pass  # 不拷贝旧产物/旧中间表
        else:
            dst = os.path.join(bundle_nb_dir, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            entry["sha256"] = _sha256(src)
            entry["size"] = os.path.getsize(src)
        manifest["files"].append(entry)

    # 引擎源码（独立复算需要同版本执行器）
    pkg_target = os.path.join(out_bundle, "rechain")
    os.makedirs(pkg_target, exist_ok=True)
    for rel in _engine_files(engine_pkg):
        shutil.copy2(os.path.join(os.path.dirname(engine_pkg), rel),
                     os.path.join(out_bundle, rel))
        manifest["engine_files"].append(
            {"rel_path": rel, "sha256": _sha256(os.path.join(out_bundle, rel))})

    manifest["sealable"] = not manifest["missing_dependencies"]
    manifest["verdict"] = (
        "SEALED：形成所选图表所需的最小可重演执行链完整"
        if manifest["sealable"] else
        "NOT_SEALABLE：存在未随交的依赖，洁净重跑必然断链")
    with open(os.path.join(out_bundle, "seal_manifest.json"), "w",
              encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return manifest
