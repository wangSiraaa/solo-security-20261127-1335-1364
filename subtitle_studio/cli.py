from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from .errors import StaleAdmissionError, StudioError
from .studio import FontStudio


def _studio(args: argparse.Namespace) -> FontStudio:
    store = getattr(args, "store", None) or ".subtitle_studio"
    return FontStudio(store_dir=store)


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def cmd_import(args: argparse.Namespace) -> int:
    studio = _studio(args)
    receipt = studio.import_path(args.package)
    if args.format == "json":
        _print_json(receipt)
    else:
        print(f"imported {receipt['package_id']} ({receipt['name']} {receipt['version']})")
        print(f"  hash: {receipt['content_hash']}")
        print(f"  admission: {receipt['admission_id']}")
        print(f"  classic whole-glyph set ({receipt['classic_glyph_count']}): {''.join(receipt['classic_glyphs'])}")
        if receipt["rejected"]:
            print(f"  not classically admitted: {', '.join(receipt['rejected'])}")
        print(f"  store: {Path(args.store or '.subtitle_studio').resolve()}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    studio = _studio(args)
    rows = studio.list_packages()
    if args.format == "json":
        _print_json(rows)
    else:
        for row in rows:
            print(f"{row['package_id']}\t{row['content_hash'][:12]}\t{row['admission_id']}")
        if not rows:
            print("(no imported packages)")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    studio = _studio(args)
    try:
        result = studio.verify_admission(args.admission_id)
    except StaleAdmissionError as exc:
        payload = {"valid": False, "error": "stale", "message": str(exc)}
        if args.format == "json":
            _print_json(payload)
        else:
            print(f"STALE: {exc}")
        return 3
    if args.format == "json":
        _print_json(result)
    else:
        print(f"valid admission {result['admission_id']} for {result['package_id']}@{result['content_hash'][:12]}")
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    studio = _studio(args)
    report = studio.analyze(args.package_id)
    if args.format == "json":
        _print_json(report)
    else:
        print(f"package {report['package_id']}@{report['content_hash'][:12]}")
        print(f"admission {report['admission_id']}")
        print(f"classic glyphs: {''.join(report['classic_glyphs'])}")
        if report["cyclic_primitives"]:
            print(f"cyclic/tainted primitives: {', '.join(report['cyclic_primitives'])}")
        for char, glyph in report["glyphs"].items():
            bound = glyph["bound"]
            if bound["finite"]:
                print(f"  {char}: work<={bound['work']}, cells<={bound['cell_writes']}, reasons={glyph['reasons']}")
            else:
                print(f"  {char}: infinite/cyclic, reasons={glyph['reasons']}")
    return 0


def cmd_promises(args: argparse.Namespace) -> int:
    studio = _studio(args)
    result = studio.promised_chars(
        args.package_id,
        font_size=args.font_size,
        line_width=args.line_width,
        mode=args.mode,
    )
    if args.format == "json":
        _print_json(result)
    else:
        print(f"{args.mode} promises for {args.package_id} at font_size={args.font_size}, line_width={args.line_width}")
        print("".join(result["promised_chars"]))
        print(result["source"])
    return 0


def cmd_lock(args: argparse.Namespace) -> int:
    studio = _studio(args)
    lock = studio.lock_layout(
        args.text,
        package_id=args.package_id,
        font_size=args.font_size,
        line_width=args.line_width,
        mode=args.mode,
        canvas_width=args.canvas_width,
        canvas_height=args.canvas_height,
        work_budget=args.work_budget,
        cell_budget=args.cell_budget,
    )
    if args.format == "json":
        _print_json(lock)
    else:
        print(f"locked: {lock['lock_id']}")
        print(f"promise: {lock['promise']}")
        print(lock["canvas"]["unicode"])
        for glyph in lock["glyph_results"]:
            if glyph["outcome"] != "committed":
                print(
                    f"  {glyph['requested_char']} -> {glyph['rendered_char']} "
                    f"({glyph['outcome']})"
                )
        print(f"read back with: python -m subtitle_studio canvas {lock['lock_id']}")
    return 0


def cmd_canvas(args: argparse.Namespace) -> int:
    studio = _studio(args)
    readback = studio.read_canvas(args.lock_id)
    if args.format == "json":
        _print_json(readback.to_dict())
    else:
        print(f"lock {readback.lock_id}  canvas {readback.width}x{readback.height}  filled={readback.filled_cells}")
        print(readback.unicode_art)
        if args.matrix:
            _print_json(readback.matrix)
    return 0


def cmd_cut_classic(args: argparse.Namespace) -> int:
    studio = _studio(args)
    chars = list(args.chars) if args.chars else None
    cut = studio.classic_cut(args.package_id, chars=chars, out_path=args.output)
    if args.format == "json":
        _print_json(cut if not args.output else {"output": str(Path(args.output).resolve()), "id": cut["id"], "glyphs": list(cut["glyphs"])})
    else:
        print(f"wrote classic cut to {Path(args.output).resolve()}")
        print(f"  id: {cut['id']}")
        print(f"  guaranteed glyphs: {''.join(cut['glyphs'])}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .server import run_server

    run_server(host=args.host, port=args.port, store_dir=args.store or ".subtitle_studio")
    return 0


def cmd_selftest(args: argparse.Namespace) -> int:
    from .acceptance import run_acceptance

    result = run_acceptance(verbose=not args.quiet)
    if args.format == "json":
        _print_json(result)
    return 0 if result["ok"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m subtitle_studio",
        description="Restricted declarative font importer and local grid subtitle machine.",
    )
    parser.add_argument("--store", default=".subtitle_studio", help="JSON lock/store directory")
    common = argparse.ArgumentParser(add_help=False)
    # SUPPRESS keeps a global --store before the subcommand from being reset.
    common.add_argument("--store", default=argparse.SUPPRESS, help="JSON lock/store directory")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_parser(name: str, **kwargs: Any) -> argparse.ArgumentParser:
        return sub.add_parser(name, parents=[common], **kwargs)

    p_import = add_parser("import", help="import and analyze a declarative package")
    p_import.add_argument("package")
    p_import.add_argument("--format", choices=["text", "json"], default="text")
    p_import.set_defaults(func=cmd_import)

    p_list = add_parser("list", help="list current package revisions")
    p_list.add_argument("--format", choices=["text", "json"], default="text")
    p_list.set_defaults(func=cmd_list)

    p_verify = add_parser("verify-admission", help="verify an admission against current revision")
    p_verify.add_argument("admission_id")
    p_verify.add_argument("--format", choices=["text", "json"], default="text")
    p_verify.set_defaults(func=cmd_verify)

    p_analyze = add_parser("analyze", help="show dependencies and static expansion bounds")
    p_analyze.add_argument("package_id")
    p_analyze.add_argument("--format", choices=["text", "json"], default="text")
    p_analyze.set_defaults(func=cmd_analyze)

    p_promises = add_parser("promises", help="list chars promised for one locked profile")
    p_promises.add_argument("package_id")
    p_promises.add_argument("--font-size", type=int, default=5)
    p_promises.add_argument("--line-width", type=int, default=16)
    p_promises.add_argument("--mode", choices=["live", "classic"], default="live")
    p_promises.add_argument("--format", choices=["text", "json"], default="text")
    p_promises.set_defaults(func=cmd_promises)

    p_lock = add_parser("lock", help="lock one layout and render its virtual canvas")
    p_lock.add_argument("text")
    p_lock.add_argument("--package", dest="package_id", default="fangyuan")
    p_lock.add_argument("--font-size", type=int, default=5)
    p_lock.add_argument("--line-width", type=int, default=16)
    p_lock.add_argument("--mode", choices=["live", "classic"], default="live")
    p_lock.add_argument("--canvas-width", type=int, default=None)
    p_lock.add_argument("--canvas-height", type=int, default=None)
    p_lock.add_argument("--work-budget", type=int, default=None)
    p_lock.add_argument("--cell-budget", type=int, default=None)
    p_lock.add_argument("--format", choices=["text", "json"], default="text")
    p_lock.set_defaults(func=cmd_lock)

    p_canvas = add_parser("canvas", help="read back a locked virtual canvas")
    p_canvas.add_argument("lock_id")
    p_canvas.add_argument("--matrix", action="store_true")
    p_canvas.add_argument("--format", choices=["text", "json"], default="text")
    p_canvas.set_defaults(func=cmd_canvas)

    p_cut = add_parser("cut-classic", help="cut statically guaranteed old-machine font pack")
    p_cut.add_argument("package_id")
    p_cut.add_argument("output")
    p_cut.add_argument("--chars", nargs="*", help="optional explicit admitted chars")
    p_cut.add_argument("--format", choices=["text", "json"], default="text")
    p_cut.set_defaults(func=cmd_cut_classic)

    p_serve = add_parser("serve", help="run local JSON HTTP API")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8765)
    p_serve.set_defaults(func=cmd_serve)

    p_test = add_parser("selftest", aliases=["acceptance"], help="run the built-in acceptance narrative")
    p_test.add_argument("--quiet", action="store_true")
    p_test.add_argument("--format", choices=["text", "json"], default="text")
    p_test.set_defaults(func=cmd_selftest)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except StudioError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"invalid argument or profile: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"error: file not found: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
