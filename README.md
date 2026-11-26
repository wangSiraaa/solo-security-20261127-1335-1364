# Public draw: commitment, sealing, and independent verification

A small, standard-library-only Python service for a two-seat public draw where
an outside reader can recompute the result from truly published material — even
when the person who did the draw is temporarily absent.

The answer to the opening question — *"can the audience independently verify
this round was not re-drawn?"* — is **yes**, because:

* the roster, its order and the round's mixing rule are **sealed** (SHA-256) at
  creation, before any reveal is accepted;
* each participant's value is bound by a **salted commitment** to its round and
  ticket, and the seed mixes **every** revealed value in sealed roster order, so
  a partial view cannot be used to pick a favourable winner;
* a round with complete material is **locked** and *must* open — it cannot be
  postponed, and a re-issue reproduces the **identical packet hash**.

Nothing here needs a third-party library. Only `hashlib`, `json`, `secrets`,
`dataclasses` and friends are used.

## The rules that are enforced

| Rule | Where | Test |
| --- | --- | --- |
| Two seats; one household wins at most one seat | `crypto.compute_draw` | `test_crypto` |
| One reservation settles at most once | `service.claim` / claims ledger | `test_service` |
| Replacement follows the **originally sealed** recomputable order | `crypto.promote_replacements` | `test_incidents` |
| Sealed (roster, order, quota, mixing rule, custody plan) before reveals | `service.create_round`/`seal` | `test_service` |
| Complete material must open; a locked round cannot be postponed | `service.open_round`, `postpone` | `test_service` |
| A postponed round publishes **no** winners | `service.postpone` | `test_service` |
| Reopen (补开) needs a fresh public witness | `service.reopen` | `test_service` |
| Re-issue (同轮重发) is byte-identical | `service.reissue` | `test_service` |
| No partial reveal may be seen before locking | `service.first_reveal` | `test_service` |
| Round 1 packet survives later rounds | per-round storage | `test_service` |

## One witness, one shift (the trade-off)

The brief pins a genuine conflict: a single witness can either oversee **early
two-cabinet custody** (so 甲 can hand in a sealed original up front and then be
absent) **or** host **opening-day synchronous sealing** (so 乙 can keep only a
commitment until opening).  It cannot do both.

The service models this explicitly.  A round is created with one
`witness_shift`, and every ticket carries a delivery `source`:

* `witness_shift="escrow"` → every source must be `escrow`.  Originals are
  XOR-split 2-of-2 into two witness cabinets.  At opening the cabinets release
  each original only against **two matching receipts**.  An **absent depositor
  does not stall** the draw, because the cabinet still supplies the same
  original.
* `witness_shift="opening"` → every source must be `onsite`.  Originals are
  handed in on opening day.  A **missing original postpones** the whole round,
  and an offline cabinet is irrelevant because custody was never relied upon.

`create_round` **rejects** a mixed round (a single shift cannot serve both 甲 and
乙).  This is the enforced trade-off, not a suggestion.  `scenarios.witness_shift_choice`
prints the exclusive capabilities.

The two required incident demos:

* **`escrow-absent`** — 甲 escrows early then is absent; the cabinets hand over;
  the round opens and verifies.
* **`cabinet-offline`** — a cabinet is offline but all submitters are present;
  the round still opens and verifies (deadline-style round, cabinet irrelevant).

## First-reveal gate

Reveals are stored privately and only exposed by `first_reveal(round_id)` once
the round is **LOCKED** (all originals present / retrieval condition met).  The
`POSTPONABLE → LOCKED` transition is the exact boundary between “may still be
postponed” and “the draw can no longer change”.  Escrow recovery is **atomic**:
if a cabinet is offline, nothing is staged, so a partial set can never leak.

## The public package

After opening, a packet is published and kept forever per round id:

```json
{
  "round_id": "...",
  "witness_shift": "escrow",
  "seal": { "roster": [...], "quota": 2, "mixing_rule": "...", "cutoff": "..." },
  "seal_hash": "...",
  "commitments": { "T1": "<commit>", ... },
  "reveals":     { "T1": {"salt": "...", "value": "..."}, ... },
  "seed": "...",
  "draw_order": [ ... ],
  "draw_winners": [ ... ],
  "winners": [ ... ],
  "replacements": [ ... ],
  "packet_hash": "..."
}
```

An outside reader verifies it with `verify_package(pkg)`, which recomputes:
packet hash → seal digest → commitment binding → stray reveals → source/shift
consistency → cutoff → seed → draw order → draw winners → household uniqueness →
replacement replay → effective seats.

## What tampering is caught

`tamper` demo (and `test_service.TestAcceptanceSequence`):

1. A reviewer recomputes the winners and issues a claim receipt for one.
2. Swapping one reveal for a value that contradicts its commitment makes the
   package **fail** (`commitments_bind_reveals`).
3. Restoring the original reveal makes it **pass** again.
4. With the claims ledger showing the seat already handed out, re-claiming the
   same reservation is **refused** (`DuplicateSettlement`).

## Run it

```bash
python3 -m lottery baseline
python3 -m lottery tamper
python3 -m lottery duplicate-claim
python3 -m lottery escrow-absent
python3 -m lottery cabinet-offline
python3 -m lottery postpone-reopen
python3 -m lottery reissue
python3 -m lottery shift
python3 -m lottery verify path/to/package.json
python3 -m lottery audit  path/to/event-log.json
```

Verify an external packet:

```bash
python3 -m lottery verify my_round.json      # exits 0 only if it recomputes
```

## Tests

```bash
python3 -m unittest discover -s tests -v
```

70 tests cover the primitives, the custodial cabinets, the draw lifecycle, both
incidents, the witness-shift trade-off, replacement ordering, tamper detection,
the event chain, round isolation, and the CLI.

## Layout

```
lottery/
  crypto.py         digests, commitments, mix/order, 2-of-2 split, replacement rule
  cabinets.py       mock two-cabinet custody (matching receipts to release)
  models.py         records, event log + hash chain, verification result
  service.py        round lifecycle: seal, commit, reveal, lock, open, postpone, claim
  public_package.py public packet I/O + independent verifier
  audit.py          event-sourced classification (延期 / 补开 / 同轮重发)
  scenarios.py      incident builders and the shift trade-off
  __main__.py       CLI demos
tests/
  test_crypto.py  test_service.py  test_incidents.py  test_verifier.py  test_cli.py
```
