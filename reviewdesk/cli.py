from __future__ import annotations

import argparse
import json
from pathlib import Path

from .cabinet import query_word
from .ledger import verify_chain
from .server import serve as serve_cabinet

ROOT = Path(__file__).resolve().parents[1]


def cmd_demo(_args: argparse.Namespace) -> int:
    from .lab import run_all

    report = run_all()
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["school_acceptance_pass"] else 1


def cmd_verify_ledger(_args: argparse.Namespace) -> int:
    result = verify_chain()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 3


def cmd_cabinet_query(args: argparse.Namespace) -> int:
    if args.all:
        registry_path = ROOT / "review-desk" / "cabinets" / "registry.json"
        if not registry_path.exists():
            print(json.dumps({"ok": False, "reason": "cabinet registry missing; run demo first"}, ensure_ascii=False))
            return 2
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        result = {"cabinets": []}
        any_word = False
        for cabinet in registry["cabinets"]:
            events = query_word(cabinet["state"], args.word)
            any_word = any_word or bool(events)
            result["cabinets"].append({**cabinet, "count": len(events), "events": events})
        result["queried_word"] = args.word
        result["received_any_matching_word"] = any_word
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        # Exit 0 for query success; 1 means at least one matching event, useful in scripts.
        return 1 if any_word and args.exit_code_on_hit else 0

    if not args.state:
        raise SystemExit("cabinet query requires --state or --all")
    events = query_word(args.state, args.word)
    print(json.dumps({"state": args.state, "word": args.word, "count": len(events), "events": events}, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if events and args.exit_code_on_hit else 0


def cmd_serve_cabinet(args: argparse.Namespace) -> int:
    return serve_cabinet(["--state", args.state, "--cabinet-id", args.cabinet_id, "--host", args.host, "--port", str(args.port)])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reviewdesk", description="Maintenance port review console")
    sub = parser.add_subparsers(dest="command", required=True)

    demo = sub.add_parser("demo", help="Create/evaluate branches, bundles, classroom and all drills")
    demo.set_defaults(func=cmd_demo)

    ledger = sub.add_parser("verify-ledger", help="Verify append-only review hash chain")
    ledger.set_defaults(func=cmd_verify_ledger)

    cabinet = sub.add_parser("cabinet", help="Query mock receiving cabinets")
    cabinet_sub = cabinet.add_subparsers(dest="cabinet_command", required=True)
    query = cabinet_sub.add_parser("query", help="Query one cabinet or every registered cabinet")
    query.add_argument("--state")
    query.add_argument("--word")
    query.add_argument("--all", action="store_true")
    query.add_argument("--exit-code-on-hit", action="store_true", help="Exit 1 when matching events exist")
    query.set_defaults(func=cmd_cabinet_query)

    serve = cabinet_sub.add_parser("serve", help="Run a mock cabinet HTTP service")
    serve.add_argument("--state", required=True)
    serve.add_argument("--cabinet-id", default="manual-cabinet")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.set_defaults(func=cmd_serve_cabinet)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
