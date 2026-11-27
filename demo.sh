#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

echo "== Unit tests =="
python3 -m unittest discover -s tests

echo
echo "== Built-in end-to-end demo =="
python3 -m collab_editor demo --root /workspace/demo-run >/workspace/demo-run-cli.json
python3 - <<'PY'
import json
r = json.load(open('/workspace/demo-run-cli.json'))
print(f"demo ok={r['ok']} checks={r['check_count']}")
if not r['ok']:
    raise SystemExit(1)
PY

echo
echo "== Separate-process CLI scenario =="
bash examples/scenario.sh /workspace/cli-run

echo
echo "Privacy audit and all scenarios passed."
echo "See /workspace/demo-run/DEMO-REPORT.md and /workspace/cli-run/audit.json"
