# synth_ime

A shareable local input method whose members' learning can be withdrawn exactly.

The tablet learns "after this word, what word follows" (and which words start a
sentence) from synthetic corpus sentences.  Several members may teach the same
word pair.  When a member leaves, their contribution is removed **to the count**,
so the tablet behaves exactly like one that never contained their teaching —
while words other members also taught still appear.  New sentence starts keep
completing from the remaining members and the public base.

All sentences in tests and examples are built from invented words.

## Architecture

One merged count model on the device, plus a device-sealed **compensating
receipt** per member holding the exact sentences they taught.  Leaving applies
the exact inverse of what they added, which is count-for-count the never-had
baseline.  Ordinary users cannot read the receipts (they are sealed); the
current user's disabled-word list is a post-filter that never changes the model.

See [DESIGN.md](DESIGN.md) for the argument, the rejected per-owner alternative,
and the security/replay analysis.

## Layout

```
synth_ime/
  seal.py      HMAC-SHA256 encrypt-then-MAC seal (stdlib only)
  engine.py    Device: learn / exit / restore / suggest / chain / local words
  corpus.py    synthetic-word corpora
  cli.py       command-line driver
tests/         unittest suite (no third-party deps)
```

## Quick start (Python API)

```python
from synth_ime.engine import Device, MemoryStore

A, B = "a"*64, "b"*64          # each member's private token
d = Device(MemoryStore())
d.set_base(["bra ka lo"])      # public base words
d.learn(A, ["bra ka mi", "bra zu po"])
d.learn(B, ["bra ka zu"])      # B also teaches bra -> ka

print(d.suggest("bra "))       # ['ka', 'zu']  (ka count 3, zu count 1)
d.exit(A)                      # A leaves
print(d.suggest("bra "))       # ['ka']  still there: B + base taught it
```

After `A` exits, `zu` (A-only) disappears and `ka` remains because both the base
corpus and `B` taught it.

## Quick start (CLI)

```bash
export PYTHONPATH=.
TOK_A=$(python3 -c "import secrets;print(secrets.token_hex(32))")
TOK_B=$(python3 -c "import secrets;print(secrets.token_hex(32))")
PY="python3 -m synth_ime.cli --db dev.json"

$PY init
$PY learn alice --token "$TOK_A" "bra ka lo"
$PY learn bob   --token "$TOK_B" "bra ka zu"
$PY suggest "bra "                 # ["ka"]
$PY exit alice --token "$TOK_A"    # revoke by token
$PY suggest "bra "                 # ["ka"]  (bob still taught it)
$PY exit --member-id uXXXXXXXXXXXX # or revoke by id, no token needed
$PY disable ka --as-user alice     # local filter only
$PY suggest "bra " --as-user alice # reroutes past disabled words
```

Use `--key-file dev.key` to keep the sealing key outside the state file.

## Running the tests

```bash
python3 -m unittest discover -s tests -v
```

The suite covers: exact baseline equivalence (randomised brute force),
shared-word survival, multi-member retention, reteach disappearance, order
independence, reboot non-resurrection, idempotent/repeated exit, backup replay
refusal, per-user local filtering, and seal tamper detection.
