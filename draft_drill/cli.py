"""命令行入口。"""

from __future__ import annotations

import argparse

from .demo import run_demo


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m draft_drill",
        description="本地虚拟终端、状态核验器与借稿台演练",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("demo", help="运行完整端到端演练（默认）")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command in (None, "demo"):
        return run_demo()
    parser.error(f"未知命令：{args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
