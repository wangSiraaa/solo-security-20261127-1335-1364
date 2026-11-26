# Local mail & relay simulator (`mailbox_sim.py`)

A local simulator for a duty dormitory that receives private mail through one
shared relay box. It produces **two separate records** and never mixes them:

| view | method | what it shows |
|------|--------|---------------|
| corridor | `SimResult.corridor` / `ReplayReport.corridors` | only `(beat, slot-count)` for each bag that went by |
| home | `SimResult.outcomes` / `.outcome(recipient)` | each recipient's own letter record |

The corridor never exposes recipient names or bodies — only *when* a bag of
*how many fixed-size slots* passed. Blank filler pieces pad a bag to a chosen
size, so bag size says nothing about how many real letters are inside.

Run the demo and the tests:

```bash
python3 mailbox_sim.py            # demonstration of every scenario
python3 -m unittest -v            # 28 tests
```

## Standard shift: four beats, one bag

* A: if sent, present from beat 1, must be handed over by the **end of beat 2**.
* B: earliest arrival beat 3, must be handed over at the **end of beat 4**.

One bag may pass per shift (capacity 2). The two deadlines **cannot both be
met**, so `plan_shift(target)` decides before the shift which one to honour and
reports the other recipient honestly:

| target | bag beat | target | other |
|--------|----------|--------|-------|
| A | 2 | A on time | B **missed** (arrives only beat 3) |
| B | 4 | B on time | A **late** (beat 4 > deadline 2) |

`plan_shift` never claims both deadlines hold and never sends an all-blank
night. `replay_plan` replays the *same* plan under all four cases
(empty / A only / B only / both) and asserts the corridor record is identical
everywhere — same beats, same sizes.

## Alternate shift: two windows, both deadlines

`alternate_session()` publishes two public windows with different beats and
bag types:

* beat 2, `early-small`, 2 slots
* beat 3, `late-large`, 3 slots

With `max_bags = 2` both may pass, and **both A and B are on time**. This is
the "reusable plan from the shift's public transfer windows": a plan is
derived from the published windows (`design_plan`), not hand-customised, so it
survives to future shifts that expose the same windows.

### Supervisor cancels a window

`Session.affected_by(cancellation)` replays the shift with and without the
cancelled window and reports which recipient's original on-time promise can no
longer be kept:

* cancel `early-small` (beat 2) → **A** must adjust (it loses its only on-time route)
* cancel `late-large` (beat 3) → **B** must adjust

The cancellation *reason* is public and is never used in the decision — only
the window's beat/label matters.

## B-only shift and custody receipts

When only B must be protected, the beat-4 bag is released and the dormitory
signs a `BagReceipt`. A receipt binds `(shift, serial, round, load-beat,
letter-digests)`, so:

* a valid receipt for the real beat-4 bag **does** sign for B;
* a receipt claiming the bag was *loaded before B was produced* does **not**;
* the **previous shift's bag** presented as tonight's bag does **not** (wrong
  shift/serial even when the round matches) — "B is in the bag but the
  dormitory took the previous shift's bag" is reported as not received;
* a letter that is not actually inside the bag does **not** get signed.

## Corridor history is append-only

`CorridorLog.erase()` always raises `PermissionError`: a passage that already
happened cannot be erased by deleting logs afterwards.
