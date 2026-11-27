#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
exec python3 run.py serve \
  --host "${HOST:-0.0.0.0}" \
  --port "${PORT:-8000}" \
  --db "${DB_PATH:-data/demo.db}"
