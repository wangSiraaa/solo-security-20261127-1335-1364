#!/usr/bin/env python3
"""三幕端到端演示，全程离线、仅用标准库。

幕一：从正常入口装入可采样配置，提交短事件列并取回采样记录。
幕二：只移动程序中的开放中断点，重算峰值/响应保证（旧配置指纹立即失效）。
幕三：恢复原程序，仅把板卡一段内存留给新外设，再查同一采样任务能否兑现。
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fwstack.analyzer import analyze
from fwstack.deliver import build_config, save_config, verify_fingerprints
from fwstack.interpreter import ReplayError, replay
from fwstack.model import ISOLATED, SHARED
from fwstack.parser import parse_board_file, parse_program_file

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROG = os.path.join(ROOT, "examples/programs/sampler.fw")
PROG_LATE = os.path.join(ROOT, "examples/programs/sampler_late.fw")
BOARDS = os.path.join(ROOT, "examples/boards/boards.fwboard")
EVENTS = os.path.join(ROOT, "examples/events_baseline.json")
OUT = os.path.join(ROOT, "build")
os.makedirs(OUT, exist_ok=True)


def hr(title: str) -> None:
    print("\n" + "=" * 76)
    print(title)
    print("=" * 76)


def summarize(tag: str, result) -> None:
    print(f"[{tag}] 每周期占用 {result.period_load}/{result.period}us")
    for lr in result.level_reports.values():
        verdict = "达标" if lr.meets_deadline else "违约"
        print(f"  {lr.level:4s} 封闭 W={lr.max_closed:5d}us  最坏响应 "
              f"{lr.worst_response:5d}us / 最晚 {lr.deadline}us -> {verdict}")
    for plan in result.plans.values():
        layout = ", ".join(
            f"{r.name} {r.size}B@{'放不进' if r.start is None else r.start}"
            for r in plan.regions
        )
        print(f"  栈接法 {plan.model:8s} 需 {plan.peak:4d}B  {layout}  "
              f"=> {'可装入' if plan.fits else '不可装入'}")
    if result.gaps:
        print("  缺口:")
        for g in result.gaps:
            print(f"    ✗ {g}")


def act_one() -> dict:
    hr("幕一：基线程序 + NANO 小板，从正常入口装入并回放短事件列")
    prog = parse_program_file(PROG)
    board = parse_board_file(BOARDS, "NANO")
    result = analyze(prog, board)
    summarize("分析", result)
    if result.gaps:
        raise SystemExit("基线组合本应全部达标，演示数据有误")

    # 装板员选共享栈接法（两种都可行，选更省固定内存的一种）
    cfg = build_config(result, SHARED)
    cfg_path = os.path.join(OUT, "nano_sampler_shared.json")
    save_config(cfg, cfg_path)
    print(f"\n已交付 {os.path.relpath(cfg_path, ROOT)}，栈接法={SHARED}，"
          f"程序/板卡指纹已写入配置")

    with open(EVENTS, encoding="utf-8") as fh:
        events = json.load(fh)
    rep = replay(cfg, events, mode="SAMPLE", base_dir=ROOT)
    print("\n采样记录：")
    for rec in rep.records:
        print(f"  #{rec.seq} {rec.level:4s} 到达 {rec.arrival:5d}us -> 启动 "
              f"{rec.started:5d} -> 完成 {rec.finished:5d}  延迟 {rec.latency}us "
              f"响应 {rec.response}us [{rec.state}]")
    print(f"实测峰值 {rep.peak_bytes}B（静态承诺 {cfg['guarantees']['shared_peak_bytes']}B），"
          f"回放 {'全部达标' if rep.ok else rep.violations}")
    return cfg


def act_two(cfg: dict) -> None:
    hr("幕二：只移动开放中断点（sampler_late.fw），原配置与保证如何变化")
    print("先校验幕一交付配置对当前（已改动的）程序源文件的指纹：")
    # 临时把配置指向变体源文件，模拟装板员手里的旧配置遇到新程序
    tampered = dict(cfg)
    tampered["program"] = dict(cfg["program"])
    tampered["program"]["path"] = os.path.relpath(PROG_LATE, ROOT)
    problems = verify_fingerprints(tampered, ROOT)
    for p in problems:
        print(f"  ✗ {p}")
    try:
        replay(tampered, [{"at": 100, "level": "high"}], base_dir=ROOT)
    except ReplayError as exc:
        print(f"  回放被拒绝: {exc}")

    late = parse_program_file(PROG_LATE)
    board = parse_board_file(BOARDS, "NANO")
    result = analyze(late, board)
    summarize("重算", result)
    print("\n结论：函数帧与 work 未变，故栈峰值不变；但封闭区间从 950us 拉长到 1440us，"
          "high 最坏响应 1640us 超过 1400us，该组合不再获保证，禁止据此交付。")
    try:
        build_config(result, SHARED)
    except ValueError as exc:
        print(f"交付拦截: {exc}")


def act_three() -> None:
    hr("幕三：恢复基线程序，仅给新外设留出一段内存（NANO_P），重查同一采样任务")
    prog = parse_program_file(PROG)
    board_p = parse_board_file(BOARDS, "NANO_P")
    print(f"空闲块: {board_p.free_gaps()}（总量 {sum(s for _, s in board_p.free_gaps())}B，"
          "保留区 periph_fifo 造成碎片）")
    result = analyze(prog, board_p)
    summarize("分析", result)
    shared_ok = result.plans[SHARED].fits
    isolated_ok = result.plans[ISOLATED].fits
    print(f"\n同一采样任务的装板结论：共享栈={'可装入' if shared_ok else '不可'}，"
          f"隔离栈={'可装入' if isolated_ok else '不可'}")
    if shared_ok and not isolated_ok:
        cfg = build_config(result, SHARED)
        path = os.path.join(OUT, "nano_p_sampler_shared.json")
        save_config(cfg, path)
        with open(EVENTS, encoding="utf-8") as fh:
            events = json.load(fh)
        rep = replay(cfg, events, mode="SAMPLE", base_dir=ROOT)
        print(f"已按共享栈交付 {os.path.relpath(path, ROOT)}；回放结果: "
              f"{'全部达标' if rep.ok else rep.violations}，实测峰值 {rep.peak_bytes}B")
        print("隔离栈的具体缺口已在上方列出：总量够但被保留区切碎，无法同时容纳两个栈区。")


if __name__ == "__main__":
    cfg = act_one()
    act_two(cfg)
    act_three()
    hr("演示结束")
