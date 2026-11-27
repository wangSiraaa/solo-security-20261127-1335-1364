# Random Practice-Material Issuance Bench

A teaching-lab simulator for handing out **practice** random material under a
written contract. It consumes only prefabricated bit strings and synthetic
device declarations -- it never produces or stores a real key.

The contract written for the receiving lab is [`docs/CHARTER.md`](docs/CHARTER.md).
Read that first; it is the deliverable.

## What it demonstrates

The bench is built around one rule:

> Independence is a **provenance** property. Two pieces share a source iff they
> come from the same `(pool_id, epoch)`. Literal bit equality proves nothing;
> literal name differences prove nothing. A shared source is counted once.

On top of that rule it implements the accepted methods and the serving state:

* **Method A -- partitioned stock** (the chosen contract): draw from a pool,
  keep the cut-off leftover fragment; disjoint fragments of one guaranteed
  batch are independent, but a shared source never multiplies capacity.
* **Method B -- combiner**: XOR equal-length paths; the output is guaranteed
  only if one *head* path is genuinely independent of the rest.
* **Declarations**: only pools backed by an accepted (non-synthetic) feed can
  back an independence promise. Synthetic-only claims are refused.
* **Pause**: a paused pool contributes nothing to the balance until re-verified;
  already-issued receipts stay valid.
* **Idempotent re-claim**: re-query by order number returns the *same* receipt
  and material plus the true live balance, with no double-spend.

## Layout

```
bench/content.py    device declarations (synthetic) + provenance model
bench/reserve.py    physical pools of prefabricated bits; deterministic slices
bench/ledger.py     idempotent consumption marks (no bit comparison)
bench/combiner.py   the written combiner contract + structural combine
bench/engine.py     IssuanceEngine: pool classification, balances, Method A/B
bench/device.py     the four-step narrative + pause + combiner demos
bench/cli.py        command-line entry point
tests/test_bench.py property tests
docs/CHARTER.md     the contract handed to the receiving lab
```

## Run it

```bash
python3 -m bench.cli                 # the four-step narrative (Method A)
python3 -m bench.cli --all           # + pause scenario + combiner demo
python3 -m bench.cli --all --json    # machine-readable transcript
python3 -m unittest discover -s tests -v
```

The narrative prints, in order: the two real orders, the baseline capacity, the
same-source collapse (only declarations changed), the restore-and-bigger-length
balance check, and the lost-reply re-fetch.

## The promise, precisely

For a pool of `P` live, accepted, unpaused bytes and an order length `L`:

* `independent_bytes` = sum of live bytes over **distinct** accepted, unpaused
  pools;
* `max_independent_orders(L)` = `sum` over those pools of `floor(P / L)`.

A second chassis naming the same pool adds zero. A paused pool is subtracted.
Spent slices are never re-offered as separately independent.
