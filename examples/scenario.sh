#!/usr/bin/env bash
# The exact two-identical-sentence scenario, driven through the CLI in
# separate processes so persistence/reopen is genuinely exercised.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

ROOT="${1:-$REPO/cli-run}"
rm -rf "$ROOT"
mkdir -p "$ROOT/exchange"

BASE='潮水退回桥下。'
SECOND='潮水漫过桥下。'
A1CHANGE='潮光退回桥下。'
REOPEN='潮水涌过桥下。'
CANARY='CLI-CANARY-PRIVATE-NOTE-551：第一句旁注，不得出现在合作社一侧。'

CE="python3 -m collab_editor"

$CE init --root "$ROOT/museum" --role museum --author museum \
  --anchor "A1=$BASE" --anchor "A2=$BASE"
$CE deliver --from "$ROOT/museum" --to "$ROOT/coop" \
  --packet "$ROOT/exchange/00-delivery.json"

cmp "$ROOT/museum/public.txt" "$ROOT/coop/public.txt"

# Coop changes the second stable anchor, then undoes and redoes it offline.
$CE edit --root "$ROOT/coop" --anchor A2 --old "$BASE" --text "$SECOND" >/dev/null
$CE undo --root "$ROOT/coop" >/dev/null
$CE redo --root "$ROOT/coop" >/dev/null
$CE push --root "$ROOT/coop" --out "$ROOT/exchange/10-coop-second.json" >/dev/null

# Museum-only note before A1.  It has no public operation and no conflict.
$CE note --root "$ROOT/museum" --anchor A1 --text "$CANARY" >/dev/null
$CE merge --root "$ROOT/museum" \
  --packet "$ROOT/exchange/10-coop-second.json" \
  --answer-out "$ROOT/exchange/11-museum-answer.json" >"$ROOT/merge-1.json"

python3 - "$ROOT/merge-1.json" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
assert r["status"] == "merged", r
assert r["conflicts"] == [], r["conflicts"]
assert {h["anchor"] for h in r["affected_locations"]} == {"A2"}, r
PY

# Same public anchor as the private note, one public character elsewhere.
$CE edit --root "$ROOT/museum" --anchor A1 --old "$BASE" --text "$A1CHANGE" >/dev/null
$CE push --root "$ROOT/museum" --peer coop \
  --out "$ROOT/exchange/20-museum-a1.json" >/dev/null
$CE merge --root "$ROOT/coop" \
  --packet "$ROOT/exchange/20-museum-a1.json" \
  --answer-out "$ROOT/exchange/21-coop-answer.json" >"$ROOT/merge-2.json"
$CE ack --root "$ROOT/museum" --answer "$ROOT/exchange/21-coop-answer.json" >/dev/null

python3 - "$ROOT/merge-2.json" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
assert r["status"] == "merged", r
assert len(r["affected_locations"]) == 1, r["affected_locations"]
assert r["affected_locations"][0]["anchor"] == "A1", r
assert r["conflicts"] == [], r
PY

# A new CLI process reopens coop and continues after confirmation.
$CE edit --root "$ROOT/coop" --anchor A2 --old "$SECOND" --text "$REOPEN" >/dev/null
$CE push --root "$ROOT/coop" --out "$ROOT/exchange/30-reopen.json" >/dev/null
$CE merge --root "$ROOT/museum" --packet "$ROOT/exchange/30-reopen.json" \
  --answer-out "$ROOT/exchange/31-answer.json" >"$ROOT/merge-3.json"
$CE ack --root "$ROOT/coop" --answer "$ROOT/exchange/31-answer.json" >/dev/null

# Privacy boundary audit over every counterpart-readable location.
$CE audit \
  --external "$ROOT/coop" \
  --external "$ROOT/exchange" \
  --external "$ROOT/museum/outbox" \
  --external "$ROOT/museum/reports" \
  --canary "$CANARY" >"$ROOT/audit.json"

python3 - "$ROOT" <<'PY'
import json, sys
from pathlib import Path
root = Path(sys.argv[1])
audit = json.loads((root / "audit.json").read_text())
assert audit["clean"], audit
public = (root / "museum/public.txt").read_text()
assert "潮光退回桥下。" in public
assert "潮水涌过桥下。" in public
assert "CLI-CANARY" not in public
private = json.loads((root / "museum/private/private.json").read_text())
assert private["annotations"][0]["full_text_start"] == 0
print(f"CLI scenario passed: {root}")
PY
