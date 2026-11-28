# -*- coding: utf-8 -*-
"""
rechain.table —— 小型数据运算语言的表内核（纯标准库）。

一张表 = 有序纵列 + 行 + 每列一个计量单位（维度）。
支持：过滤、分组、汇总、局部更新（覆盖同名列）、连接、带单位的四则运算。
所有度量值在载入时按单位换算成基准单位参与运算；
带单位的表达式做维度检查，禁止跨维度相加（如 米 + 千克）。
"""
from __future__ import annotations

import csv
import io
import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------- 单位登记
# 单位 -> (维度, 相对基准单位的倍数)；基准单位倍数为 1。
UNIT_REGISTRY: Dict[str, Tuple[str, float]] = {
    # 能量
    "Wh": ("energy", 1.0),
    "kWh": ("energy", 1000.0),
    # 体积
    "m3": ("volume", 1.0),
    "L": ("volume", 0.001),
    # 质量
    "kg": ("mass", 1.0),
    "t": ("mass", 1000.0),
    # 长度
    "m": ("length", 1.0),
    "cm": ("length", 0.01),
}
# 复合单位（派生列用），如 kWh/m3 -> 维度 energy/volume
DIM_DIV = {("energy", "volume"): "energy/volume"}


def register_unit(name: str, dimension: str, factor: float) -> None:
    """登记自定义单位（合成表可自带单位）。"""
    UNIT_REGISTRY[name] = (dimension, factor)


def dimension_of(unit: Optional[str]) -> Optional[str]:
    if unit is None:
        return None
    if unit in UNIT_REGISTRY:
        return UNIT_REGISTRY[unit][0]
    if "/" in unit:  # 复合单位，如 kWh/m3
        u_num, u_den = unit.split("/", 1)
        d_num = dimension_of(u_num)
        d_den = dimension_of(u_den)
        if d_num and d_den:
            return DIM_DIV.get((d_num, d_den), f"{d_num}/{d_den}")
    return None


def factor(unit: str) -> float:
    if unit not in UNIT_REGISTRY:
        raise KeyError(f"未登记的单位: {unit!r}（可用 register_unit 登记）")
    return UNIT_REGISTRY[unit][1]


# ---------------------------------------------------------------- 数值规范化
def _num(x: Any) -> float:
    if isinstance(x, bool):
        return float(x)
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip()
    if s == "":
        raise ValueError("空串不能当数值")
    return float(s)


def canon_num(x: float) -> str:
    """稳定的数值文本（哈希用）：整数去 .0，浮点保留 10 位有效数字。"""
    if x == int(x):
        return str(int(x))
    return f"{x:.10g}"


# ---------------------------------------------------------------- 表
@dataclass
class Table:
    name: str
    columns: List[str]
    rows: List[List[Any]]
    units: Dict[str, Optional[str]]
    meta: Dict[str, Any] = field(default_factory=dict)
    parents: List[str] = field(default_factory=list)  # 上游表内容哈希

    # --- 基础访问 ---
    def col_index(self, col: str) -> int:
        try:
            return self.columns.index(col)
        except ValueError:
            raise KeyError(f"表 {self.name!r} 没有纵列 {col!r}，现有列: {self.columns}")

    def unit_dim(self, col: str) -> Optional[str]:
        return dimension_of(self.units.get(col))

    def copy(self, name: Optional[str] = None) -> "Table":
        return Table(
            name=name or self.name,
            columns=list(self.columns),
            rows=[list(r) for r in self.rows],
            units=dict(self.units),
            meta=dict(self.meta),
            parents=list(self.parents),
        )

    # --- 内容哈希（不含表名，只看列/单位/数据；规范化后 JSON） ---
    def content_hash(self) -> str:
        payload = {
            "columns": self.columns,
            "units": [self.units.get(c) for c in self.columns],
            "rows": [
                [canon_num(v) if isinstance(v, float) else v for v in r]
                for r in self.rows
            ],
        }
        blob = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()[:16]

    # --- 派生构造 ---
    def _derive(self, name: str, columns: List[str], rows: List[List[Any]],
                units: Dict[str, Optional[str]]) -> "Table":
        t = Table(name=name, columns=columns, rows=rows, units=units,
                  meta=dict(self.meta), parents=self.parents + [self.content_hash()])
        return t

    # --- 过滤：谓词为 (row_dict) -> bool ---
    def filter(self, name: str, predicate: Callable[[Dict[str, Any]], bool]) -> "Table":
        kept = [r for r in self._dicts() if predicate(r)]
        rows = [[d[c] for c in self.columns] for d in kept]
        return self._derive(name, list(self.columns), rows, dict(self.units))

    # --- 局部更新：覆盖同名纵列；同维度则允许换单位，新列直接追加 ---
    def update_column(self, name: str, col: str,
                      values: Sequence[Any],
                      unit: Optional[str]) -> "Table":
        if len(values) != len(self.rows):
            raise ValueError(f"更新列 {col!r} 长度 {len(values)} 与行数 {len(self.rows)} 不符")
        cols = list(self.columns)
        units = dict(self.units)
        if col in cols:
            old_dim = self.unit_dim(col)
            new_dim = dimension_of(unit)
            if old_dim and new_dim and old_dim != new_dim:
                raise ValueError(
                    f"局部更新 {col!r} 维度冲突: {old_dim} -> {new_dim}（同名字段只能换同维度计量单位）")
            idx = cols.index(col)
        else:
            idx = len(cols)
            cols.append(col)
        rows = [list(r) for r in self.rows]
        for i, v in enumerate(rows):
            if idx < len(v):
                v[idx] = values[i]
            else:
                v.append(values[i])
        units[col] = unit
        return self._derive(name, cols, rows, units)

    # --- 派生新纵列（表达式求值结果），做维度检查 ---
    def add_column(self, name: str, col: str,
                   func: Callable[[Dict[str, Any]], Any],
                   unit: Optional[str],
                   dim_check: Optional[Tuple[Optional[str], Optional[str]]] = None
                   ) -> "Table":
        values = [func(d) for d in self._dicts()]
        out = self.update_column(name, col, values, unit)
        if dim_check:
            lhs_dim, rhs_dim = dim_check
            if lhs_dim and rhs_dim and lhs_dim != rhs_dim:
                raise ValueError(f"表达式维度不匹配: {lhs_dim} != {rhs_dim}")
        return out

    # --- 分组 + 汇总；agg_specs: [(输出列, 输入列, 函数名)] ---
    def groupby(self, name: str, keys: Sequence[str],
                agg_specs: Sequence[Tuple[str, str, str]]) -> "Table":
        funcs = AGG_FUNCS
        groups: Dict[Tuple, List[Dict[str, Any]]] = {}
        order: List[Tuple] = []
        for d in self._dicts():
            k = tuple(d[c] for c in keys)
            if k not in groups:
                groups[k] = []
                order.append(k)
            groups[k].append(d)
        out_cols = list(keys) + [spec[0] for spec in agg_specs]
        units: Dict[str, Optional[str]] = {c: self.units.get(c) for c in keys}
        rows: List[List[Any]] = []
        for k in order:
            members = groups[k]
            row = list(k)
            for out_col, in_col, fn in agg_specs:
                if fn == "count":
                    vals = [float(len(members))]
                    row.append(vals[0])
                    units[out_col] = None
                    continue
                vals = [_num(d[in_col]) for d in members]
                row.append(funcs[fn](vals))
                units[out_col] = self.units.get(in_col) if fn in ("sum", "max", "min") else None
            rows.append(row)
        return self._derive(name, out_cols, rows, units)

    # --- 等值连接（自然键，右表列追加；同名列报错以防歧义） ---
    def join(self, name: str, right: "Table", keys: Sequence[str]) -> "Table":
        for c in right.columns:
            if c in self.columns and c not in keys:
                raise ValueError(f"连接列名冲突: {c!r} 在两表均存在")
        r_idx = {tuple(d[k] for k in keys): d for d in right._dicts()}
        extra = [c for c in right.columns if c not in keys]
        out_cols = list(self.columns) + extra
        units = dict(self.units)
        units.update({c: right.units.get(c) for c in extra})
        rows: List[List[Any]] = []
        for dl in self._dicts():
            k = tuple(dl[c] for c in keys)
            dr = r_idx.get(k)
            if dr is None:
                continue
            rows.append([dl[c] for c in self.columns] + [dr[c] for c in extra])
        t = Table(name=name, columns=out_cols, rows=rows, units=units,
                  meta={**self.meta, **right.meta},
                  parents=self.parents + [self.content_hash(), right.content_hash()])
        return t

    # --- 排序（趋势图按月份等） ---
    def sort(self, name: str, col: str) -> "Table":
        idx = self.col_index(col)
        rows = sorted((list(r) for r in self.rows), key=lambda r: r[idx])
        return self._derive(name, list(self.columns), rows, dict(self.units))

    def _dicts(self) -> List[Dict[str, Any]]:
        return [dict(zip(self.columns, r)) for r in self.rows]

    def to_csv_text(self) -> str:
        buf = io.StringIO()
        meta = dict(self.meta)
        meta["name"] = self.name
        # 落盘只保留相对来源标识，不固化运行机临时目录绝对路径
        if meta.get("source"):
            meta["source"] = os.path.basename(meta["source"])
        buf.write("# " + json.dumps(meta, ensure_ascii=False, sort_keys=True) + "\n")
        buf.write("# " + ",".join(
            f"{c}[{self.units.get(c) or '-'}]" for c in self.columns) + "\n")
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(self.columns)
        for r in self.rows:
            w.writerow([canon_num(v) if isinstance(v, float) else v for v in r])
        return buf.getvalue()
        return buf


# 汇总函数；sum/max/min 保持原单位，mean 保持原单位（同量纲平均），count 无单位
AGG_FUNCS: Dict[str, Callable[[List[float]], float]] = {
    "sum": lambda xs: sum(xs),
    "mean": lambda xs: sum(xs) / len(xs),
    "max": lambda xs: max(xs),
    "min": lambda xs: min(xs),
    "count": lambda xs: float(len(xs)),
}


# ---------------------------------------------------------------- CSV 读写
def load_csv(path: str, name: Optional[str] = None,
             unit_override: Optional[Dict[str, str]] = None) -> Table:
    """读带 # 元信息头的 CSV。unit_override 即勘误口径：只改某列计量单位声明。"""
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    return parse_csv(raw, name=name, source=path, unit_override=unit_override)


def parse_csv(raw: str, name: Optional[str] = None, source: str = "<str>",
              unit_override: Optional[Dict[str, str]] = None) -> Table:
    lines = raw.splitlines()
    meta: Dict[str, Any] = {}
    header_units: Dict[str, Optional[str]] = {}
    data_lines: List[str] = []
    for ln in lines:
        if ln.startswith("#"):
            body = ln[1:].strip()
            if body.startswith("{"):
                meta.update(json.loads(body))
            elif "[" in body:
                for token in body.split(","):
                    col, u = token[:-1].split("[", 1)
                    header_units[col] = None if u == "-" else u
        elif ln.strip():
            data_lines.append(ln)
    if not data_lines:
        raise ValueError(f"{source}: 无数据行")
    reader = csv.reader(io.StringIO("\n".join(data_lines)))
    columns = next(reader)
    rows = [[_coerce(v) for v in r] for r in reader]
    units = {c: header_units.get(c) for c in columns}
    if unit_override:
        for c, u in unit_override.items():
            if c not in units:
                raise KeyError(f"勘误单位针对的列 {c!r} 不存在于 {source}")
            old_dim = dimension_of(units.get(c))
            new_dim = dimension_of(u)
            if old_dim and new_dim and old_dim != new_dim:
                raise ValueError(f"勘误只能修正同维度计量单位: {c} {old_dim}->{new_dim}")
            units[c] = u
    tname = name or meta.get("name") or source
    t = Table(name=tname, columns=columns, rows=rows, units=units, meta=meta)
    t.meta["source"] = source
    if unit_override:
        t.meta["unit_override"] = unit_override
    return t


def _coerce(v: str) -> Any:
    try:
        f = float(v)
        return int(f) if f == int(f) else f
    except ValueError:
        return v


def save_csv(t: Table, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(t.to_csv_text())


def to_base_units(t: Table) -> Table:
    """把全部度量纵列按各自单位换算到基准单位（载入后第一步，勘误在此生效）。"""
    out = t.copy(t.name)
    for ci, col in enumerate(t.columns):
        u = t.units.get(col)
        if u and u in UNIT_REGISTRY:
            mult = factor(u)
            for r in out.rows:
                r[ci] = _num(r[ci]) * mult
            out.units[col] = base_unit_of(u)
            out.meta.setdefault("converted", {})[col] = u
    out.parents = t.parents + [t.content_hash()]
    return out


def base_unit_of(unit: str) -> str:
    dim = dimension_of(unit)
    for u, (d, f) in UNIT_REGISTRY.items():
        if d == dim and f == 1.0:
            return u
    return unit
