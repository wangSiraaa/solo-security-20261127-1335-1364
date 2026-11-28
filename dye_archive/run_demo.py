"""端到端离线演示：
1) 基线（宽校正窗）下判定五管可领性，出具两支可领留样的正常申请；
2) 外地实验室独立核源、按最坏允许变化调两张模拟色样、独立比色台验收；
3) 缩窄参考浴更正限 -> 救出 T-03；还原 T-04 履历 -> 核源通过但仍超热预算；
4) 单换 B 分液器步长（0.02 -> 0.14 mL），两张共用基液的复验单按实际混色/耗量分别办结；
5) 封存件防拆演示：改一个旧读数或误贴回单，收件人核验立即失败。
"""
from __future__ import annotations

import json

from . import color
from .application import build_application
from .archive import (BUDGET_EQH, apply_narrow_bath, build_world,
                      leadability, restore_t04_receipt, worst_exposure)
from .lab import (DELTA_E_LIMIT, ExternalLab, RecheckOrder,
                  independently_verify, packet_for)

SIGNED_DOSES = {
    "RV-101": {"A": 0.24, "B": 0.36},
    "RV-102": {"A": 0.50, "B": 0.46},
}
COLORANTS = ["A", "B"]


def target_cards() -> dict[str, tuple[float, float, float]]:
    # 比色台自持色卡：由已签配方在名义强度下的 Lab 生成
    return {
        "CARD-RV-101": color.lab_of(SIGNED_DOSES["RV-101"], {"A": 1.0, "B": 1.0}),
        "CARD-RV-102": color.lab_of(SIGNED_DOSES["RV-102"], {"A": 1.0, "B": 1.0}),
    }


def make_orders(base_lot: str) -> list[RecheckOrder]:
    cards = target_cards()
    return [
        RecheckOrder("RV-101", "F1 品红-黄复配（虚构）", COLORANTS,
                     SIGNED_DOSES["RV-101"], cards["CARD-RV-101"], base_lot),
        RecheckOrder("RV-102", "F2 深调复配（虚构）", COLORANTS,
                     SIGNED_DOSES["RV-102"], cards["CARD-RV-102"], base_lot),
    ]


def eqh_map(world: dict, tubes: list[str]) -> dict[str, float]:
    return {t: worst_exposure(world, t)["eq_hours"] for t in tubes}


def section(title: str) -> str:
    return "\n" + "=" * 78 + f"\n{title}\n" + "=" * 78


def run() -> dict:
    out: dict = {"sections": []}
    world = build_world()
    all_tubes = list(world["tubes"].keys())

    # ---------- 1. 基线可领性 + 正常申请 ----------
    print(section("1. 基线（参考浴宽校正窗 ±0.80°C）逐管判定"))
    baseline = {}
    for t in all_tubes:
        r = leadability(world, t)
        baseline[t] = r
        flag = "可领" if r["leadable"] else "不可领"
        print(f"{t} [{world['tubes'][t]['colorant']}] {flag}  "
              f"等效小时={r['eq_hours']:.1f}/{BUDGET_EQH}  重建段={r['rebuilt_hours']}h")
        for why in r["reasons"]:
            print(f"    - {why}")
    out["baseline_leadability"] = baseline

    leadable = [t for t, r in baseline.items() if r["leadable"]]
    print(f"\n外部实验室还能领的复验基准：{leadable}（共 {len(leadable)} 管）")

    application = build_application(world, leadable)
    print(f"\n正常申请 {application['document_id']} -> {application['intake_decision']}")
    print(f"选择路线：{application['route_selected']}")
    out["application_id"] = application["document_id"]

    # ---------- 2. 外地实验室独立核源 + 实际调样 ----------
    print(section("2. 外地实验室 EXT-LAB-7：独立核源与模拟调样"))
    packets = {t: packet_for(world, t) for t in leadable}
    lab = ExternalLab("EXT-LAB-7", {"A": 0.02, "B": 0.02})
    verify_report = lab.receive(packets, eqh_map(world, leadable), target_cards())
    for t, v in verify_report.items():
        print(f"收件核源 {t}: {'通过' if v['ok'] else '失败 ' + str(v['problems'])}")
    tubes_ab = {"A": "T-01", "B": "T-02"}
    orders = make_orders("BL-07")
    first_results = []
    for order in orders:
        lab.run_order(order, tubes_ab)
        first_results.append(order)
        print(f"\n{order.order_id} {order.formula} -> {order.verdict}")
        for s in order.swatches:
            print(f"  色样 {s['tag']:10s} 剂量={s['doses_ml']} "
                  f"基液批次={s['base_lot']} ΔE00={s['delta_e00']:.3f}（限 {DELTA_E_LIMIT}）"
                  f" -> {'过' if s['pass'] else '超差'}")
        print(f"  累计耗量 染料={order.doses_used_ml} 本单基液="
              f"{order.base_used_ml}mL")
    out["first_round"] = [
        {"order": o.order_id, "verdict": o.verdict, "swatches": o.swatches,
         "doses_used_ml": o.doses_used_ml,
         "base_used_ml": o.base_used_ml} for o in first_results]
    print(f"\n基液 BL-07 余量：{lab.base_lots['BL-07']:.1f}mL；"
          f"T-01(A) 余 {lab.tube_stock['A']:.2f}mL，"
          f"T-02(B) 余 {lab.tube_stock['B']:.2f}mL")

    # ---------- 3a. 缩窄参考浴 -> T-03 可领 ----------
    print(section("3a. 追加 BTN-03：参考浴更正限缩窄至 ±0.10°C"))
    apply_narrow_bath(world)
    r3 = leadability(world, "T-03")
    print(f"T-03 等效小时 {r3['eq_hours']:.1f} -> {'可领' if r3['leadable'] else '不可领'} "
          f"（重建段 {r3['rebuilt_hours']}h，理由 {r3['reasons'] or '无'}）")
    out["t03_after_narrow"] = r3

    # ---------- 3b. 还原 T-04 履历 ----------
    print(section("3b. 还原 T-04 跨柜回单（撤回误贴的 T-07 真实回单）"))
    before = independently_verify(packet_for(world, "T-04"))
    print(f"还原前核源：{before}")
    restore_t04_receipt(world)
    after_packet = packet_for(world, "T-04")
    after = independently_verify(after_packet)
    r4 = leadability(world, "T-04")
    print(f"还原后核源：{'通过' if after['ok'] else after['problems']}")
    print(f"但最不利暴露 {r4['eq_hours']:.1f} 等效小时 vs 预算 {BUDGET_EQH} "
          f"-> {'仍不可领' if not r4['leadable'] else '可领'}：{r4['reasons'] or '无'}")
    out["t04_after_restore"] = {"verify": after, "leadability": r4}

    # ---------- 4. 单换 B 分液器步长：同一实验室续开，两张复验单分别办结 ----------
    print(section("4. 单换 B 分液器步长 0.02 -> 0.14 mL（A 不变），同实验室续开"))
    print("两张复验单须共享基液批次 BL-07；库存与基液台账沿用第一轮。")
    lab.dispenser.steps_ml["B"] = 0.14
    new_orders = make_orders("BL-07")
    second_results = []
    for order in new_orders:
        lab.run_order(order, tubes_ab)
        second_results.append(order)
        print(f"\n{order.order_id} -> {order.verdict}")
        for s in order.swatches:
            print(f"  色样 {s['tag']:10s} 量化后剂量={s['doses_ml']} "
                  f"基液批次={s['base_lot']} ΔE00={s['delta_e00']:.3f} "
                  f"-> {'过' if s['pass'] else '超差'}")
        print(f"  实际耗量 染料={order.doses_used_ml} 本单基液="
              f"{order.base_used_ml}mL")
    print("\n共享基液逐笔台账：")
    for entry in lab.base_ledger:
        print(f"  {entry['order']} <- {entry['base_lot']} {entry['ml']}mL")
    print(f"批次余量：BL-07={lab.base_lots['BL-07']:.1f}mL，"
          f"BL-08={lab.base_lots['BL-08']:.1f}mL；"
          f"A 余 {lab.tube_stock['A']:.2f}mL，B 余 {lab.tube_stock['B']:.2f}mL")
    out["second_round"] = [
        {"order": o.order_id, "verdict": o.verdict, "swatches": o.swatches,
         "doses_used_ml": o.doses_used_ml,
         "base_used_ml": o.base_used_ml} for o in second_results]
    out["base_ledger"] = lab.base_ledger
    out["base_lots_remaining"] = lab.base_lots
    out["dye_remaining_ml"] = lab.tube_stock

    # ---------- 5. 封存件防拆演示 ----------
    print(section("5. 封存件防拆/误贴核验演示"))
    good = independently_verify(packet_for(build_world(), "T-01"))
    print(f"未改动履历核源：{good}")

    tampered_world = build_world()
    env = tampered_world["probes"].envelopes[10]
    env["body"]["record"]["reading_c"] = 99.9
    bad = independently_verify(packet_for(tampered_world, "T-01"))
    print(f"改写一个已封读数后核源：{bad}")

    mislabeled = independently_verify(packet_for(build_world(), "T-04"))
    print(f"误贴他管真实回单核源：{mislabeled}")
    out["tamper_checks"] = {"clean": good, "altered_reading": bad,
                            "mislabeled_receipt": mislabeled}

    with open("demo_result.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print("\n完整结构化结果已写入 demo_result.json")
    return out


if __name__ == "__main__":
    run()
