#!/usr/bin/env python3
"""sealctl：流动服务队受保护报讯信道的命令行本地收发器。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .case import PRESETS, SealCase
from .errors import SealChannelError
from .models import ALPHA, BETA, TRIP_LABELS
from .planner import Planner
from .security import sha256_hex
from .verifier import body_receipt_value


STATUS_TEXT = {
    "recorded": "即时入册",
    "duplicate": "重送只补回条",
    "duplicate_pending": "重送仍待对账，只补回条",
    "pending_reconciliation": "过窗真信，待人工对账",
    "rejected": "核验失败/拒收",
    "conflict": "封条正文冲突",
}


def build_parser() -> argparse.ArgumentParser:
    default_state = os.environ.get("SEAL_CASE", ".seal-case")
    parser = argparse.ArgumentParser(
        prog="sealctl",
        description="两台离线报讯器的封条号分配、持久发信、验证与三号窗口收端",
    )
    # 同时支持 `sealctl --state X cmd` 和 `sealctl cmd --state X`。
    parser.add_argument("--state", "--data-dir", dest="state", default=default_state, help="本地状态目录")
    parser.add_argument("--json", action="store_true", default=False, help="输出 JSON")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--state",
        "--data-dir",
        dest="state",
        default=argparse.SUPPRESS,
        help="本地状态目录（默认 $SEAL_CASE 或 ./.seal-case）",
    )
    common.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="输出 JSON，便于脚本调用",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", parents=[common], aliases=["chu-shi-hua"], help="初始化一册 1-6 封条")
    p.add_argument("--book-size", type=int, default=6)
    p.add_argument("--window", type=int, default=3, dest="window_size")
    p.add_argument("--force", action="store_true", help="删除并重建状态目录")
    p.set_defaults(handler=cmd_init)

    p = sub.add_parser("apply", parents=[common], aliases=["shenqing", "request"], help="某台申请三枚封条")
    p.add_argument("--trip", "--owner", dest="trip", required=True, help="alpha/甲 或 beta/乙")
    p.add_argument("--seals", required=True, help="例如 1,3,6 或 1-3")
    p.set_defaults(handler=cmd_apply)

    p = sub.add_parser("approve", parents=[common], aliases=["pizhun", "grant"], help="批准申请并预置可信回执")
    p.add_argument("--trip", "--owner", dest="trip", required=True)
    p.set_defaults(handler=cmd_approve)

    p = sub.add_parser("allocate", parents=[common], aliases=["fenhao"], help="断线前一次性分完整册")
    p.add_argument("--alpha", help="甲的三个号，如 1,3,6")
    p.add_argument("--beta", help="乙的三个号，如 2,4,5")
    p.add_argument("--preset", choices=sorted(set(PRESETS)), help="segment/interleave/balanced 等")
    p.set_defaults(handler=cmd_allocate)

    p = sub.add_parser("depart", parents=[common], aliases=["lizhan", "leave"], help="离站并冻结分号")
    p.add_argument("--trip", help="alpha/甲 或 beta/乙")
    p.add_argument("--all", action="store_true", dest="all_trips")
    p.set_defaults(handler=cmd_depart)

    p = sub.add_parser("restart", parents=[common], aliases=["chongqi", "reboot"], help="模拟发端重启并重载持久状态")
    p.add_argument("--trip", "--owner", dest="trip", required=True)
    p.set_defaults(handler=cmd_restart)

    p = sub.add_parser("write", parents=[common], aliases=["xiexin", "compose", "faxin"], help="按从小到大顺序写信并消耗封条，存入 outbox")
    p.add_argument("--trip", "--owner", dest="trip", required=True)
    p.add_argument("--text", "--body", "--message", dest="text", help="电文正文；也可直接把正文作为位置参数")
    p.add_argument("words", nargs="*", help="电文正文（位置参数形式）")
    p.add_argument("--seal", type=int, help="一般省略，由系统选下一个最小未耗号")
    p.set_defaults(handler=cmd_write)

    p = sub.add_parser("send", parents=[common], aliases=["fasong", "fabao", "send-message"], help="写信并立即经本地信道送给指定收端")
    p.add_argument("--trip", "--owner", dest="trip", required=True)
    p.add_argument("--text", "--body", "--message", dest="text", help="电文正文")
    p.add_argument("words", nargs="*", help="电文正文（位置参数形式）")
    p.add_argument("--seal", type=int)
    p.add_argument("--receiver", default="receiver")
    p.set_defaults(handler=cmd_send)

    p = sub.add_parser("outbox", parents=[common], aliases=["faxiang"], help="查看某台已写待发的信")
    p.add_argument("--trip", "--owner", dest="trip", required=True)
    p.set_defaults(handler=cmd_outbox)

    p = sub.add_parser("receive", parents=[common], aliases=["shouxin", "record", "luce"], help="收一封信并执行核验/窗口/落册")
    p.add_argument("--envelope", required=True, help="信封 JSON 路径")
    p.add_argument("--receiver", default="receiver", help="收端状态名，默认主账册")
    p.set_defaults(handler=cmd_receive)

    p = sub.add_parser("deliver", parents=[common], aliases=["fasong-outbox", "transmit"], help="把某台 outbox 按封条号顺序送达")
    p.add_argument("--trip", "--owner", dest="trip", required=True)
    p.add_argument("--receiver", default="receiver")
    p.set_defaults(handler=cmd_deliver)

    p = sub.add_parser("arrival", parents=[common], aliases=["dada", "sync"], help="按整批行程次序送达，如 alpha,beta")
    p.add_argument("--order", required=True, help="例如 alpha,beta 或 beta,alpha")
    p.add_argument("--receiver", default="receiver")
    p.set_defaults(handler=cmd_arrival)

    p = sub.add_parser("reconcile", parents=[common], aliases=["duizhang"], help="人工对账后把过窗真信补录入册")
    p.add_argument("--seal", required=True, type=int)
    p.add_argument("--note", default="")
    p.add_argument("--receiver", default="receiver")
    p.set_defaults(handler=cmd_reconcile)

    for name, aliases, handler in [
        ("status", ["dashboard", "zhuangtai"], cmd_status),
        ("ledger", ["ce", "luce-list"], cmd_ledger),
    ]:
        p = sub.add_parser(name, parents=[common], aliases=aliases, help="查看窗口、账册和待对账区")
        p.add_argument("--receiver", default="receiver")
        p.set_defaults(handler=handler)

    p = sub.add_parser("plan", parents=[common], aliases=["fangan"], help="真实运行一个固定分法的两种抵达次序")
    p.add_argument("--alpha")
    p.add_argument("--beta")
    p.add_argument("--preset", choices=sorted(set(PRESETS)))
    p.set_defaults(handler=cmd_plan)

    p = sub.add_parser("plans", parents=[common], aliases=["compare", "bijiao"], help="枚举全部分法并比较两种抵达次序")
    p.add_argument("--all", action="store_true", help="显示全部 20 种甲选三号方案")
    p.set_defaults(handler=cmd_plans)

    p = sub.add_parser("demo", parents=[common], aliases=["yanshi"], help="重建状态并运行完整场景与故障演练")
    p.set_defaults(handler=cmd_demo)

    p = sub.add_parser("tamper-unissue", parents=[common], aliases=["po-huai-deng-ji"], help="故障注入：把某号登记改成尚未交该端")
    p.add_argument("--seal", required=True, type=int)
    p.set_defaults(handler=cmd_tamper_unissue)

    p = sub.add_parser("repair-grant", parents=[common], aliases=["xiufu-deng-ji"], help="按预置可信回执修复封条登记")
    p.add_argument("--seal", type=int)
    p.add_argument("--all", action="store_true", dest="all_seals")
    p.set_defaults(handler=cmd_repair_grant)

    p = sub.add_parser("expect-body", parents=[common], help="在预置可信回执中再钉住某号应对应的正文")
    p.add_argument("--seal", required=True, type=int)
    p.add_argument("--text", required=True)
    p.set_defaults(handler=cmd_expect_body)

    p = sub.add_parser("reset-receiver", parents=[common], help="清空指定名称的收端窗口和账册")
    p.add_argument("--name", default="receiver")
    p.add_argument("--window", type=int, default=3)
    p.set_defaults(handler=cmd_reset_receiver)

    return parser


# ---------- 输出辅助 ----------

def print_json(value: Any) -> None:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False))


def case_for(args: argparse.Namespace) -> SealCase:
    return SealCase(args.state)


def emit(args: argparse.Namespace, data: Any, text: str = "") -> int:
    if args.json:
        print_json(data)
    elif text:
        print(text)
    return 0


def result_exit_code(result: Any) -> int:
    status = result.status if hasattr(result, "status") else result.get("status")
    return 3 if status in ("rejected", "conflict") else 0


def format_allocation(plan: Dict[str, Any]) -> str:
    return (
        f"甲(alpha)={plan[ALPHA]}（{TRIP_LABELS[ALPHA]}）\n"
        f"乙(beta) ={plan[BETA]}（{TRIP_LABELS[BETA]}）"
    )


def format_result(result: Any) -> str:
    d = result.to_dict() if hasattr(result, "to_dict") else dict(result)
    label = STATUS_TEXT.get(d["status"], d["status"])
    lines = [f"[{label}] 封条 {d.get('seal')} / {d.get('owner')}: {d.get('message', '')}"]
    if d.get("window") is not None:
        lines.append(f"  当前三号窗口: {d['window']}；已见最大号: {d.get('high_water')}")
    if d.get("ledger_id"):
        lines.append(f"  账册行: {d['ledger_id']}；回条: {d.get('receipt_id')}")
    elif d.get("receipt_id"):
        lines.append(f"  回条: {d['receipt_id']}")
    if d.get("code") and d["status"] in ("rejected", "conflict"):
        lines.append(f"  代码: {d['code']}")
    return "\n".join(lines)


def format_dashboard(dash: Dict[str, Any]) -> str:
    lines = [
        f"已见最大号 H={dash['high_water']}；直收窗口={dash['window'] or '（尚未收信）'}",
        f"直接入册号: {dash['direct_seals']}",
        f"人工补录号: {dash['manual_seals']}",
        f"待对账号:   {dash['pending_seals']}",
        f"账册 {dash['ledger_count']} 行；待对账 {dash['pending_count']} 封",
        "计数: " + json.dumps(dash["stats"], ensure_ascii=False),
    ]
    return "\n".join(lines)


def format_plan_report(report: Any) -> str:
    d = report.to_dict() if hasattr(report, "to_dict") else report
    lines = [
        f"分法：甲={d['alpha']}，乙={d['beta']}"
        + (f"（preset={d['preset']}）" if d.get("preset") else ""),
        f"最坏次序可直收 {d['worst_direct']}/6；两种次序合计直收 {d['total_direct']}/12",
    ]
    for key, run in d["runs"].items():
        order = " → ".join(TRIP_LABELS.get(x, x) for x in run["order"])
        lines.append(
            f"  {order}: 直收 {run['direct']}（{run['direct_count']}），"
            f"待对账 {run['pending']}（{run['manual_count']}），"
            f"拒收/冲突 {len(run['rejected'])}，H={run['high_water']}，窗口={run['window']}"
        )
        if run.get("late_trip"):
            late_label = TRIP_LABELS.get(run["late_trip"], run["late_trip"])
            lines.append(
                f"    晚到的{late_label}端：窗口内可直收 {run['late_direct']}；"
                f"落入待对账 {run['late_pending']}"
            )
        for bad in run["rejected"]:
            lines.append(f"    拒收: {bad}")
    return "\n".join(lines)


# ---------- 命令处理 ----------

def cmd_init(args: argparse.Namespace) -> int:
    case = SealCase.init(
        args.state,
        book_size=args.book_size,
        window_size=args.window_size,
        force=args.force,
    )
    data = {"state": str(case.root), "book_size": args.book_size, "window_size": args.window_size}
    return emit(args, data, f"已初始化状态目录 {case.root}：全册 1-{args.book_size}，直收窗口 {args.window_size} 号")


def cmd_apply(args: argparse.Namespace) -> int:
    case = case_for(args)
    app = case.apply(args.trip, args.seals)
    return emit(args, app, f"{TRIP_LABELS[app['trip']]}端申请已记录：{app['seals']}")


def cmd_approve(args: argparse.Namespace) -> int:
    case = case_for(args)
    app = case.approve(args.trip)
    return emit(args, app, f"已批准并预置可信回执：{TRIP_LABELS[app['trip']]}端 {app['seals']}")


def cmd_allocate(args: argparse.Namespace) -> int:
    case = case_for(args)
    plan = case.allocate(alpha=args.alpha, beta=args.beta, preset=args.preset)
    return emit(args, plan, "断线前分号完成（尚未离站）：\n" + format_allocation(plan))


def cmd_depart(args: argparse.Namespace) -> int:
    case = case_for(args)
    if args.all_trips:
        case.depart_all()
        text = "甲、乙均已离站；分号现已冻结，两端离线通信。"
        data = {"departed": [ALPHA, BETA]}
    elif args.trip:
        data = case.depart(args.trip)
        text = f"{TRIP_LABELS[data['trip']]}端已离站；分号现已冻结。"
    else:
        raise SealChannelError("请指定 --trip 或 --all", code="MISSING_TRIP")
    return emit(args, data, text)


def cmd_restart(args: argparse.Namespace) -> int:
    case = case_for(args)
    data = case.restart_terminal(args.trip)
    return emit(
        args,
        {"trip": data["trip"], "restart_count": data["restart_count"], "consumed": data["consumed"]},
        f"{TRIP_LABELS[data['trip']]}端已从磁盘状态重启；已耗封条 {sorted(map(int, data['consumed']))} 不会换正文。",
    )


def _body_from_args(args: argparse.Namespace) -> str:
    if args.text is not None:
        return args.text
    if args.words:
        return " ".join(args.words)
    raise SealChannelError("请用 --text 或位置参数给出电文", code="EMPTY_BODY")


def cmd_write(args: argparse.Namespace) -> int:
    case = case_for(args)
    envelope = case.write(args.trip, _body_from_args(args), seal=args.seal)
    text = (
        f"{TRIP_LABELS[envelope['owner']]}端写信完成：封条 {envelope['seal']}，"
        f"回执 {envelope['body_receipt'][:16]}…，已入本端 outbox。"
    )
    return emit(args, envelope, text)


def cmd_send(args: argparse.Namespace) -> int:
    case = case_for(args)
    envelope = case.write(args.trip, _body_from_args(args), seal=args.seal)
    result = case.receive_envelope(envelope, args.receiver)
    if args.json:
        print_json({"envelope": envelope, "result": result.to_dict()})
    else:
        print(
            f"{TRIP_LABELS[envelope['owner']]}端已封发 #{envelope['seal']}，"
            f"信封已交给收端 {args.receiver}。"
        )
        print(format_result(result))
    return result_exit_code(result)


def cmd_outbox(args: argparse.Namespace) -> int:
    case = case_for(args)
    terminal = case.terminal(args.trip)
    envelopes = terminal.all_envelopes()
    if args.json:
        print_json(envelopes)
    else:
        print(f"{TRIP_LABELS[terminal.trip]}端 outbox（按封条号升序）:")
        for e in envelopes:
            print(f"  #{e['seal']}: {e['body']}")
    return 0


def cmd_receive(args: argparse.Namespace) -> int:
    case = case_for(args)
    result = case.receive_file(args.envelope, args.receiver)
    if args.json:
        print_json(result)
    else:
        print(format_result(result))
    return result_exit_code(result)


def cmd_deliver(args: argparse.Namespace) -> int:
    case = case_for(args)
    results = case.deliver_trip(args.trip, args.receiver)
    if args.json:
        print_json([r.to_dict() for r in results])
    else:
        for r in results:
            print(format_result(r))
    return max((result_exit_code(r) for r in results), default=0)


def cmd_arrival(args: argparse.Namespace) -> int:
    case = case_for(args)
    results = case.arrival(args.order, args.receiver)
    if args.json:
        print_json([r.to_dict() for r in results])
    else:
        for r in results:
            print(format_result(r))
        print(format_dashboard(case.receiver(args.receiver).dashboard()))
    return max((result_exit_code(r) for r in results), default=0)


def cmd_reconcile(args: argparse.Namespace) -> int:
    case = case_for(args)
    entry = case.reconcile(args.seal, args.note, args.receiver)
    return emit(args, entry, f"人工对账完成：{args.seal} 号已补录入册，账册行 {entry['ledger_id']}")


def cmd_status(args: argparse.Namespace) -> int:
    case = case_for(args)
    dash = case.receiver(args.receiver).dashboard()
    return emit(args, dash, format_dashboard(dash))


def cmd_ledger(args: argparse.Namespace) -> int:
    case = case_for(args)
    receiver = case.receiver(args.receiver)
    data = {
        "dashboard": receiver.dashboard(),
        "ledger": receiver.data["ledger"],
        "pending": receiver.data["pending"],
    }
    if args.json:
        print_json(data)
    else:
        print("账册明细:")
        for e in receiver.data["ledger"]:
            source = "直收" if e["source"] == "direct_window" else "人工补录"
            print(f"  {e['ledger_id']} #{e['seal']} [{source}] {e['body']}")
        print("\n待对账区:")
        active = [p for p in receiver.data["pending"] if p.get("state") == "active"]
        if not active:
            print("  （无）")
        for p in active:
            print(f"  #{p['seal']} H到达={p['high_water_at_arrival']} 窗口={p['window_at_arrival']} {p['body']}")
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    if args.preset:
        chosen = PRESETS[args.preset]
        alpha, beta = chosen[ALPHA], chosen[BETA]
    else:
        if not args.alpha or not args.beta:
            raise SealChannelError("请给 --alpha/--beta，或使用 --preset", code="MISSING_PLAN")
        alpha = [int(x) for x in args.alpha.replace(",", " ").split()]
        beta = [int(x) for x in args.beta.replace(",", " ").split()]
    report = Planner.evaluate(alpha, beta, preset=args.preset)
    return emit(args, report.to_dict(), format_plan_report(report))


def cmd_plans(args: argparse.Namespace) -> int:
    reports = Planner.compare_all()
    shown = reports if args.all else reports[:6]
    data = {
        "total_allocations": len(reports),
        "showing": len(shown),
        "recommended": reports[0].to_dict(),
        "plans": [r.to_dict() for r in shown],
    }
    if args.json:
        print_json(data)
        return 0

    print("固定分法 × 同一批电文 × 两种整批抵达次序（按最坏直收数排序）")
    for report in shown:
        print(format_plan_report(report))
        print("-" * 72)
    if not args.all:
        print("仅显示前 6 个；使用 `plans --all` 查看全部 20 个甲选三号方案。")
    else:
        seg = next(r for r in reports if r.alpha == [1, 2, 3])
        inter = next(r for r in reports if r.alpha == [1, 3, 5])
        bal = next(r for r in reports if r.alpha == [1, 3, 6])
        print("\n题目三个参照分法:")
        for r in (seg, inter, bal):
            print(format_plan_report(r))
    return 0


def cmd_tamper_unissue(args: argparse.Namespace) -> int:
    case = case_for(args)
    record = case.mark_unissued(args.seal)
    return emit(
        args,
        record,
        f"已制造登记故障：{args.seal} 号当前为 {record['state']}；可信回执未改，原信应核验失败。",
    )


def cmd_repair_grant(args: argparse.Namespace) -> int:
    case = case_for(args)
    count = case.repair_grants(seal=args.seal, all_seals=args.all_seals)
    data = {"repaired": count}
    return emit(args, data, f"已按预置可信回执修复 {count} 条封条登记，归属保持不变。")


def cmd_expect_body(args: argparse.Namespace) -> int:
    case = case_for(args)
    case.expect_body(args.seal, args.text)
    return emit(args, {"seal": args.seal, "body_sha256": sha256_hex(args.text)}, f"已为 {args.seal} 号预置正文回执。")


def cmd_reset_receiver(args: argparse.Namespace) -> int:
    from .receiver import initial_receiver_state

    case = case_for(args)
    path = case.receiver_state_path(args.name)
    case.store.write_json(path, value=initial_receiver_state(args.window))
    return emit(args, {"receiver": args.name, "path": path}, f"已重置收端状态 {args.name}（{path}）。")


def cmd_demo(args: argparse.Namespace) -> int:
    # Demo 自己负责人类输出；--json 时最后给结构化结果。
    root = Path(args.state).resolve()
    case = SealCase.init(root, force=True)
    output: Dict[str, Any] = {"state": str(root)}

    def heading(title: str) -> None:
        if not args.json:
            print("\n" + "=" * 76)
            print(title)
            print("=" * 76)

    heading("1. 先看固定分法在两种抵达次序下的真实运行")
    comparisons = {}
    for preset in ("segment", "interleave", "balanced"):
        report = Planner.evaluate(**_preset_kwargs(preset), preset=preset)
        comparisons[preset] = report.to_dict()
        if not args.json:
            print(format_plan_report(report))
            print("-" * 72)
    output["comparisons"] = comparisons

    heading("2. 在当前状态目录运行推荐分法：甲 1,3,6；乙 2,4,5")
    plan = case.allocate(preset="balanced")
    case.depart_all()
    messages = {
        ALPHA: {
            1: "甲-1：先去西河湾，封条一号平安。",
            3: "甲-3：途经断崖岭，路况可通行。",
            6: "甲-6：已到北哨，伤员与药品交接完毕。",
        },
        BETA: {
            2: "乙-2：东线水源点已补给。",
            4: "乙-4：石桥完好，车队可过。",
            5: "乙-5：折返前收到基地回执。",
        },
    }
    for trip, body_by_seal in messages.items():
        for seal, body in body_by_seal.items():
            case.write(trip, body, seal=seal)
    case.restart_terminal(ALPHA)
    output["orders"] = {}
    for order, receiver_name in [
        ((ALPHA, BETA), "a-first"),
        ((BETA, ALPHA), "b-first"),
    ]:
        results = case.arrival(order, receiver_name)
        dash = case.receiver(receiver_name).dashboard()
        output["orders"][",".join(order)] = {
            "results": [r.to_dict() for r in results],
            "dashboard": dash,
        }
        if not args.json:
            print(f"\n收端 {receiver_name}，次序 {' → '.join(TRIP_LABELS[x] for x in order)}")
            for r in results:
                print(format_result(r))
            print(format_dashboard(dash))

    heading("3. 登记改成“尚未交甲”：同一封 #1 原信不能通过核验")
    original_envelope = case.terminal(ALPHA).load_envelope(1)
    case.mark_unissued(1)
    tampered_result = case.receive_envelope(original_envelope, "tamper")
    output["tampered_registration_result"] = tampered_result.to_dict()
    if not args.json:
        print(format_result(tampered_result))

    heading("4. 发端重启后，#1 不能转给另一正文")
    case.restart_terminal(ALPHA)
    sender_conflict = None
    try:
        case.write(ALPHA, "这是想占用一号封条的新正文", seal=1)
    except SealChannelError as exc:
        sender_conflict = exc.to_dict()
        output["sender_rebind_error"] = sender_conflict
        if not args.json:
            print(f"[已阻止 {exc.code}] {exc.message}")
    if sender_conflict is None:
        raise SealChannelError("安全 invariant 被破坏：已耗封条竟可改绑新正文", code="INVARIANT_FAILED")

    heading("5. 恢复归属登记；让更晚的 #6 先收妥，#1 原件转为待对账而非消失")
    repaired = case.repair_grants(seal=1)
    high_result = case.receive_envelope(case.terminal(ALPHA).load_envelope(6), "late-one")
    late_original = case.receive_envelope(original_envelope, "late-one")
    duplicate_original = case.receive_envelope(original_envelope, "late-one")

    # 构造密码学上有效的异文，模拟“另一份正文也想占 #1”；正常发端状态不会
    # 产生它，收端的 body_index 仍必须拒绝占号。
    book = case.trusted_book()
    key = book["seals"]["1"]["seal_key_hex"]
    competing = dict(original_envelope)
    competing["body"] = "另一份试图占用一号封条的正文"
    competing["body_sha256"] = sha256_hex(competing["body"])
    competing["body_receipt"] = body_receipt_value(
        key, 1, ALPHA, competing["body_sha256"]
    )
    conflict_result = case.receive_envelope(competing, "late-one")
    reconcile_entry = case.reconcile(1, "演示：人工核对原信与可信回执后补录", "late-one")
    final_dash = case.receiver("late-one").dashboard()
    output["late_one"] = {
        "high_result": high_result.to_dict(),
        "original_result": late_original.to_dict(),
        "duplicate_result": duplicate_original.to_dict(),
        "conflict_result": conflict_result.to_dict(),
        "reconcile_entry": reconcile_entry,
        "dashboard": final_dash,
        "repaired_registrations": repaired,
    }
    if not args.json:
        for r in (high_result, late_original, duplicate_original, conflict_result):
            print(format_result(r))
        print(format_dashboard(final_dash))
        print(f"人工补录: {reconcile_entry['ledger_id']}")

    if args.json:
        print_json(output)
    else:
        print("\n演示完成。可运行 `./sealctl plans` 查看全部分法，或进入上述状态目录检查 JSON。")
    return 0


def _preset_kwargs(preset: str) -> Dict[str, Any]:
    chosen = PRESETS[preset]
    return {"alpha": chosen[ALPHA], "beta": chosen[BETA]}


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args) or 0)
    except SealChannelError as exc:
        if getattr(args, "json", False):
            print_json(exc.to_dict())
        else:
            print(f"错误[{exc.code}]: {exc.message}", file=sys.stderr)
            if exc.details:
                print(json.dumps(exc.details, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        # 常见于尚未 init 就执行命令。
        msg = f"找不到状态文件：{exc.filename}；请先运行 init/allocate/demo"
        if getattr(args, "json", False):
            print_json({"ok": False, "error": msg, "code": "STATE_NOT_INITIALIZED"})
        else:
            print(f"错误[STATE_NOT_INITIALIZED]: {msg}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
