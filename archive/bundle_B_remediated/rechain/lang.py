# -*- coding: utf-8 -*-
"""
rechain.lang —— 小型数据运算语言。

一行一条语句，// 起注释：

  LOAD path AS name [UNITS col=unit,...]
  FILTER src -> dst WHERE <表达式>
  JOIN   a WITH b ON k1,k2 AS dst
  DERIVE src -> dst SET col[单位] = <表达式>
  UPDATE src -> dst SET col[单位] = <表达式>      # 局部更新，col 必须已存在
  GROUP  src -> dst BY k1,k2 AGG out=sum(col), m=mean(x)
  SORT   src -> dst BY col
  SAVE   src TO path
  PLOT   src x=col series=a,b AS path [TITLE 标题]

表达式是 Python 的安全子集（ast 白名单）：列名即变量，
支持 + - * /、比较、and/or/not、括号与数值/字符串常量；
禁止函数调用、属性访问和一切导入，无法触碰宿主环境。
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import table as T

AGG = {"sum", "mean", "max", "min", "count"}


@dataclass
class Statement:
    verb: str
    args: Dict[str, Any]
    raw: str
    lineno: int


@dataclass
class Program:
    statements: List[Statement] = field(default_factory=list)


# ---------------------------------------------------------------- 解析
def parse(source: str) -> Program:
    prog = Program()
    for lineno, raw_line in enumerate(source.splitlines(), 1):
        line = raw_line.split("//", 1)[0].strip()
        if not line:
            continue
        prog.statements.append(_parse_line(line, lineno))
    return prog


def _parse_line(line: str, lineno: int) -> Statement:
    head = line.split(None, 1)[0].upper()
    parser = PARSERS.get(head)
    if parser is None:
        raise SyntaxError(f"第 {lineno} 行：无法识别的语句 {head!r}：{line}")
    return parser(line, lineno)


def _load(line, lineno):
    m = re.fullmatch(r"LOAD\s+(\S+)\s+AS\s+([A-Za-z_]\w*)(?:\s+UNITS\s+(.+))?", line)
    if not m:
        raise SyntaxError(f"第 {lineno} 行 LOAD 语法：{line}")
    units = None
    if m.group(3):
        units = dict(kv.split("=", 1) for kv in m.group(3).split(","))
    return Statement("LOAD", {"path": m.group(1), "name": m.group(2),
                              "units": units}, line, lineno)


def _filter(line, lineno):
    m = re.fullmatch(r"FILTER\s+(\w+)\s*->\s*(\w+)\s+WHERE\s+(.+)", line)
    if not m:
        raise SyntaxError(f"第 {lineno} 行 FILTER 语法：{line}")
    return Statement("FILTER", {"src": m.group(1), "dst": m.group(2),
                                "expr": m.group(3)}, line, lineno)


def _join(line, lineno):
    m = re.fullmatch(r"JOIN\s+(\w+)\s+WITH\s+(\w+)\s+ON\s+([\w,]+)\s+AS\s+(\w+)", line)
    if not m:
        raise SyntaxError(f"第 {lineno} 行 JOIN 语法：{line}")
    return Statement("JOIN", {"left": m.group(1), "right": m.group(2),
                              "keys": m.group(3).split(","), "dst": m.group(4)},
                     line, lineno)


def _derive(line, lineno, verb):
    m = re.fullmatch(
        r"(?:DERIVE|UPDATE)\s+(\w+)\s*->\s*(\w+)\s+SET\s+(\w+)(?:\[([^\]]+)\])?\s*=\s*(.+)",
        line)
    if not m:
        raise SyntaxError(f"第 {lineno} 行 {verb} 语法：{line}")
    return Statement(verb, {"src": m.group(1), "dst": m.group(2), "col": m.group(3),
                            "unit": m.group(4), "expr": m.group(5)}, line, lineno)


def _group(line, lineno):
    m = re.fullmatch(r"GROUP\s+(\w+)\s*->\s*(\w+)\s+BY\s+([\w,]+)\s+AGG\s+(.+)", line)
    if not m:
        raise SyntaxError(f"第 {lineno} 行 GROUP 语法：{line}")
    aggs = []
    for item in m.group(4).split(","):
        am = re.fullmatch(r"\s*(\w+)\s*=\s*(\w+)\((\w+)\)\s*", item)
        if not am or am.group(2) not in AGG:
            raise SyntaxError(f"第 {lineno} 行聚合规格无效: {item}")
        aggs.append((am.group(1), am.group(3), am.group(2)))
    return Statement("GROUP", {"src": m.group(1), "dst": m.group(2),
                               "keys": m.group(3).split(","), "aggs": aggs},
                     line, lineno)


def _sort(line, lineno):
    m = re.fullmatch(r"SORT\s+(\w+)\s*->\s*(\w+)\s+BY\s+(\w+)", line)
    if not m:
        raise SyntaxError(f"第 {lineno} 行 SORT 语法：{line}")
    return Statement("SORT", {"src": m.group(1), "dst": m.group(2),
                              "col": m.group(3)}, line, lineno)


def _save(line, lineno):
    m = re.fullmatch(r"SAVE\s+(\w+)\s+TO\s+(\S+)", line)
    if not m:
        raise SyntaxError(f"第 {lineno} 行 SAVE 语法：{line}")
    return Statement("SAVE", {"src": m.group(1), "path": m.group(2)}, line, lineno)


def _plot(line, lineno):
    m = re.fullmatch(
        r"PLOT\s+(\w+)\s+x=(\w+)\s+series=([\w,]+)\s+AS\s+(\S+)(?:\s+TITLE\s+(.+))?", line)
    if not m:
        raise SyntaxError(f"第 {lineno} 行 PLOT 语法：{line}")
    return Statement("PLOT", {"src": m.group(1), "x": m.group(2),
                              "series": m.group(3).split(","),
                              "path": m.group(4), "title": m.group(5)}, line, lineno)


PARSERS = {
    "LOAD": _load, "FILTER": _filter, "JOIN": _join,
    "DERIVE": lambda l, n: _derive(l, n, "DERIVE"),
    "UPDATE": lambda l, n: _derive(l, n, "UPDATE"),
    "GROUP": _group, "SORT": _sort, "SAVE": _save, "PLOT": _plot,
}


# ---------------------------------------------------------------- 安全求值
_ALLOWED = {
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Compare, ast.BoolOp,
    ast.Constant, ast.Name, ast.Load,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.USub,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
    ast.And, ast.Or, ast.Not,
}


def _check(node: ast.AST) -> None:
    if type(node) not in _ALLOWED:
        raise ValueError(f"表达式中禁止使用 {type(node).__name__}")
    for child in ast.iter_child_nodes(node):
        _check(child)


def evaluate(expr: str, row: Dict[str, Any]) -> Any:
    tree = ast.parse(expr, mode="eval")
    _check(tree)

    def ev(node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id not in row:
                raise KeyError(f"表达式引用了不存在的列: {node.id}")
            return row[node.id]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -ev(node.operand)
        if isinstance(node, ast.BinOp):
            a, b = ev(node.left), ev(node.right)
            op = node.op
            if isinstance(op, ast.Add):
                return a + b
            if isinstance(op, ast.Sub):
                return a - b
            if isinstance(op, ast.Mult):
                return a * b
            if isinstance(op, ast.Div):
                return a / b
        if isinstance(node, ast.Compare):
            left = ev(node.left)
            for op, comp in zip(node.ops, node.comparators):
                right = ev(comp)
                ok = {
                    ast.Eq: left == right, ast.NotEq: left != right,
                    ast.Lt: left < right, ast.LtE: left <= right,
                    ast.Gt: left > right, ast.GtE: left >= right,
                }[type(op)]
                if not ok:
                    return False
                left = right
            return True
        if isinstance(node, ast.BoolOp):
            vals = [ev(v) for v in node.values]
            return all(vals) if isinstance(node.op, ast.And) else any(vals)
        raise ValueError(f"不支持的节点 {type(node).__name__}")

    return ev(tree)


# ---------------------------------------------------------------- 维度推断
def infer_dimension(expr: str, tbl: "T.Table") -> Optional[str]:
    """按列单位推断表达式结果维度，做 +/- 同维度校验；纯数返回 None。"""
    tree = ast.parse(expr, mode="eval")
    _check(tree)

    def dim(node: ast.AST) -> Optional[str]:
        if isinstance(node, ast.Expression):
            return dim(node.body)
        if isinstance(node, ast.Constant):
            return None
        if isinstance(node, ast.Name):
            return tbl.unit_dim(node.id)
        if isinstance(node, ast.UnaryOp):
            return dim(node.operand)
        if isinstance(node, ast.BinOp):
            da, db = dim(node.left), dim(node.right)
            if isinstance(node.op, (ast.Add, ast.Sub)):
                if da and db and da != db:
                    raise ValueError(f"表达式维度冲突，不能相加: {da} 与 {db}")
                return da or db
            if isinstance(node.op, ast.Mult):
                if da and db:
                    return f"{da}*{db}"
                return da or db
            if isinstance(node.op, ast.Div):
                if da and db:
                    return T.DIM_DIV.get((da, db), f"{da}/{db}")
                return da
        if isinstance(node, ast.Compare):
            return None
        if isinstance(node, ast.BoolOp):
            return None
        raise ValueError(f"维度推断不支持 {type(node).__name__}")

    return dim(tree)
