"""Command-line entry point: run the teaching-lab narrative and print it."""

from __future__ import annotations

import argparse
import json

from .device import (
    run_combiner_demo,
    run_narrative,
    run_pause_scenario,
    standard_bench,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--length", type=int, default=32, help="bytes per practice string")
    parser.add_argument("--pool-bytes", type=int, default=64, help="bytes per reserve pool")
    parser.add_argument("--json", action="store_true", help="machine-readable JSON")
    parser.add_argument(
        "--all", action="store_true", help="also run the pause and combiner demos"
    )
    args = parser.parse_args(argv)

    # The chosen contract for the receiving lab is METHOD A (partitioned
    # stock); it is what the requested four-step narrative exercises.
    engine, runner = standard_bench(method="partition", pool_bytes=args.pool_bytes)
    transcript = run_narrative(engine, runner, length=args.length)

    if args.all:
        transcript += run_pause_scenario(args.length)
        transcript += run_combiner_demo()

    if args.json:
        print(json.dumps(transcript, indent=2, ensure_ascii=False))
        return 0
    _pretty_print(transcript)
    return 0


def _pretty_print(transcript) -> None:
    for block in transcript:
        print("=" * 74)
        print(f"STEP: {block.get('step', '?')}")
        print("-" * 74)
        print(json.dumps(block, indent=2, ensure_ascii=False))
        print()


if __name__ == "__main__":
    raise SystemExit(main())
