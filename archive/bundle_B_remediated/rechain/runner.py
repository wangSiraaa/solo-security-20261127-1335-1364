# -*- coding: utf-8 -*-
"""
rechain.runner —— 独立环境内的洁净重跑入口（由复算室在随机临时目录以子进程调用）。

用法：
  python -m rechain.runner --notebook notebooks/a.json \
      [--errata erratum.json] [--carry column_sheet.json] \
      [--cells c1,c2] [--label round2] --result result.json

只读取当前工作目录下封存包里的资料；除写出文件外不访问任何旧内存。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from typing import Any, Dict, Optional

from .engine import Engine


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def run(nb_path: str, errata_path: Optional[str], carry_path: Optional[str],
        only_cells: Optional[list], label: str, result_path: str) -> Dict[str, Any]:
    with open(nb_path, encoding="utf-8") as f:
        nb = json.load(f)
    nb_dir = os.path.dirname(os.path.abspath(nb_path))

    errata = {}
    if errata_path and os.path.exists(errata_path):
        doc = json.load(open(errata_path, encoding="utf-8"))
        errata = doc.get("unit_overrides", {})

    carry = None
    if carry_path and os.path.exists(carry_path):
        carry = json.load(open(carry_path, encoding="utf-8"))

    eng = Engine(notebook_dir=nb_dir, errata=errata)
    failure = None
    try:
        eng.run_notebook(nb, only_cells=only_cells)
    except (FileNotFoundError, KeyError, ValueError, SyntaxError) as exc:
        failure = {"type": type(exc).__name__, "message": str(exc),
                   "at_seq": eng.seq + 1}

    manifest = eng.manifest(nb["id"], label, nb)
    manifest["failure"] = failure
    manifest["errata_in_effect"] = errata
    manifest["cwd"] = os.getcwd()
    manifest["outputs"]["carry_policy"] = carry
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return manifest


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--notebook", required=True)
    p.add_argument("--errata")
    p.add_argument("--carry")
    p.add_argument("--cells")
    p.add_argument("--label", default="rerun")
    p.add_argument("--result", required=True)
    args = p.parse_args(argv)
    cells = args.cells.split(",") if args.cells else None
    manifest = run(args.notebook, args.errata, args.carry, cells,
                   args.label, args.result)
    print(json.dumps({
        "notebook_id": manifest["notebook_id"],
        "run_label": args.label,
        "failure": manifest["failure"],
        "outputs": {k: v["sha256"] for k, v in manifest["outputs"]["files"].items()},
    }, ensure_ascii=False, indent=2))
    return 0 if manifest["failure"] is None else 2


if __name__ == "__main__":
    sys.exit(main())
