#!/usr/bin/env python3
"""Standalone command line shipped to fictional classrooms/reviewers."""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from pathlib import Path
from typing import Any

# Allow execution both as ``python -m mojiao.cli`` and ``python mojiao/cli.py``.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mojiao.config import find_root, load_classroom, resolve_wordlist_paths
from mojiao.dictionary import DictionarySet


def load_entrypoint(spec: str):
    module_name, _, function_name = spec.partition(":")
    if not function_name:
        raise ValueError(f"entrypoint must be module:function, got {spec!r}")
    module = importlib.import_module(module_name)
    return getattr(module, function_name)


def request_from_value(value: str, mode: str) -> dict[str, Any]:
    if mode == "old-id":
        return {"word_id": value, "id": value}
    if mode == "modern-id":
        return {"id": value}
    if mode == "text":
        return {"text": value}
    if value.startswith("W-"):
        return {"id": value}
    if value.isdigit():
        return {"word_id": value, "id": value}
    return {"text": value}


def cmd_lookup(args: argparse.Namespace) -> int:
    root = find_root(args.bundle_root)
    classroom = load_classroom(root)
    if args.offline or classroom.get("network_policy") == "deny":
        os.environ["MOJIAO_OFFLINE"] = "1"
    paths = [Path(p) if Path(p).is_absolute() else root / p for p in args.wordlist] if args.wordlist else resolve_wordlist_paths(root, classroom)
    dictionaries = DictionarySet.load_paths(paths)
    entrypoint_spec = args.entrypoint or classroom["entrypoint"]
    resolve = load_entrypoint(entrypoint_spec)
    request = request_from_value(args.value, args.mode)
    request["offline"] = bool(args.offline or classroom.get("network_policy") == "deny")
    result = resolve(request, dictionaries)
    result.setdefault("entrypoint", entrypoint_spec)
    result.setdefault("classroom_id", classroom.get("classroom_id"))
    if args.human:
        if result.get("status") == "ok":
            print(f"{result.get('word_id') or result.get('legacy_id')}\t{result['canonical']}")
        else:
            print(f"NOT_FOUND\t{args.value}")
    else:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") == "ok" else 4


def cmd_wordlists(args: argparse.Namespace) -> int:
    root = find_root(args.bundle_root)
    classroom = load_classroom(root)
    dictionaries = DictionarySet.load_paths(resolve_wordlist_paths(root, classroom))
    print(json.dumps(dictionaries.versions(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ww", description="Fictional offline proofreading package")
    parser.add_argument("--bundle-root", help="Bundle/classroom root; defaults to auto-discovery")
    sub = parser.add_subparsers(dest="command", required=True)

    lookup = sub.add_parser("lookup", help="Look up one word or word number")
    lookup.add_argument("value")
    lookup.add_argument("--mode", choices=["auto", "old-id", "modern-id", "text"], default="auto")
    lookup.add_argument("--offline", action="store_true", help="Force offline mode")
    lookup.add_argument("--entrypoint", help="Override classroom entrypoint (review/drill use)")
    lookup.add_argument("--wordlist", action="append", help="Explicit word-list path; may be repeated")
    lookup.add_argument("--human", action="store_true", help="Print tab-separated output")
    lookup.set_defaults(func=cmd_lookup)

    lists = sub.add_parser("wordlists", help="List loaded word-list versions")
    lists.set_defaults(func=cmd_wordlists)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
