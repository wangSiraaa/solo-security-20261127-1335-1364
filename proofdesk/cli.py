from __future__ import annotations

import argparse
import shutil
import sys
import unittest
from pathlib import Path

from .actors import DELIVERY_DESIGNS
from .environment import DeskEnvironment
from .scenario import run_all_scenarios


def format_demo(results: dict) -> str:
    main = results["main"]
    edit_first = results["edit_first"]
    revoke_first = results["revoke_first"]
    local_revoke = results["local_revoke"]
    hosted = results["hosted"]

    lines: list[str] = []
    lines.append("校录委托与书库落稿演示")
    lines.append("=" * 72)
    lines.append("")
    lines.append("【校录终端身份说明】")
    lines.append(main["terminal_statement"])
    lines.append("")
    lines.append("【主故事：三个人、两章、真实修订、删/恢复转委托、锁定与重启】")
    lines.append(main["report"])
    lines.append("")
    lines.append(
        f"删去 redelegate 后：编辑站拒绝签新凭据={main['station_refused_new_pass']}；"
        f"书库拒绝同一校录员送件={main['negative_result']['status']} / "
        f"{main['negative_result']['code']}"
    )
    lines.append(f"  拒绝理由：{main['negative_result']['reason']}")
    lines.append(
        f"责编二校先锁定并推进版本后，校录员旧件={main['pending_result']['status']} "
        f"({main['pending_result']['merge_id']}, {','.join(main['pending_result']['reasons'])}); "
        "现稿未被覆盖。"
    )
    lines.append("")
    lines.append("【断联次序一：先改稿，后撤回】")
    lines.extend(_format_order_case(edit_first))
    lines.append("")
    lines.append("【断联次序二：先在编辑站撤回，后持 portable 凭据改稿】")
    lines.extend(_format_order_case(revoke_first))
    lines.append("  说明：这不是系统失效，而是作者选择 portable_pass 时接受的")
    lines.append("  “书库当地已知撤回”最终一致窗口；撤回同步后的下一笔即被拒绝。")
    lines.append("")
    lines.append("【若撤回件已通过本地可信收发柜进入书库抽屉】")
    lines.append(
        f"  断联也拒绝：status={local_revoke['status']}, code={local_revoke['code']}"
    )
    lines.append("")
    lines.append("【编辑站托管模式】")
    lines.append(
        f"  第一笔：{hosted['landed_rev_id']} / {hosted['landed_mode']}；"
        f"站内实际操作者留痕 {len(hosted['actual_operator_log'])} 条。"
    )
    lines.append(
        f"  撤回后 station 处理结果：{hosted['after_revoke_station']}"
    )
    lines.append(
        f"  断联后校录员自称代理：{hosted['claim_status']} / {hosted['claim_code']}"
    )
    lines.append(f"  书库说明：{hosted['claim_reason']}")
    lines.append("")
    lines.append(f"所有凭据信封、密钥、书库 JSON 与审计日志均在：{results['base_dir']}")
    lines.append("可用以下命令在重启进程后继续查看修订号报告：")
    lines.append(f"  python -m proofdesk report --state {Path(results['base_dir']) / 'main'}")
    return "\n".join(lines)


def _format_order_case(case: dict) -> list[str]:
    return [
        f"  目录：{case['state_dir']}",
        f"  第一笔：status={case['first_edit_status']} rev={case['first_rev_id']}",
        f"  撤回同步后第二笔：status={case['second_status']} code={case['second_code']}",
        f"  最终第一章：{case['final_chapter1_text']}",
        f"  实际落下“鼎”字：{case['landed_ding_character']}；"
        f"仍残留错词“顶沸”：{case['old_top_typo_remaining']}",
        f"  时间线：{case['timeline']}",
    ]


def cmd_demo(args: argparse.Namespace) -> int:
    state = Path(args.state)
    if args.fresh and state.exists():
        shutil.rmtree(state)
    results = run_all_scenarios(state)
    text = format_demo(results)
    print(text)
    report_path = state / "DEMO-REPORT.txt"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(text + "\n", encoding="utf-8")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    env = DeskEnvironment(Path(args.state))
    print(env.library.author_report_text())
    return 0


def cmd_designs(_args: argparse.Namespace) -> int:
    for mode, design in DELIVERY_DESIGNS.items():
        print(f"{mode}: {design['name']}")
        print(f"  断联书库：{design['offline_library']}")
        print(f"  撤回边界：{design['revocation_boundary']}")
        print(f"  删转委托：{design['clause_removal']}")
    return 0


def cmd_test(_args: argparse.Namespace) -> int:
    loader = unittest.TestLoader()
    suite = loader.discover(str(Path(__file__).resolve().parent.parent / "tests"))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="proofdesk", description=__doc__)
    sub = parser.add_subparsers(dest="command")

    demo = sub.add_parser("demo", help="run the complete three-person/two-chapter story")
    demo.add_argument("--state", default="demo-state")
    demo.add_argument("--fresh", action="store_true")
    demo.set_defaults(func=cmd_demo)

    report = sub.add_parser("report", help="reload persisted library and show revision report")
    report.add_argument("--state", required=True)
    report.set_defaults(func=cmd_report)

    designs = sub.add_parser("designs", help="explain the two author-selectable delivery designs")
    designs.set_defaults(func=cmd_designs)

    test = sub.add_parser("test", help="run bundled unittest suite")
    test.set_defaults(func=cmd_test)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        args = parser.parse_args(["demo", "--state", "demo-state", "--fresh"])
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
