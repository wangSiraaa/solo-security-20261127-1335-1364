#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

python3 scripts/build_package.py
PYTHONPATH="$ROOT" python3 -m unittest discover -s tests
python3 scripts/acceptance_demo.py
python3 scripts/run_replays.py
python3 scripts/summarize.py

echo
echo "Done. Records:"
echo "  records/summary.md"
echo "  records/acceptance-demo.md"
echo "  records/replay-healthy.md"
echo "  records/replay-flaky-wait.md"
echo "  records/whatif-flaky-burn-early.md"
