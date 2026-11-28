# -*- coding: utf-8 -*-
"""
run_demo.py —— 端到端演练（记录端 + 保管室复算）。

阶段：
  R1 作者机第一轮：A、B 两本界面相同的笔记各跑一次（B 借用 tmp/ 上一轮汇总），
                   两图同高、同哈希；演示重复运行某格与同名变量覆写留痕。
  S  封存：各自形成最小可重演执行链；B 因 tmp/ 未随交而 NOT_SEALABLE。
  R2 复算室：随机换名临时目录 + 最小环境子进程，带正式勘误（仅一个同名列单位）
             与栏目单（用电重算、用水局部沿用）独立重算两个图。
  R3 补救：B 改用可纯重建单元边界后重新封存复算（作为新提交，原 B 仍无证）。
产物全部落在 out/ 与 archive/。
"""
from __future__ import annotations

import json
import os
import shutil

from rechain import demo
from rechain.engine import Engine
from rechain import sealing, recompute
from rechain.table import load_csv
from rechain.charts import ascii_chart

ROOT = os.path.dirname(os.path.abspath(__file__))
NB = os.path.join(ROOT, "notebooks")
ARCH = os.path.join(ROOT, "archive")
OUT = os.path.join(ROOT, "out")


def p(*x):
    print("\n" + "=" * 78 + "\n" + " ".join(str(i) for i in x) + "\n" + "=" * 78)


def load_nb(name):
    return json.load(open(os.path.join(NB, name), encoding="utf-8"))


def dump(obj, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------- R1 作者机
def round1_author():
    p("R1 作者机第一轮：两本笔记各跑一次（环境相同，B 的 tmp 汇总来自 A 本轮产物）")
    nb_a, nb_b = load_nb("notebook_a.json"), load_nb("notebook_b.json")

    eng_a = Engine(notebook_dir=NB)
    eng_a.run_notebook(nb_a)
    # 允许重复运行某格：再跑一次 c5，同名变量 region_agg 覆写留痕
    rerun_steps = eng_a.rerun_cell(nb_a, "c5")
    man_a = eng_a.manifest(nb_a["id"], "round1-author", nb_a)
    man_a["rerun_demo"] = [
        {"seq": s.seq, "cell": s.cell_id, "output": s.output,
         "note": s.note} for s in rerun_steps]
    dump(man_a, os.path.join(ARCH, "round1_manifest_A.json"))

    # B 借用的“上一轮汇总”：把 A 的带版本中间表放进 tmp（作者机上恰好存在）
    shutil.copy2(os.path.join(NB, "gen", "monthly_trend_v1.csv"),
                 os.path.join(NB, "tmp", "last_round_monthly.csv"))
    eng_b = Engine(notebook_dir=NB)
    eng_b.run_notebook(nb_b)
    man_b = eng_b.manifest(nb_b["id"], "round1-author", nb_b)
    dump(man_b, os.path.join(ARCH, "round1_manifest_B.json"))

    ha = man_a["file_accesses"]
    svg_a = next(a for a in ha if a["rel_path"] == "out/trend_chart.svg")["sha256"]
    svg_b = next(a for a in eng_b.accesses
                 if a.rel_path == "out/trend_chart.svg").sha256
    print(f"A 图 sha256 = {svg_a}")
    print(f"B 图 sha256 = {svg_b}")
    print("两图同高且同哈希：", svg_a == svg_b)
    return man_a, man_b, svg_a == svg_b


# ---------------------------------------------------------------- S 封存
def seal_both():
    p("S 封存最小可重演执行链（tmp/ 不随交，验证 B 断链）")
    pkg = os.path.join(ROOT, "rechain")
    res = {}
    for nb_file, tag in (("notebook_a.json", "A"), ("notebook_b.json", "B")):
        bundle = os.path.join(ARCH, f"bundle_{tag}")
        if os.path.exists(bundle):
            shutil.rmtree(bundle)
        m = sealing.seal(os.path.join(NB, nb_file), bundle, pkg)
        dump(m, os.path.join(ARCH, f"seal_manifest_{tag}.json"))
        res[tag] = m
        print(f"[{tag}] {m['verdict']}")
        for miss in m["missing_dependencies"]:
            print("   缺失依赖:", miss["rel_path"], "—", miss["reason"])
    return res


# ---------------------------------------------------------------- R2 复算室
def round2_recompute(man_a_r1):
    p("R2 复算室：随机临时目录 + 最小环境子进程，带勘误与栏目单独立重算两个图")
    erratum = os.path.join(ARCH, "erratum_ER-2026-001.json")
    sheet = demo.build_column_sheet(man_a_r1)

    results = {}
    for tag in ("A", "B"):
        bundle = os.path.join(ARCH, f"bundle_{tag}")
        nb_file = f"notebook_{tag.lower()}.json"
        r = recompute.independent_recompute(
            bundle, nb_file, label="round2-recompute",
            erratum=erratum, carry=sheet)
        dump(r, os.path.join(ARCH, f"round2_result_{tag}.json"))
        results[tag] = r
        print(f"[{tag}] returncode={r['_returncode']} signed={r['signed']} "
              f"workdir={os.path.basename(r['_workdir'])}")
        if r["failure"]:
            print("   断链点:", r["failure"]["type"], r["failure"]["message"])
    # 栏目沿用核对（依据第一轮封存哈希）
    sheet_doc = json.load(open(sheet, encoding="utf-8"))
    carry_verdicts = recompute.verify_carry(man_a_r1, sheet_doc)
    dump(carry_verdicts, os.path.join(ARCH, "carry_verdicts.json"))
    for v in carry_verdicts:
        print("栏目沿用核对:", v)
    return results, sheet_doc, carry_verdicts


# ---------------------------------------------------------------- R3 补救 B
def remediate_b():
    p("R3 补救：B 作者提前声明可纯重建单元边界（c8 改为装入自生成中间表）")
    nb_b_fixed = load_nb("notebook_b.json")
    nb_b_fixed["id"] = "NB-B-2026-07-REMEDIATED"
    for cell in nb_b_fixed["cells"]:
        if cell["id"] == "c8":
            cell["code"] = ("// 格8【补救版】装入本笔记前格 c7 自生成的带版本中间表\n"
                            "LOAD gen/monthly_trend_v1.csv AS trend_reloaded")
    fixed_path = os.path.join(NB, "notebook_b_remediated.json")
    dump(nb_b_fixed, fixed_path)
    bundle = os.path.join(ARCH, "bundle_B_remediated")
    if os.path.exists(bundle):
        shutil.rmtree(bundle)
    seal_m = sealing.seal(fixed_path, bundle, os.path.join(ROOT, "rechain"))
    dump(seal_m, os.path.join(ARCH, "seal_manifest_B_remediated.json"))
    r = recompute.independent_recompute(
        bundle, "notebook_b_remediated.json", label="round2-remediated",
        erratum=os.path.join(ARCH, "erratum_ER-2026-001.json"))
    dump(r, os.path.join(ARCH, "round2_result_B_remediated.json"))
    print("补救版 signed =", r["signed"], "| 结论:", seal_m["verdict"])
    return r


# ---------------------------------------------------------------- 交付物收集
def collect_deliverables(r2):
    p("收集按申请重算交付的图和表（从复算临时目录取回 out/）")
    delivered = os.path.join(OUT, "delivered_round2")
    if os.path.exists(delivered):
        shutil.rmtree(delivered)
    os.makedirs(delivered, exist_ok=True)
    # A：洁净重跑成功，产物在其临时工作目录
    workdir_a = r2["A"]["_workdir"]
    src_out = os.path.join(workdir_a, "bundle", "notebook_dir", "out")
    for f in os.listdir(src_out):
        shutil.copy2(os.path.join(src_out, f), os.path.join(delivered, f))
    print("交付目录:", delivered, "->", sorted(os.listdir(delivered)))

    # 用水单栏目单独保留演示：从交付表中抽出仍合规的 water_m3 序列
    trend = load_csv(os.path.join(delivered, "trend_table.csv"), name="trend")
    water_only = trend.copy("water_series_retained")
    for c in [c for c in water_only.columns if c not in ("month", "region", "water_m3")]:
        idx = water_only.col_index(c)
        water_only.columns.pop(idx)
        water_only.rows = [r[:idx] + r[idx + 1:] for r in water_only.rows]
        water_only.units.pop(c)
    from rechain.table import save_csv
    save_csv(water_only, os.path.join(delivered, "water_series_retained.csv"))

    # 终端 ASCII 等高对照（无界面也可核对）
    trend_sorted = trend.sort("trend_sorted", "month")
    ascii_txt = ascii_chart(trend_sorted, "month",
                            ["electricity_kwh", "water_m3"])
    with open(os.path.join(delivered, "ascii_chart.txt"), "w", encoding="utf-8") as f:
        f.write(ascii_txt + "\n")
    print("\n" + ascii_txt)
    return delivered


def round1_vs_round2(man_a_r1, r2):
    p("第一轮 vs 勘误后第二轮：图表哈希对照（勘误后应当分开）")
    cmp_doc = recompute.compare_runs(man_a_r1, r2["A"])
    dump(cmp_doc, os.path.join(ARCH, "round1_vs_round2_A.json"))
    for row in cmp_doc["comparison"]:
        print(row)
    return cmp_doc


# ---------------------------------------------------------------- 签认报告
def write_final_report(same_r1, seals, r2, carry_verdicts, remediated, cmp_doc):
    p("复算室签认报告")
    a_ok = r2["A"]["signed"]
    b_fail = r2["B"]["failure"]
    lines = []
    lines.append("# 复算室独立复算与签认报告\n")
    lines.append("## 一、申请事项")
    lines.append("两本界面相同的分析笔记（NB-A、NB-B）显示同一张南区月度趋势图，")
    lines.append("复算室在独立环境实际重算两个图，并按正式勘误 ER-2026-001（仅修正")
    lines.append("electricity_kwh 一个同名列的计量单位：kWh→Wh，同属 energy 维度）重算。\n")
    lines.append("## 二、第一轮（作者机屏上结果）")
    lines.append(f"- 两本笔记产出 trend_chart.svg 内容哈希一致、面板同高：{same_r1}")
    lines.append("- 但‘屏上恰好得到’不等于‘可从所交资料重现’，须分别审查执行链。\n")
    lines.append("## 三、封存与独立复算结论")
    lines.append(f"- NB-A：{seals['A']['verdict']}；随机临时目录洁净重跑"
                 f"（工作目录 {os.path.basename(r2['A']['_workdir'])}）成功，"
                 f"全部输入哈希与封存记录匹配，signed={a_ok}。")
    lines.append(f"- NB-B：{seals['B']['verdict']}；c8 装入 tmp/last_round_monthly.csv，")
    lines.append(f"  该上一轮汇总未随资料提交，子进程洁净重跑断链：{b_fail['type']} — {b_fail['message']}。")
    lines.append(f"  复算室无法从所交资料重现其图，signed={r2['B']['signed']}，**无证签认**。\n")
    lines.append("## 四、勘误后两图应当分开")
    for row in cmp_doc["comparison"]:
        lines.append(f"- {row['rel_path']}: 第一轮={str(row['round1_sha256'])[:16]} "
                     f"第二轮={str(row['round2_sha256'])[:16]} changed={row['changed']}")
    lines.append("\n## 五、只换一个栏目口径，另一序列单独保留")
    for v in carry_verdicts:
        lines.append(f"- {v['column']}: {v['verdict']}（证据哈希 {str(v['evidence_hash'])[:16]}）")
    lines.append("- 用电 electricity_kwh：按现行口径重算；用水 water_m3：口径未变，")
    lines.append("  凭第一轮封存哈希核对一致后局部沿用，并已另存 water_series_retained.csv。\n")
    lines.append("## 六、最终答复：能签认的是哪一次计算？")
    lines.append("**只有 NB-A 在复算室随机临时目录中、按勘误后现行口径完成的洁净重算可签认。**")
    lines.append("NB-B 第一轮屏上图像虽与 A 同高同哈希，但其关键单元借用未随交的上一轮")
    lines.append("内存文件，所交资料不足以重现，不予签认；其补救版（c8 改为自生成中间表）")
    lines.append(f"作为新提交重跑通过 signed={remediated['signed']}，不回溯豁免原提交。")
    report = "\n".join(lines)
    with open(os.path.join(ARCH, "SIGNOFF_REPORT.md"), "w", encoding="utf-8") as f:
        f.write(report)
    print(report)
    return report


def main():
    demo.reset_dirs()
    demo.build_raw()
    demo.build_notebooks()
    demo.build_erratum()

    man_a_r1, man_b_r1, same_r1 = round1_author()
    seals = seal_both()
    r2, sheet_doc, carry_verdicts = round2_recompute(man_a_r1)
    remediated = remediate_b()
    cmp_doc = round1_vs_round2(man_a_r1, r2)
    collect_deliverables(r2)
    write_final_report(same_r1, seals, r2, carry_verdicts, remediated, cmp_doc)
    print("\n全部产物目录：archive/ 与 out/delivered_round2/")


if __name__ == "__main__":
    main()
