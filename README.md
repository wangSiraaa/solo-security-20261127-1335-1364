# Confidential diagnostic channel (虚构寄件程序)

A small, dependency-free Python package implementing a **confidential
diagnostic channel** for a fictional shipping program, plus the outsourced
display console that observes only its public output.

The programming exercise asks a specific question:

> A customer-service diagnostic message is written out in four parts. Can the
> outsourced console still correctly point at the failing step?

Yes — but only if two rules are respected:

1. **Reassemble by local stream identity + explicit byte offset**, never by
   accidental byte adjacency. Two concurrent components interleave, and two
   unrelated fragments can end up side by side.
2. **Only provably-public bytes may cross to the console.** The confidential
   drill marker (演练标记) is invisible on the outer wire — including in packet
   lengths and merge relationships.

## The trust boundary

| Inner world (local)                              | Outer world (outsourced console)              |
| ------------------------------------------------ | --------------------------------------------- |
| business components, trusted relay, local audit  | public step name, public success/failure, id  |
| drill marker (演练标记) — **confidential**        | never sees the marker, never sees chunking    |

`:mod:`diag.wire` has two disjoint encodings:

- `encode_internal` / `split_internal` — the **inner** message (marker included)
  and its fragment records.
- `encode_frame` / `decode_frame` — the **outer** frame: a canonical,
  length-prefixed, CRC-checked event built **only** from public fields.

The outer frame has no representation for the marker at all, so marker changes
cannot alter outer bytes or lengths. That is what makes *non-interference* a
testable byte-level property rather than a promise.

## Two supported routes

### Route A — trusted relay assembles, then forwards (`diag.relay.TrustedRelay`)

- Reassembles each inner stream by `stream_id` + byte offset.
- Emits an outer frame only once the public fields pass their integrity check.
- Makes the **stream-break / waiting boundary explicit** (`waiting_streams`,
  `stall`, `close`): incomplete streams are held while bytes may still arrive; a
  stall (断流) evicts the buffer so a truncated message can never leak a partial
  public event. A *complete* stream is promoted the instant its last fragment
  arrives and is never dropped by a late stall.

### Route B — typed fields, public projection (`TypedEmitter` / `LegacyAdapter`)

- Modern components write `TypedEvent`; the trusted side projects **only** the
  public fields into a standalone outer event.
- `LegacyAdapter` is the *limited* bridge for old segment-writing components:
  it reassembles one declared stream by offset and refuses to forward anything
  it cannot prove public. Fragments that never complete (or fail the check) stay
  inside.

## The outsourced console (`diag.console`)

It consumes **only** delivered public frames plus a `StepTable` the duty officer
(值班员) registers by hand. It never infers order from arrival order.

For each receipt it reports:

| Status              | Meaning                                                    |
| ------------------- | ---------------------------------------------------------- |
| `DONE`              | all registered stages confirmed successful                 |
| `FAILED`            | first definite failure, located at a specific step         |
| `WAITING`           | first not-yet-delivered stage, named                        |
| `AWAITING_BASIS`    | 依据尚缺: a failure was seen, but a precursor receipt is missing, so the *first* definite failure cannot yet be proven; names the missing precursor |
| `EMPTY`             | nothing delivered for this receipt                          |

Crucially:

- A **normal failure receipt is never blanked**: as long as its precursors are
  confirmed, the console names the exact failing step.
- A failure that **overtakes its precursor** is shown as `依据尚缺` naming the
  precursor — until the duty officer supplies that public receipt (the inner
  program can be off). The report is then fetched again by the original receipt
  id and the failure step becomes confirmable.
- An **unfamiliar step name** is held as `AWAITING_BASIS` until the officer
  registers it and its place in the sequence with `StepTable.register(name, rank)`.

## Tests

The suite covers every scenario in the brief (four-part write, interleaved
components with byte-adjacent halves, marker straddling two legacy writes,
non-interference of marker vs. failure step, out-of-order `依据尚缺` then
resolution, ordinary "done", operator registration of a stranger step, and a
restored batch with one precursor withheld while another shipment still
delivers).

pytest is not installed in this environment (no network), so the suite ships
with a dependency-free runner that mirrors pytest's behavior for the one
feature used (`pytest.raises`):

```sh
python3 run_tests.py      # offline, no dependencies
python3 -m pytest -q      # works too, if pytest is available
```

An executable walkthrough of the narrative:

```sh
python3 demo.py
```

## Layout

```
diag/model.py         value types: TypedEvent, PublicEvent, Report, Status, Outcome
diag/wire.py          inner/fragment/outer encodings (the trust boundary)
diag/assembly.py      demux by stream id + reassemble by offset; prove-public gate
diag/relay.py         Route A (TrustedRelay) + Route B (TypedEmitter/LegacyAdapter)
diag/console.py       outsourced console: step table, merging, report state machine
diag/interleave.py    deterministic concurrency simulation for tests/drills
tests/test_channel.py the scenario suite
demo.py               executable narrative
run_tests.py          offline test runner
```
