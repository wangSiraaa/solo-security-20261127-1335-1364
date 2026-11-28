"""End-to-end demonstration (no real hardware; deterministic-ish).

Run:  python3 demo.py
Prints, for each required case, what the calibration room sees and what it is
allowed to put its name to.
"""

from __future__ import annotations

import json

from probestation.calibrate import default_curves, issue_report
from probestation.plan import preset_beats
from probestation.reconstruct import swap_witnesses
from probestation.rig import SLOT_LABELS, Session
from probestation.scenarios import (
    INSERTION_A,
    INSERTION_B,
    RAWS,
    curve_only_change,
    drop_one_cell,
    grounds,
    recovery_sheet,
    run_preplan,
    run_rollcall,
    scenario_a_unique_from_bijection,
    scenario_b_genuine_swap,
    sheet_with_checkable_ambiguity,
    stale_receipt_run,
)


def hr(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def show(recon) -> None:
    for s in recon.slots:
        if s in recon.fixed:
            ev = recon.evidence[s]
            print(f"  槽 {s}: {recon.fixed[s]:5s}  [依据={ev.kind}]")
        else:
            cand = recon.ambiguous[s]
            print(f"  槽 {s}: ??? 候选={list(cand)}  {recon.reasons.get(s, '')}")


def main() -> None:
    hr("0. 并行询问计划（进场前固定）")
    sheet = recovery_sheet()
    print(f"  探头数 {sheet.n_probes}，拍数 {sheet.n_beats}（少于探头数，逐个叫号超预算）")
    for b, call in sheet.beats:
        print(f"  第{b}拍 点名: {', '.join(call)}")
    print("  码字:", sheet.codewords)
    print("  保证:", sheet.guarantee)

    hr("1. 场景A：单槽仍符合两支，靠其余槽可靠应答 + 单槽单支 唯一确定")
    run_a = scenario_a_unique_from_bijection()
    r = run_a.reconstruction
    print("  原始候选域:", {s: list(r.raw_domains[s]) for s in r.slots})
    print("  槽甲 单独看:", list(r.raw_domains["甲"]), "-> 证据类型:", r.evidence["甲"].kind)
    show(r)
    print("  结论: 全部唯一确定 =", r.complete, "；与真实插入一致 =", r.fixed == INSERTION_A)

    hr("2. 场景B：甲、乙确实可对调，其余槽仍能定名")
    run_b = scenario_b_genuine_swap()
    rb = run_b.reconstruction
    show(rb)
    print("  歧义见证:", json.dumps(swap_witnesses(rb), ensure_ascii=False))
    rep = issue_report(run_b.readings(RAWS), rb, default_curves())
    print("  读数单:")
    for line in rep.lines:
        who = line.identity or "（未定名，仅保留原始读数）"
        conv = f"{line.converted:.2f}" if line.converted is not None else "-"
        print(f"    槽 {line.slot}: 原始={line.raw}  身份={who}  换算={conv}  依据={line.basis}")

    hr("3. 缺一格本次响应 -> 仍有依据；换回旧回单 -> 命名依据丢失")
    # drop one cell from a complete run
    full = run_preplan(INSERTION_B, Session.new(), "DEMO")
    d = drop_one_cell(full, 4, "甲")
    print("  移除一格后 槽甲 仍有依据:", grounds(d)["甲"], "；整体完成 =", d.complete)
    honest, stale = stale_receipt_run(INSERTION_B, "DEMO-ST")
    print("  旧回单(上一班) 用于本会话:")
    print("    诚实运行 完成 =", honest.reconstruction.complete)
    print("    旧回单运行 完成 =", stale.reconstruction.complete, " 原因:", stale.reconstruction.reasons)
    print("    旧回单运行 槽甲 依据仍在:", stale.reconstruction.grounds()["甲"])
    print("    其他槽:", {s: stale.reconstruction.fixed[s] for s in stale.reconstruction.fixed})
    # Restore attestation material: re-issue this session's receipt for P-A1.
    from probestation.scenarios import reenroll_same_roster

    degraded, restored = reenroll_same_roster(INSERTION_B)
    print("  恢复认证材料后:")
    print("    恢复前 完成 =", degraded.reconstruction.complete,
          " 槽甲原因 =", degraded.reconstruction.reasons.get("甲"))
    print("    恢复后 完成 =", restored.reconstruction.complete,
          " 归属不变 =", restored.reconstruction.fixed == INSERTION_B)

    hr("4. 仅换校准室自己的分段曲线：归属不变，换算值改变")
    rep_def, rep_alt = curve_only_change(full)
    print("  归属一致:", [l.identity for l in rep_def.lines] == [l.identity for l in rep_alt.lines])
    for a, b in zip(rep_def.lines, rep_alt.lines):
        if a.identity:
            print(f"    槽 {a.slot} {a.identity}: 默认={a.converted:.2f} -> 替换曲线={b.converted:.2f}")

    hr("5. 超出可承诺范围的局部（此处甲、丁各丢两拍）：留可查歧义，不硬猜")
    run_over, wit = sheet_with_checkable_ambiguity(INSERTION_B, {0: ("甲", "丁"), 2: ("甲", "丁")})
    ro = run_over.reconstruction
    show(ro)
    print("  歧义见证:", json.dumps(wit, ensure_ascii=False))
    for s in ro.fixed:
        assert ro.fixed[s] == INSERTION_B[s]
    print("  所有已定名槽均与真实插入一致 ✓")

    hr("6. 另一路：在场点名器（自适应），首拍全暗仍能补问认回")
    res, rc = run_rollcall(INSERTION_B, Session.new(),
                           "DEMO-R", budget=5, missing_by_beat={0: SLOT_LABELS})
    print("  拍数消耗:", len(res.turns), " 完成 =", rc.reconstruction.complete,
          " 与真实一致 =", rc.reconstruction.fixed == INSERTION_B)
    for t in res.turns:
        print(f"    第{t.beat}拍 点名 {', '.join(t.call)} -> 歧义槽 {sorted(t.recon.ambiguous)}")

    print("\n完成。")


if __name__ == "__main__":
    main()
