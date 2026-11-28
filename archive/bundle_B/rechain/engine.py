# -*- coding: utf-8 -*-
"""
rechain.engine —— 笔记单元执行器。

职责：
* 每条 LOAD/SAVE/PLOT 的文件访问一律相对笔记本目录，并记录实际路径；
* 同名变量在后格覆写时保留覆写历史（不静默丢旧值）；
* 每个结果记录：语句序号、输入表内容哈希、输入文件版本、产出哈希；
* 输出完整运行清单 manifest，使任何数/图都能追到输入版本和执行次序；
* 支持只重跑某一格（rerun_cell），并标注本次次序与被覆写变量。
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import lang
from . import table as T
from . import charts


@dataclass
class Access:
    kind: str            # load | save | plot
    rel_path: str
    abs_path: str
    sha256: Optional[str]
    version: Optional[str]


@dataclass
class Step:
    seq: int
    cell_id: str
    lineno: int
    raw: str
    verb: str
    output: Optional[str]          # 产出变量名或文件路径
    output_hash: Optional[str]
    inputs: List[Dict[str, Any]]   # 上游表/文件及其版本
    note: str = ""


@dataclass
class Engine:
    notebook_dir: str
    tables: Dict[str, T.Table] = field(default_factory=dict)
    history: Dict[str, List[str]] = field(default_factory=dict)  # 变量 -> 历次哈希
    steps: List[Step] = field(default_factory=list)
    accesses: List[Access] = field(default_factory=list)
    seq: int = 0
    files_written: Dict[str, str] = field(default_factory=dict)  # rel -> sha256
    # 复算室持有的正式勘误：{相对路径: {列: 正确单位}}，在 LOAD 时并入
    errata: Dict[str, Dict[str, str]] = field(default_factory=dict)

    # ---- 路径与哈希 ----
    def _abs(self, rel: str) -> str:
        if os.path.isabs(rel):
            raise ValueError(f"笔记本内禁止绝对路径，收到: {rel}")
        norm = os.path.normpath(os.path.join(self.notebook_dir, rel))
        # 允许笔记本目录内及其共享资料根内的相对路径，禁止逃逸出资料根
        root = os.path.abspath(os.path.dirname(self.notebook_dir.rstrip(os.sep)))
        if not (norm.startswith(root + os.sep) or norm == root):
            raise ValueError(f"相对路径逃逸资料根: {rel}")
        return norm

    @staticmethod
    def _file_sha(path: str) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    def _record_access(self, kind: str, rel: str, version: Optional[str]) -> Access:
        ap = self._abs(rel)
        sha = self._file_sha(ap) if os.path.exists(ap) else None
        acc = Access(kind, rel, ap, sha, version)
        self.accesses.append(acc)
        return acc

    # ---- 变量覆写留痕 ----
    def _store(self, name: str, tbl: T.Table) -> None:
        self.tables[name] = tbl
        self.history.setdefault(name, []).append(tbl.content_hash())

    def _table_inputs(self, names: List[str]) -> List[Dict[str, Any]]:
        out = []
        for n in names:
            if n not in self.tables:
                raise KeyError(f"变量 {n!r} 不存在（可能未按次序执行或依赖未交资料）")
            t = self.tables[n]
            out.append({
                "variable": n,
                "content_hash": t.content_hash(),
                "source_version": t.meta.get("version"),
                "source_file": t.meta.get("source"),
                "unit_override": t.meta.get("unit_override"),
                "parents": list(t.parents),
            })
        return out

    # ---- 单条语句 ----
    def exec_statement(self, st: lang.Statement, cell_id: str) -> Step:
        self.seq += 1
        seq = self.seq
        a = st.args
        before = set(self.tables)
        note = ""
        output_name: Optional[str] = None

        if st.verb == "LOAD":
            rel = a["path"]
            ap = self._abs(rel)
            if not os.path.exists(ap):
                raise FileNotFoundError(
                    f"LOAD 失败：{rel} 不在所交资料内（疑为借用旧内存/临时文件的单元）")
            tbl = T.load_csv(ap, name=a["name"],
                             unit_override=_merge_units(rel, a["units"], self.errata))
            applied = dict(tbl.meta.get("unit_override") or {})
            acc = self._record_access("load", rel, tbl.meta.get("version"))
            converted = T.to_base_units(tbl)
            converted.name = a["name"]
            converted.meta["loaded_version"] = tbl.meta.get("version")
            if applied:
                converted.meta["erratum_applied"] = applied
            self._store(a["name"], converted)
            output_name = a["name"]
            note = f"载入版本 {tbl.meta.get('version')}，访问记录 #{len(self.accesses)}"
            if applied:
                note += f"；应用单位勘误 {applied}"

        elif st.verb == "FILTER":
            src = self.tables[a["src"]]
            out = src.filter(a["dst"], lambda r: bool(lang.evaluate(a["expr"], r)))
            self._store(a["dst"], out)
            output_name, note = a["dst"], f"保留 {len(out.rows)}/{len(src.rows)} 行"

        elif st.verb == "JOIN":
            out = self.tables[a["left"]].join(
                a["dst"], self.tables[a["right"]], a["keys"])
            self._store(a["dst"], out)
            output_name = a["dst"]

        elif st.verb in ("DERIVE", "UPDATE"):
            src = self.tables[a["src"]]
            if st.verb == "UPDATE" and a["col"] not in src.columns:
                raise KeyError(f"UPDATE 只能局部更新已有纵列，{a['col']!r} 不存在")
            inferred = lang.infer_dimension(a["expr"], src)
            declared = T.dimension_of(a["unit"]) if a["unit"] else None
            if inferred and declared and inferred != declared:
                raise ValueError(
                    f"{a['col']} 声明单位维度 {declared} 与表达式维度 {inferred} 不符")
            if st.verb == "DERIVE" and a["col"] in src.columns:
                raise KeyError(f"DERIVE 不能覆盖已有纵列 {a['col']!r}，请用 UPDATE")
            vals = [lang.evaluate(a["expr"], r) for r in src._dicts()]
            out = src.update_column(a["dst"], a["col"], vals, a["unit"])
            self._store(a["dst"], out)
            output_name = a["dst"]
            note = f"结果维度 {inferred}，声明 {declared}"

        elif st.verb == "GROUP":
            out = self.tables[a["src"]].groupby(a["dst"], a["keys"], a["aggs"])
            self._store(a["dst"], out)
            output_name = a["dst"]

        elif st.verb == "SORT":
            out = self.tables[a["src"]].sort(a["dst"], a["col"])
            self._store(a["dst"], out)
            output_name = a["dst"]

        elif st.verb == "SAVE":
            tbl = self.tables[a["src"]]
            ap = self._abs(a["path"])
            os.makedirs(os.path.dirname(ap), exist_ok=True)
            T.save_csv(tbl, ap)
            self.files_written[a["path"]] = self._file_sha(ap)
            self._record_access("save", a["path"], None)
            output_name = a["path"]
            note = f"写出 {len(tbl.rows)} 行；sha256={self.files_written[a['path']][:12]}"

        elif st.verb == "PLOT":
            tbl = self.tables[a["src"]]
            ap = self._abs(a["path"])
            os.makedirs(os.path.dirname(ap), exist_ok=True)
            charts.write_svg(tbl, a["x"], a["series"], ap, title=a["title"] or tbl.name)
            self.files_written[a["path"]] = self._file_sha(ap)
            self._record_access("plot", a["path"], None)
            output_name = a["path"]
            note = "SVG 趋势图 sha256=" + self.files_written[a["path"]][:12]
        else:  # pragma: no cover
            raise SyntaxError(f"未实现的动词 {st.verb}")

        if output_name in before and st.verb not in ("SAVE", "PLOT"):
            note = (note + "；" if note else "") + f"同名变量覆写（历史: {self.history[output_name]}）"

        inputs = self._table_inputs(_input_vars(st, before))
        h = (self.tables[output_name].content_hash()
             if output_name in self.tables else self.files_written.get(output_name))
        step = Step(seq, cell_id, st.lineno, st.raw, st.verb,
                    output_name, h, inputs, note)
        self.steps.append(step)
        return step

    # ---- 整程序 / 整笔记本 ----
    def run_program(self, source: str, cell_id: str = "cell") -> List[Step]:
        return [self.exec_statement(st, cell_id) for st in lang.parse(source).statements]

    def run_notebook(self, nb: Dict[str, Any], only_cells: Optional[List[str]] = None,
                     skip_cells: Optional[List[str]] = None) -> List[Step]:
        done: List[Step] = []
        for cell in nb["cells"]:
            cid = cell["id"]
            if only_cells is not None and cid not in only_cells:
                continue
            if skip_cells and cid in skip_cells:
                continue
            done.extend(self.run_program(cell["code"], cid))
        return done

    def rerun_cell(self, nb: Dict[str, Any], cell_id: str) -> List[Step]:
        """重复运行某格：变量已存在也允许，覆写留痕。"""
        cell = next(c for c in nb["cells"] if c["id"] == cell_id)
        return self.run_program(cell["code"], cell_id)

    # ---- 清单 ----
    def manifest(self, notebook_id: str, run_label: str,
                 notebook: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        column_lineage = []
        for name, t in self.tables.items():
            column_lineage.append({
                "variable": name,
                "content_hash": t.content_hash(),
                "columns": t.columns,
                "units": t.units,
                "source_versions": sorted({
                    str(t.meta.get("loaded_version"))
                    for _ in [0] if t.meta.get("loaded_version")}),
                "erratum_applied": t.meta.get("erratum_applied"),
                "parents": t.parents,
            })
        outputs = {"files": {}, "column_lineage": column_lineage}
        for decl in (notebook or {}).get("declared_outputs", []):
            rel = decl["rel_path"]
            ap = self._abs(rel)
            outputs["files"][rel] = {
                "sha256": self._file_sha(ap) if os.path.exists(ap) else None,
                "exists": os.path.exists(ap), "kind": decl["kind"]}
        return {
            "notebook_id": notebook_id,
            "run_label": run_label,
            "execution_order": [
                {"seq": s.seq, "cell": s.cell_id, "verb": s.verb,
                 "raw": s.raw, "output": s.output,
                 "output_hash": s.output_hash, "inputs": s.inputs, "note": s.note}
                for s in self.steps
            ],
            "file_accesses": [
                {"kind": a.kind, "rel_path": a.rel_path, "sha256": a.sha256,
                 "version": a.version}
                for a in self.accesses
            ],
            "variables": {
                name: {"current_hash": t.content_hash(),
                       "overwrite_history": self.history.get(name, []),
                       "units": t.units}
                for name, t in self.tables.items()
            },
            "outputs": outputs,
        }


def _input_vars(st: lang.Statement, existing: set) -> List[str]:
    a = st.args
    names: List[str] = []
    for key in ("src", "left", "right"):
        if key in a and a[key] in existing:
            names.append(a[key])
    return names


def _merge_units(rel: str, inline: Optional[Dict[str, str]],
                 errata: Dict[str, Dict[str, str]]) -> Optional[Dict[str, str]]:
    merged: Dict[str, str] = dict(inline or {})
    for pattern, override in errata.items():
        if pattern == rel or os.path.basename(pattern) == os.path.basename(rel):
            merged.update(override)
    return merged or None
