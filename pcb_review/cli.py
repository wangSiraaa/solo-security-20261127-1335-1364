from __future__ import annotations

import argparse
import json
from pathlib import Path

from .model import IsolationRule, load_board
from .plot import export_public_package, write_stack_overlay
from .review import load_package, review_fabrication
from .svgio import read_drill_csv


def _parse_pairs(values: list[str]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for value in values:
        if value.count(":") != 1:
            raise argparse.ArgumentTypeError("required pair must be A:B")
        pairs.append(tuple(value.split(":", 1)))  # type: ignore[arg-type]
    return pairs


def _parse_groups(values: list[str]) -> list[IsolationRule]:
    rules: list[IsolationRule] = []
    for value in values:
        if value.count(":") != 1:
            raise argparse.ArgumentTypeError("isolation group must be A,B:C,D")
        left, right = value.split(":", 1)
        rules.append(IsolationRule(left, right))
    return rules


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Public SVG artwork export and geometry continuity review")
    sub = parser.add_subparsers(dest="command", required=True)

    export = sub.add_parser("export", help="export fixed public interface area")
    export.add_argument("board_json")
    export.add_argument("out_dir")
    export.add_argument("--stack", help="optional local diagnostic stack SVG (not a deliverable)")

    review = sub.add_parser("review", help="rebuild connectivity from delivered SVG and actual drill CSV")
    review.add_argument("--package", required=True)
    review.add_argument("--actual-drill", required=True)
    review.add_argument("--required", action="append", default=[], help="terminal pair A:B; repeatable")
    review.add_argument("--expect-open", action="append", default=[], help="terminal pair A:B that must remain open; repeatable")
    review.add_argument(
        "--allow-npth",
        action="append",
        default=[],
        metavar="X,Y",
        help="expected plated hole coordinate allowed by work order to be non-plated; repeatable",
    )
    review.add_argument(
        "--isolate",
        action="append",
        default=[],
        help="terminal groups A,B:C,D that must remain isolated; repeatable",
    )
    review.add_argument("--tolerance", type=float, default=0.05)
    review.add_argument("--report", help="write text report in addition to stdout")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "export":
        board = load_board(args.board_json)
        hashes = export_public_package(board, args.out_dir)
        print(json.dumps(hashes, indent=2, sort_keys=True))
        if args.stack:
            write_stack_overlay(board, args.stack)
        return 0

    shapes, expected_holes, terminals = load_package(args.package)
    actual_holes = read_drill_csv(args.actual_drill)
    pairs = _parse_pairs(args.required)
    open_pairs = _parse_pairs(args.expect_open)
    allowed_npth = set()
    for value in args.allow_npth:
        try:
            x_text, y_text = value.split(",", 1)
            allowed_npth.add((float(x_text), float(y_text)))
        except ValueError as exc:
            raise argparse.ArgumentTypeError("--allow-npth must be X,Y") from exc
    group_rules = _parse_groups(args.isolate)
    internal_nets: dict[str, str] = {}
    isolation: list[IsolationRule] = []
    for index, rule in enumerate(group_rules):
        net_a = f"GROUP_A_{index}"
        net_b = f"GROUP_B_{index}"
        for name in rule.a.split(","):
            if name:
                internal_nets[name] = net_a
        for name in rule.b.split(","):
            if name:
                internal_nets[name] = net_b
        isolation.append(IsolationRule(net_a, net_b))

    report = review_fabrication(
        shapes=shapes,
        actual_holes=actual_holes,
        terminals=terminals,
        expected_holes=expected_holes,
        required_pairs=pairs,
        isolation=isolation,
        internal_nets=internal_nets,
        drill_tolerance=args.tolerance,
        allowed_npth=allowed_npth,
        expected_open_pairs=open_pairs,
    )
    text = report.to_text()
    print(text, end="")
    if args.report:
        Path(args.report).write_text(text, encoding="utf-8")
    return 0 if report.status == "RELEASE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
