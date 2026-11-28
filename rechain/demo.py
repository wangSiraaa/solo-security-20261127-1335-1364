# -*- coding: utf-8 -*-
"""
demo.build_all —— 生成合成教学资料、两本界面相同的笔记本及正式文书。

场景（小型数据运算语言 + 合成表即可）：
  南区逐月用电、用水两张带单位纵列的表；按 月+区域 连接后出趋势图。
  原始用电表计量单位声明有误（表头写 kWh，实际抄表为 Wh），
  复算室随后下达只改这一个同名列单位的正式勘误。
两本笔记界面（cell id / 代码格 / 声明输出）完全相同，仅 c8 不同：
  A：用相对路径重新装入“前格自己生成”的带版本中间表（可重演）；
  B：装入 tmp/ 里上一轮汇总（借用旧内存，资料封存时缺失）。
"""
from __future__ import annotations

import json
import os
import shutil

from rechain.table import Table, save_csv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NB_DIR = os.path.join(ROOT, "notebooks")

# 月份固定，保证两本笔记、两轮复算完全可重演
MONTHS = ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"]
ELEC_SOUTH = [420000, 435000, 410000, 395000, 405000, 430000]      # 实际为 Wh
WATER_SOUTH = [910, 925, 890, 860, 875, 905]                       # m3
ELEC_EAST = [500000, 510000, 498000, 487000, 492000, 515000]       # 过滤项
WATER_EAST = [1020, 1040, 1005, 980, 995, 1030]
WATER_NORTH = [760, 770, 755, 740, 748, 765]                       # 无用电记录，连接淘汰


def _write(name: str, path: str, columns, rows, units, version) -> Table:
    t = Table(name=name, columns=columns, rows=rows,
              units=dict(units), meta={"version": version})
    save_csv(t, path)
    return t


def build_raw():
    raw = os.path.join(ROOT, "data", "raw")
    os.makedirs(raw, exist_ok=True)
    elec_rows, water_rows = [], []
    for i, m in enumerate(MONTHS):
        elec_rows.append([m, "South", ELEC_SOUTH[i]])
        elec_rows.append([m, "East", ELEC_EAST[i]])
        water_rows.append([m, "South", WATER_SOUTH[i]])
        water_rows.append([m, "East", WATER_EAST[i]])
        water_rows.append([m, "North", WATER_NORTH[i]])
    # 注意：elec 表头声明 kWh，但数值实际以 Wh 抄表 —— 待勘误的同名列
    elec = _write("elec_raw", os.path.join(raw, "elec_monthly.csv"),
                  ["month", "region", "electricity_kwh"], elec_rows,
                  {"month": None, "region": None, "electricity_kwh": "kWh"},
                  "elec_monthly/v1.0")
    water = _write("water_raw", os.path.join(raw, "water_monthly.csv"),
                   ["month", "region", "water_m3"], water_rows,
                   {"month": None, "region": None, "water_m3": "m3"},
                   "water_monthly/v1.2")
    return elec, water


CELLS_A = [
    ("c1", "// 格1：装入带版本原始表（相对路径，单位随列声明）",
     "LOAD ../data/raw/elec_monthly.csv AS elec_raw\n"
     "LOAD ../data/raw/water_monthly.csv AS water_raw"),
    ("c2", "// 格2：过滤，只保留南区教学口径",
     "FILTER elec_raw -> elec_south WHERE region == 'South'\n"
     "FILTER water_raw -> water_south WHERE region == 'South'"),
    ("c3", "// 格3：按月+区域连接（北区无用电记录，自然淘汰）",
     "JOIN elec_south WITH water_south ON month,region AS joined"),
    ("c4", "// 格4：派生单位强度纵列（维度 energy/volume 自动推断；单位随勘误口径为 Wh/m3）",
     "DERIVE joined -> enriched SET intensity_wh_per_m3[Wh/m3] "
     "= electricity_kwh / water_m3"),
    ("c5", "// 格5：按区域分组汇总（可重复运行，结果确定）",
     "GROUP enriched -> region_agg BY region AGG "
     "elec_sum=sum(electricity_kwh), water_sum=sum(water_m3), months=count(month)"),
    ("c6", "// 格6：按月份排序形成月度趋势表",
     "SORT enriched -> monthly_trend BY month"),
    ("c7", "// 格7：把月度趋势表存成带版本的中间依赖（前格生成、后格装入）",
     "SAVE monthly_trend TO gen/monthly_trend_v1.csv"),
    ("c8", "// 格8【A 版】重新装入自己前格生成的带版本中间表",
     "LOAD gen/monthly_trend_v1.csv AS trend_reloaded"),
    ("c9", "// 格9：保存提报表",
     "SAVE trend_reloaded TO out/trend_table.csv\n"
     "SAVE region_agg TO out/region_summary.csv"),
    ("c10", "// 格10：出提报趋势图（电、水各一面板，面板同高）",
     "PLOT trend_reloaded x=month series=electricity_kwh,water_m3 "
     "AS out/trend_chart.svg TITLE 南区月度用电与用水趋势"),
]

CELLS_B = [
    (cid, comment, code if cid != "c8" else
     "// 格8【B 版】装入上一轮汇总（借用旧内存；tmp/ 不随资料封存）\n"
     "LOAD tmp/last_round_monthly.csv AS trend_reloaded")
    for cid, comment, code in CELLS_A
]

INTERFACE = {
    "cell_ids": [c[0] for c in CELLS_A],
    "declared_outputs": [
        {"rel_path": "out/trend_chart.svg", "kind": "chart"},
        {"rel_path": "out/trend_table.csv", "kind": "table"},
        {"rel_path": "out/region_summary.csv", "kind": "table"},
    ],
}


def _nb(nb_id, title, cells):
    return {
        "id": nb_id,
        "title": title,
        "interface": INTERFACE,
        "declared_outputs": INTERFACE["declared_outputs"],
        "cells": [
            {"id": cid, "comment": comment, "code": code}
            for cid, comment, code in cells
        ],
    }


def build_notebooks():
    os.makedirs(NB_DIR, exist_ok=True)
    for d in ("gen", "out", "tmp"):
        os.makedirs(os.path.join(NB_DIR, d), exist_ok=True)
    a = _nb("NB-A-2026-07", "南区公用工程趋势分析·记录本A（可重演链）", CELLS_A)
    b = _nb("NB-B-2026-07", "南区公用工程趋势分析·记录本B（借用上一轮汇总）", CELLS_B)
    json.dump(a, open(os.path.join(NB_DIR, "notebook_a.json"), "w",
                      encoding="utf-8"), ensure_ascii=False, indent=2)
    json.dump(b, open(os.path.join(NB_DIR, "notebook_b.json"), "w",
                      encoding="utf-8"), ensure_ascii=False, indent=2)
    return a, b


def build_erratum():
    """正式勘误：只修正同名字段 electricity_kwh 的一个计量单位。"""
    doc = {
        "erratum_id": "ER-2026-001",
        "title": "关于 elec_monthly.csv 中 electricity_kwh 列计量单位的正式勘误",
        "scope": "仅一个同名列的计量单位；数据数值与其他栏目一律不动",
        "unit_overrides": {
            "../data/raw/elec_monthly.csv": {"electricity_kwh": "Wh"}
        },
        "before": {"column": "electricity_kwh", "declared_unit": "kWh",
                   "dimension": "energy"},
        "after": {"column": "electricity_kwh", "corrected_unit": "Wh",
                  "dimension": "energy"},
        "note": "同维度单位修正，基准换算由 ×1000 改为 ×1；用水栏目不在勘误范围。",
    }
    p = os.path.join(ROOT, "archive", "erratum_ER-2026-001.json")
    json.dump(doc, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return p


def build_column_sheet(round1_manifest_a):
    """栏目单：用电按现行口径重算；用水允许局部沿用（附第一轮证据哈希）。"""
    lineage = {c["variable"]: c
               for c in round1_manifest_a["outputs"]["column_lineage"]}
    water_hash = lineage["trend_reloaded"]["content_hash"]
    doc = {
        "sheet_id": "CS-2026-001",
        "rule": "仅换一个栏目的口径；原图中另一条仍合规的序列可单独保留",
        "columns": [
            {"name": "electricity_kwh", "policy": "recompute",
             "reason": "勘误 ER-2026-001 修正计量单位，必须按现行口径重算"},
            {"name": "water_m3", "policy": "carry",
             "evidence_hash": water_hash,
             "evidence_source": "round1/NB-A 月度趋势表内容哈希",
             "reason": "用水不在勘误范围，口径未变，允许局部沿用"},
            {"name": "intensity_wh_per_m3", "policy": "recompute",
             "reason": "派生列含勘误栏目，随重算"},
        ],
    }
    p = os.path.join(ROOT, "archive", "column_sheet_CS-2026-001.json")
    json.dump(doc, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return p


def reset_dirs():
    for d in (os.path.join(ROOT, "out"), os.path.join(ROOT, "archive")):
        if os.path.exists(d):
            shutil.rmtree(d)
        os.makedirs(d, exist_ok=True)
    for sub in ("gen", "out", "tmp"):
        p = os.path.join(NB_DIR, sub)
        if os.path.exists(p):
            shutil.rmtree(p)
        os.makedirs(p, exist_ok=True)


if __name__ == "__main__":
    reset_dirs()
    build_raw()
    build_notebooks()
    build_erratum()
    print("合成资料与两本笔记已生成于", ROOT)
