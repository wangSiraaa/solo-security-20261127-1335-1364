"""命令行入口。

子命令：
  verify <trustdir>                 校验席：核验受信 inbox 并签发记录
  tui <selector_dir>                全屏键盘界面（需要 TTY）
  show <selector_dir> [--width N]   脚本模式：打印字符格画面与朗读稿
  install <selector_dir> <entry_id> [--width N] [--to DIR]
  receipt check <receipt.json>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .installer import InstallError, verify_receipt
from .model import Model
from .scriptmode import dump_json, select_and_install, show
from .verifier import run as verifier_run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pick", description="本地键盘安装选择器")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_verify = sub.add_parser("verify", help="校验席核验受信目录中的包")
    p_verify.add_argument("trustdir")

    p_tui = sub.add_parser("tui", help="全屏键盘界面")
    p_tui.add_argument("selector_dir")

    p_show = sub.add_parser("show", help="输出画面与朗读稿（脚本模式）")
    p_show.add_argument("selector_dir")
    p_show.add_argument("--width", type=int, default=60)
    p_show.add_argument("--height", type=int, default=24)
    p_show.add_argument("--json", action="store_true")

    p_install = sub.add_parser("install", help="按核验结论安装一个条目")
    p_install.add_argument("selector_dir")
    p_install.add_argument("entry_id")
    p_install.add_argument("--width", type=int, default=60)
    p_install.add_argument("--height", type=int, default=24)
    p_install.add_argument("--to", dest="to", default=None,
                           help="安装目录（默认 <selector_dir>/installed），必须为空")

    p_receipt = sub.add_parser("receipt", help="操作回单")
    receipt_sub = p_receipt.add_subparsers(dest="receipt_cmd", required=True)
    p_check = receipt_sub.add_parser("check")
    p_check.add_argument("receipt")
    p_check.add_argument("--install-root", default=None)

    args = parser.parse_args(argv)

    if args.cmd == "verify":
        results = verifier_run(args.trustdir)
        for att in results:
            print(f"已核验 {att.payload['publisher_id']} 的包 "
                  f"{att.payload['package_file']} -> {att.payload['package_sha256'][:16]}…")
        print(f"共签发 {len(results)} 份核验记录，清单已更新："
              f"{Path(args.trustdir) / 'attestations' / 'manifest.json'}")
        return 0

    if args.cmd == "tui":
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            print("tui 需要真实终端；无 TTY 环境请改用 show / install 子命令",
                  file=sys.stderr)
            return 4
        from .tui import run_curses
        model = Model.open(args.selector_dir)
        outcome = run_curses(model)
        print(json.dumps(outcome, ensure_ascii=False))
        return 0

    if args.cmd == "show":
        model = Model.open(args.selector_dir)
        result = show(model, args.width, args.height)
        if args.json:
            print(dump_json(result))
        else:
            print("===== 字符格画面 =====")
            print(result.frame.visual())
            print("\n===== 可朗读文本（读屏稿） =====")
            print(result.frame.speech())
        return 0

    if args.cmd == "install":
        model = Model.open(args.selector_dir)
        to = args.to or str(Path(args.selector_dir) / "installed")
        try:
            result = select_and_install(
                model, args.entry_id, args.width, to, args.height
            )
        except InstallError as exc:
            print(f"安装被拒绝：{exc}", file=sys.stderr)
            return 2
        print(dump_json(result))
        print(f"安装完成：{result.detail['installed_path']}", file=sys.stderr)
        return 0

    if args.cmd == "receipt":
        receipt_path = Path(args.receipt)
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        root = args.install_root or receipt["installed"]["directory"]
        problems = verify_receipt(receipt_path, root)
        if problems:
            for p in problems:
                print(f"不一致：{p}", file=sys.stderr)
            return 3
        print("回单核对通过：安装目录字节与核验记录绑定哈希一致，"
              "回单内留有当时的画面行与朗读稿。")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
