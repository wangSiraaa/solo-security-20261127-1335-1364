"""命令行入口：analyze / deliver / replay / check，全程离线。"""
from __future__ import annotations

import argparse
import json
import sys

from .analyzer import analyze
from .deliver import build_config, load_config, save_config, verify_fingerprints
from .interpreter import ReplayError, replay
from .model import ISOLATED, SHARED
from .parser import parse_board_file, parse_program_file


def _print_analysis(result) -> None:
    print(f"程序 {result.program.name} 的静态分析结果"
          + (f"（板卡 {result.board.name}）" if result.board else "（未绑定板卡）"))
    print("=" * 72)
    for mr in result.mode_reports.values():
        print(f"[模式 {mr.name}] 入口 {mr.entry}  前台峰值 {mr.fg_peak}B  "
              f"前台执行 {mr.fg_time}us")
        print(f"    最长封闭区间: high {mr.max_closed['high']}us / "
              f"low {mr.max_closed['low']}us")
        print(f"    开放中断点 {len(mr.open_points)} 个:")
        for op in mr.open_points:
            print(f"      - {op.op_id}  开放={','.join(op.mask) or '无'}")
    print("-" * 72)
    for lr in result.level_reports.values():
        verdict = "—" if lr.meets_deadline is None else ("达标" if lr.meets_deadline else "违约")
        print(f"[中断 {lr.level}] handler={lr.handler} 到达上界 {lr.arrivals}/周期")
        print(f"    处理峰值 {lr.handler_peak}B  嵌套峰值 {lr.nested_peak}B  "
              f"处理时间 C={lr.handler_time}us  最长封闭 W={lr.max_closed}us")
        print(f"    最坏延迟 {lr.worst_latency}us  最坏响应 {lr.worst_response}us "
              f"最晚响应 {lr.deadline}us -> {verdict}")
    print("-" * 72)
    period_verdict = ("—" if result.meets_period is None
                      else "满足" if result.meets_period else "超载")
    print(f"每周期最少占用 {result.period_load}us / 采样周期 {result.period}us -> {period_verdict}")
    if result.plans:
        for plan in result.plans.values():
            print(f"[栈接法 {plan.model}] 需 {plan.peak}B，空闲总量 {plan.free_total}B")
            for r in plan.regions:
                where = "放不进" if r.start is None else f"[{r.start},{r.end})"
                print(f"    {r.name}: {r.size}B -> {where}")
            print(f"    结论: {'可装入' if plan.fits else '不可装入'}")
    print("=" * 72)
    if result.gaps:
        print("未获保证的具体缺口：")
        for g in result.gaps:
            print(f"  ✗ {g}")
    else:
        print("所有承诺（峰值/响应/周期/内存）均满足。")


def _split_board_ref(ref: str):
    """板卡引用形式：路径 或 路径:板卡名（仅在路径本身不存在时才按冒号拆分）。"""
    import os
    if not os.path.exists(ref) and ":" in ref:
        path, name = ref.rsplit(":", 1)
        return path, name
    return ref, None


def _cmd_analyze(args) -> int:
    prog = parse_program_file(args.program)
    board = parse_board_file(*_split_board_ref(args.board)) if args.board else None
    result = analyze(prog, board)
    _print_analysis(result)
    return 0 if result.ok else 2


def _cmd_deliver(args) -> int:
    prog = parse_program_file(args.program)
    board = parse_board_file(*_split_board_ref(args.board))
    result = analyze(prog, board)
    if result.gaps and not args.force:
        _print_analysis(result)
        print("\n存在缺口，拒绝交付。修复缺口或显式 --force（仍只能交付放得下的栈接法）。",
              file=sys.stderr)
        return 2
    try:
        cfg = build_config(result, args.stack_model)
    except ValueError as exc:
        print(f"交付失败: {exc}", file=sys.stderr)
        return 2
    save_config(cfg, args.out)
    print(f"已交付配置 {args.out}（栈接法 {args.stack_model}），"
          f"本地解释器可直接 replay；指纹已绑定程序与板卡源文件。")
    if result.gaps:
        print("注意：--force 交付，配置仍受限于已满足的内存/栈项，其余缺口见分析报告。",
              file=sys.stderr)
    return 0


def _cmd_replay(args) -> int:
    cfg = load_config(args.config)
    with open(args.events, "r", encoding="utf-8") as fh:
        event_list = json.load(fh)
    try:
        result = replay(cfg, event_list, mode=args.mode,
                        base_dir=args.base_dir)
    except ReplayError as exc:
        print(f"回放被拒绝: {exc}", file=sys.stderr)
        return 3
    print(f"回放记录（模式 {result.mode}，栈接法 {result.stack_model}）")
    print("-" * 72)
    for rec in result.records:
        print(f"#{rec.seq} {rec.level:4s} 到达={rec.arrival:5d}us "
              f"启动={rec.started if rec.started is not None else '-':>5} "
              f"完成={rec.finished if rec.finished is not None else '-':>5} "
              f"延迟={rec.latency if rec.latency is not None else '-':>5}us "
              f"响应={rec.response if rec.response is not None else '-':>5}us "
              f"[{rec.state}]")
    print(f"实测栈峰值 {result.peak_bytes}B / 配置容量 {result.capacity_bytes}B；"
          f"回放历时 {result.finished_at}us")
    if result.violations:
        print("回放违约：")
        for v in result.violations:
            print(f"  ✗ {v}")
    else:
        print("回放全部满足交付配置中的保证。")
    if args.json_out:
        data = {
            "ok": result.ok,
            "peak_bytes": result.peak_bytes,
            "capacity_bytes": result.capacity_bytes,
            "finished_at": result.finished_at,
            "records": [rec.__dict__ for rec in result.records],
            "violations": result.violations,
        }
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
    return 0 if result.ok else 2


def _cmd_check(args) -> int:
    cfg = load_config(args.config)
    problems = verify_fingerprints(cfg, base_dir=args.base_dir)
    if problems:
        for p in problems:
            print(f"✗ {p}")
        return 2
    print("✓ 程序与板卡指纹均与交付配置一致，峰值/响应保证仍然有效，可回放。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fwstack",
        description="完全离线的固件栈检查与装板工具（普通调用/互斥模式/两级标记中断）",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("analyze", help="静态分析程序（可带板卡），报告峰值/响应/内存缺口")
    p.add_argument("program")
    p.add_argument("board", nargs="?")
    p.set_defaults(func=_cmd_analyze)

    p = sub.add_parser("deliver", help="生成可回放的交付配置 JSON")
    p.add_argument("program")
    p.add_argument("board")
    p.add_argument("--stack-model", choices=[SHARED, ISOLATED], required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--force", action="store_true", help="有非内存缺口时仍尝试交付")
    p.set_defaults(func=_cmd_deliver)

    p = sub.add_parser("replay", help="用本地解释器回放短事件列，取回采样记录")
    p.add_argument("config")
    p.add_argument("events")
    p.add_argument("--mode")
    p.add_argument("--base-dir", default=".")
    p.add_argument("--json-out")
    p.set_defaults(func=_cmd_replay)

    p = sub.add_parser("check", help="校验交付配置的源文件指纹是否仍有效")
    p.add_argument("config")
    p.add_argument("--base-dir", default=".")
    p.set_defaults(func=_cmd_check)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
