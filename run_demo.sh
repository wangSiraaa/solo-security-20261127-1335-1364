#!/usr/bin/env bash
# Build evidence, exercise the online witness process, and have a separate
# local reviewer verify only the published ZIP deliverables.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

echo "[1/5] Building simulated authorities, manuscripts, receipts, and mutations..."
./coop build

echo "[2/5] Exercising online normal path through a separate HTTP witness process..."
python3 scripts/online_flow.py

echo "[3/5] Checking that the two one-character originals really differ by one character..."
python3 - <<'PY'
from pathlib import Path
import difflib, json
root = Path(__file__).resolve().parent if '__file__' in globals() else Path.cwd()
a = (root / 'deliverables/reader-originals/DENG-YING-v1-tui.txt').read_text(encoding='utf-8')
b = (root / 'deliverables/reader-originals/DENG-YING-v2-qiao.txt').read_text(encoding='utf-8')
sm = difflib.SequenceMatcher(None, a, b)
replacements = [(i1, i2, j1, j2) for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != 'equal']
assert len(replacements) == 1, replacements
i1, i2, j1, j2 = replacements[0]
assert a[i1:i2] == '推' and b[j1:j2] == '敲', (a[i1:i2], b[j1:j2])
print('OK: whole files differ only by 推 -> 敲')
PY

echo
echo "[4/5] Running independent local process from staged ZIP deliverables..."
python3 scripts/external_review.py
python3 scripts/mutation_sequence.py

echo
echo "[5/5] Writing public SHA-256 manifest..."
python3 - <<'PY'
from pathlib import Path
import hashlib
root = Path.cwd() / 'deliverables'
paths = [root / 'verify_independent.py', root / 'policy.json',
         root / 'decision.json', root / 'DECISION.md', root / 'README.md']
paths += sorted((root / 'trust').glob('*'))
paths += sorted((root / 'bundles').glob('*.zip'))
paths += sorted((root / 'reader-originals').glob('*'))
lines = []
for p in paths:
    if p.is_file():
        h = hashlib.sha256(p.read_bytes()).hexdigest()
        lines.append(f'{h}  {p.relative_to(root)}')
(root / 'SHA256SUMS').write_text('\n'.join(lines) + '\n', encoding='utf-8')
print(root / 'SHA256SUMS')
PY

chmod -R a+rX deliverables
chmod 755 deliverables/verify_independent.py

echo
echo "Done. Start with deliverables/README.md and deliverables/reviews/EXTERNAL_REVIEW.md"
