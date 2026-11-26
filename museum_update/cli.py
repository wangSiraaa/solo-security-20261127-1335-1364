from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from . import controller
from .errors import CtlError, PowerCut
from .packages import verify_package
from .report import (
    format_all_markdown,
    format_machine_report,
    format_overview,
    format_shift_records,
    shift_rows,
)
from .store import Store


def _load_json_file(path: str | Path) -> Dict[str, Any]:
    p = Path(path)
    try:
        value = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CtlError(f"输入文件不存在: {p}") from exc
    except json.JSONDecodeError as exc:
        raise CtlError(f"输入 JSON 无效 {p}: {exc}") from exc
    if not isinstance(value, dict):
        raise CtlError(f"输入文件顶层必须是 JSON 对象: {p}")
    return value


def _store(args: argparse.Namespace) -> Store:
    return Store(args.state_dir)


def _load_state(store: Store) -> Dict[str, Any]:
    try:
        return store.load()
    except FileNotFoundError as exc:
        raise CtlError(str(exc)) from exc


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def cmd_init(args: argparse.Namespace) -> int:
    store = _store(args)
    controller.initialize_state(
        store,
        force=args.force,
        deadline_shift=args.deadline_shift,
        package_dir=args.package,
    )
    print(format_overview(store.load()))
    print(f"\n持久状态: {store.state_path}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    pkg = verify_package(args.package)
    print(f"✅ 批准包核对通过: {pkg.order_id} 第{pkg.generation}代")
    print(f"   文件数={len(pkg.file_digests)} 组件数={len(pkg.components)}")
    print(f"   整套摘要={pkg.set_digest}")
    if args.json:
        _print_json(
            {
                "order_id": pkg.order_id,
                "generation": pkg.generation,
                "set_digest": pkg.set_digest,
                "file_digests": pkg.file_digests,
                "components": pkg.components,
            }
        )
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    store = _store(args)
    state = _load_state(store)
    order = _load_json_file(args.order)
    try:
        record = controller.install_order(
            store,
            state,
            order,
            base_dir=Path.cwd(),
            power_loss_after=args.power_loss_after,
        )
    except PowerCut as cut:
        print(f"⚡ 模拟掉电发生在持久化阶段 `{cut.phase}` 之后；进度已落盘。")
        print(f"   重新上电后运行: python -m museum_update --state-dir {store.root} resume")
        return cut.exit_code
    print(f"✅ 安装单 {record['id']} 已完成写槽和切换，当前阶段={record['phase']}")
    print(format_overview(store.load()))
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    store = _store(args)
    state = _load_state(store)
    orders = controller.resume_pending(store, state, args.machine)
    if not orders:
        print("没有未完成的持久安装单。")
    else:
        for order in orders:
            print(f"🔁 已接续 {order['id']} / {order['machine']}: {order['status']} ({order['phase']})")
    print(format_overview(store.load()))
    return 0


def cmd_boot(args: argparse.Namespace) -> int:
    store = _store(args)
    state = _load_state(store)
    request = _load_json_file(args.input) if args.input else {}
    machine_id = args.machine or request.get("machine")
    if not machine_id:
        raise CtlError("boot 需要机器号，或在 --input JSON 中提供 machine")
    result = args.result or request.get("result", "pass")
    if result not in ("pass", "fail"):
        raise CtlError("开机输入 result 只能是 pass 或 fail")
    note = args.note or request.get("note", "")
    slot = args.slot or request.get("slot")
    receipt_path = args.receipt or request.get("receipt")
    receipt_data = _load_json_file(receipt_path) if receipt_path else None
    outcome = controller.boot_machine(
        store,
        state,
        machine_id,
        result,
        note=note,
        receipt_data=receipt_data,
        slot=slot,
    )
    fresh = store.load()
    if args.json:
        _print_json(outcome)
    elif outcome.get("blocked"):
        print(f"⛔ 开机被阻止: {outcome['reason']}")
    elif outcome.get("rolled_back"):
        print("↩️ 试启失败后已按持久回退条件恢复到旧槽，恢复自检通过。")
    elif outcome.get("disabled"):
        print(f"🛑 试启失败且不能原地恢复: {outcome.get('reason')}")
    else:
        print("✅ 开机自检回执已记录。")
    if not args.json:
        print(format_machine_report(fresh, machine_id))
    return 0 if not outcome.get("blocked") else 3


def cmd_commit(args: argparse.Namespace) -> int:
    store = _store(args)
    state = _load_state(store)
    decision = controller.burn_bridge(
        store,
        state,
        args.machine,
        by=args.by,
        reason=args.reason,
        required_good_receipts=args.required_receipts,
        accept_early_risk=args.accept_early_risk,
    )
    if args.json:
        _print_json(decision)
    else:
        print(f"🔥 {args.machine} 已作出不可逆承诺，最低代次={decision['new_min_boot_generation']}。")
        print(format_machine_report(store.load(), args.machine))
    return 0


def cmd_advance(args: argparse.Namespace) -> int:
    store = _store(args)
    state = _load_state(store)
    shift = controller.advance_shift(store, state, args.by)
    print(f"🕘 已前进到第 {shift} 班。")
    print(format_overview(store.load()))
    return 0


def cmd_reconcile(args: argparse.Namespace) -> int:
    store = _store(args)
    state = _load_state(store)
    results = controller.reconcile_deadline(store, state, by=args.by)
    if args.json:
        _print_json(results)
    else:
        for r in results:
            print(f"- {r['machine']}: {r['action']}")
        print(format_overview(store.load()))
    return 0


def cmd_operate(args: argparse.Namespace) -> int:
    store = _store(args)
    state = _load_state(store)
    record = controller.field_operation(
        store, state, args.machine, args.operation, operator=args.by
    )
    if args.json:
        _print_json(record)
    else:
        print(f"🛠️ 已登记 {args.machine} 现场操作（{record['contract']}）: {args.operation}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    state = _load_state(_store(args))
    if args.json:
        _print_json(state["clock"])
    else:
        print(format_overview(state))
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    state = _load_state(_store(args))
    if args.json:
        _print_json(state["machines"][args.machine])
    else:
        print(format_machine_report(state, args.machine))
    return 0


def cmd_shifts(args: argparse.Namespace) -> int:
    state = _load_state(_store(args))
    if args.json:
        _print_json(shift_rows(state, args.machine))
    else:
        print(format_shift_records(state, args.machine))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    state = _load_state(_store(args))
    print(format_all_markdown(state))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="museum_update",
        description="虚构馆内终端双槽更新、试启回执、防回滚代次和掉电恢复控制器",
    )
    parser.add_argument("--state-dir", default=None, help="持久状态目录（默认 MUSEUM_STATE_DIR 或 ./state）")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--state-dir",
        default=argparse.SUPPRESS,
        help="持久状态目录；也可放在子命令后",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    original_add_parser = sub.add_parser

    def add_parser(*args: Any, **kwargs: Any) -> argparse.ArgumentParser:
        kwargs.setdefault("parents", []).append(common)
        return original_add_parser(*args, **kwargs)

    sub.add_parser = add_parser  # type: ignore[method-assign]

    p = sub.add_parser("init", help="初始化三台模拟机的同一起点")
    p.add_argument("--force", action="store_true")
    p.add_argument("--deadline-shift", type=int, default=3)
    p.add_argument("--package", default=None)
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("verify", help="只核对发布包")
    p.add_argument("package")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("install", help="按可修改的 JSON 安装单核对、写槽并切换试启")
    p.add_argument("order")
    p.add_argument("--power-loss-after", choices=["verified", "written", "switched"])
    p.set_defaults(func=cmd_install)

    p = sub.add_parser("resume", help="掉电后从持久安装单继续")
    p.add_argument("machine", nargs="?")
    p.set_defaults(func=cmd_resume)

    p = sub.add_parser("boot", help="模拟开机自检并提交 pass/fail 回执")
    p.add_argument("machine", nargs="?")
    p.add_argument("--input", help="可修改的开机请求 JSON；命令行参数优先")
    p.add_argument("--result", choices=["pass", "fail"], default=None)
    p.add_argument("--slot", choices=["a", "b"], help="显式指定模拟开机槽；默认优先试启槽，否则现役槽")
    p.add_argument("--note", default="")
    p.add_argument("--receipt", help="可选：提交外部回执 JSON，而不是由模拟设备生成")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_boot)

    p = sub.add_parser("commit", aliases=["burn-bridge"], help="提高最低代次，不可逆地烧掉回旧版的桥")
    p.add_argument("machine")
    p.add_argument("--by", default="maintainer")
    p.add_argument("--reason", required=True)
    p.add_argument("--required-receipts", type=int, default=2)
    p.add_argument(
        "--accept-early-risk",
        action="store_true",
        help="只有一次连续合格回执时，维护员显式承担第二次自检可能失败的风险",
    )
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_commit)

    p = sub.add_parser("advance-shift", help="前进逻辑班次")
    p.add_argument("--by", type=int, default=1)
    p.set_defaults(func=cmd_advance)

    p = sub.add_parser("reconcile", help="班点到达后强制处理旧代次和滞留试装")
    p.add_argument("--by", default="deadline-policy")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_reconcile)

    p = sub.add_parser("operate", help="登记一次现场操作")
    p.add_argument("machine")
    p.add_argument("--operation", required=True)
    p.add_argument("--by", default="field-staff")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_operate)

    p = sub.add_parser("status", help="显示三机总览")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("show", aliases=["explain", "records"], help="按机器号解释最终启动了什么")
    p.add_argument("machine")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("shifts", help="查询班次时间线，可按机器号过滤")
    p.add_argument("machine", nargs="?")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_shifts)

    p = sub.add_parser("report", help="输出全部 Markdown 记录")
    p.set_defaults(func=cmd_report)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.state_dir is None:
        args.state_dir = None  # Store resolves env/default
    try:
        return int(args.func(args) or 0)
    except CtlError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
