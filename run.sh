#!/usr/bin/env bash
# Run the whole exercise: unit tests, then the end-to-end demo.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
cd "$here"
echo "== unit tests =="
python3 -m unittest discover -s tests -v
echo
echo "== end-to-end demo =="
python3 -m scenarios.demo
