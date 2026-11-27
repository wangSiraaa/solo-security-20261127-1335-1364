#!/usr/bin/env bash
# Human-readable acceptance sequence. JSON results are printed at each step.
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
H="$ROOT/handoff"
STATE="${HANDOFF_STATE:-/tmp/peripheral-acceptance.json}"
rm -f "$STATE" "$STATE.lock"

"$H" --state "$STATE" seed

say() { printf '\n### %s\n' "$1"; }

say '1. trusted original reader while fixture is locked'
"$H" --state "$STATE" call --op input --at T1 \
  --interface J2 --capability read_sample \
  --value SPEC-V-001 --order WO-V --fixture F1

say '2. reconnect same port with unconfirmed replacement'
"$H" --state "$STATE" call --op remove --interface J2 --reason reader_replaced >/dev/null
"$H" --state "$STATE" call --op install --interface J2 \
  --component BLK-READ-2 --circuit C-SCAN >/dev/null
"$H" --state "$STATE" call --op witness --interface J2 --capability read_sample
"$H" --state "$STATE" call --op freeze --circuit C-SCAN --reason identity_unconfirmed >/dev/null
"$H" --state "$STATE" call --op input --at T2 \
  --interface J2 --capability read_sample \
  --value SPEC-V-002 --order WO-V --fixture F1

say 'Independent knob circuit still posts'
"$H" --state "$STATE" call --op input --at T3 \
  --interface J1 --capability turn_page \
  --value page=2 --order WO-V --fixture F1

say '3. restore trusted part, then put the next pattern in F1'
"$H" --state "$STATE" call --op remove --interface J2 >/dev/null
"$H" --state "$STATE" call --op install --interface J2 \
  --component BLK-DUO-1 --circuit C-SCAN >/dev/null
"$H" --state "$STATE" call --op witness --interface J2 --capability read_sample >/dev/null
"$H" --state "$STATE" call --op pattern_unload --fixture F1 >/dev/null
"$H" --state "$STATE" call --op fixture_load --fixture F1 \
  --pattern P-RELOAD --locked >/dev/null
"$H" --state "$STATE" call --op input --at T4 \
  --interface J2 --capability read_sample \
  --value SPEC-R-NEXT --order WO-V --fixture F1

say 'Claim to original order must fail; claim to WO-R may succeed'
"$H" --state "$STATE" call --op claim --quarantine Q-2 --order WO-V || true
"$H" --state "$STATE" call --op claim --quarantine Q-2 --order WO-R

say 'Final WO-V and open quarantine'
"$H" --state "$STATE" order WO-V
"$H" --state "$STATE" quarantine
