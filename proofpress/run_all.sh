#!/bin/sh
# geometry round-trip self-test, then the full acceptance drill
set -e
cd "$(dirname "$0")"
python3 tests/roundtrip.py
python3 demo/run_demo.py
