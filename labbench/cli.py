from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from .lab import Lab
from .scenarios import run_core_scenario, run_holder_loss_demo


def _print_summary(result: dict) -> None:
    root: Path = result["lab"].root
    print(f"    运行目录：{root}")
    print(f"    A 校导出：{root / 'exports' / 'school_a_export.json'}")
    print(f"    B 校导出：{root / 'exports' / 'school_b_export.json'}")
    print(f"    登记处导出：{root / 'exports' / 'registry_only_export.json'}")
    print(f"    公开回单：{root / 'public' / 'receipts.jsonl'}")
    print(f"    A 台样件：{root / 'workspaces' / 'bench_a'}")
    print(f"    B 台样件：{root / 'workspaces' / 'bench_b'}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="labbench",
        description="两所学校数字结业卡、同持/关联示证、本地实验台开席/交席/中断演示",
    )
    sub = parser.add_subparsers(dest="command")

    demo = sub.add_parser("demo", help="运行完整安全场景演示")
    demo.add_argument("--mode", choices=["holder", "registry", "both"], default="both")
    demo.add_argument(
        "--dir",
        default=None,
        help="演示运行目录；both 模式下会在其下建立 holder/ 与 registry/ 子目录",
    )
    demo.add_argument("--keep", action="store_true", help="保留临时目录（默认演示使用固定 runtime 目录）")

    export = sub.add_parser("export", help="从已存在的运行目录导出某校记录")
    export.add_argument("--root", required=True)
    export.add_argument("--school", required=True, choices=["school_a", "school_b"])
    export.add_argument("--out", required=True)

    args = parser.parse_args(argv)

    if args.command == "export":
        lab = Lab(args.root)
        data = lab.export_school(args.school, args.out)
        print(json.dumps({"exported": args.out, "cards": len(data["student_records"])}, ensure_ascii=False))
        return 0

    # 默认命令也是 demo，方便直接 python -m labbench
    demo_dir = getattr(args, "dir", None)
    base = Path(demo_dir) if demo_dir else Path("/workspace/runtime/demo")
    if args.command is None:
        args_mode = "both"
    else:
        args_mode = args.mode

    if args_mode in ("holder", "both"):
        holder_dir = base / "holder" if args_mode == "both" else base
        result = run_core_scenario(holder_dir, mode="holder", log=print)
        _print_summary(result)
        loss_dir = base / "holder_loss" if args_mode == "both" else base.parent / "holder_loss"
        loss = run_holder_loss_demo(loss_dir, log=print)
        print(f"    遗失补绑演示目录：{loss['lab'].root}")

    if args_mode in ("registry", "both"):
        registry_dir = base / "registry" if args_mode == "both" else base
        result = run_core_scenario(registry_dir, mode="registry", log=print)
        _print_summary(result)

    print("\n全部演示与内置断言通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
