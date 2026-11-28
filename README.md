# calboard — offline calibration-board startup inspection & deployment tool

A pure-Python, hardware-free tool for checking a calibration board's startup
and for synthesizing **reset / clear / window control sequences** before
deployment.  Everything — resets, latency, clearing, retained cells, calibration
writes, and maintenance-window reads — is simulated from a maintainer's board
description.  No real device is touched.

The tool answers the operational questions by *construction*:

* **"A calibration board only reset half — when can the calibration reading be
  published to the common service window?"**  Only when a window's entire read
  set is present, settled, public (no raw tenant cells), and carries a single
  calibration-round generation.  The planner tells you the earliest fixed tick
  that holds in *every* allowed reset interleaving (Mode A), or the runtime
  handshake decides at the spot (Mode B).
* **"What if a second service port wanted the full reading earlier than the
  first could produce it?"**  Windows are evaluated independently: a port that
  already published is never retracted because a different port missed its
  deadline.

## Install / run

No third-party dependencies.  Python 3.10+.

```bash
python3 -m calboard.cli --help
python3 run_tests.py            # 25 scenario tests, no pytest needed
```

Example fixtures live in `examples/` and are generated from `calboard/examples.py`.

## Board description (what a maintainer enters)

```json
{
  "name": "two_domain_local_reset",
  "domains": ["left", "right"],
  "cells": [
    {"name": "left_cal",  "domain": "left",  "kind": "public", "clear_with": ["left"],
     "initial": {"value": null, "gen": null}},
    {"name": "retained_cal", "domain": "right", "kind": "public", "retained": true,
     "initial": {"value": 100, "gen": "gen0"}},
    {"name": "tenant_raw", "domain": "left", "kind": "raw", "retained": true,
     "initial": {"value": 424242, "gen": "gen0"}}
  ],
  "wiring":  [{"from": "raw", "to": "pub"}],
  "links":   [{"a": "left", "b": "right"}],
  "reset_policy": {
    "power_on":          {"order": ["left","right"], "max_stagger": 2, "latency": 1},
    "local_power_loss":  {"order": ["left"],         "max_stagger": 0, "latency": 1}
  }
}
```

* **domains** — independent power / reset domains.
* **cells** — each register is `public` (a calibration result a window may
  publish) or `raw` (tenant metered data that must stay on the board).
  `clear_with` lists the domains whose reset clears it; `retained: true` (empty
  scope) means it survives every reset.
* **wiring** — default dependency edges (`from` must be readable to read `to`).
  A window's read set is the transitive closure, so raw cells reachable through
  wiring are blocked too.
* **links** — finite cross-domain control lines used by Mode B.
* **reset_policy** — per reset kind: the normal order of domains, the bounded
  stagger between their starts, and each reset's settle latency.

## Publication strategies

### Mode A — synthesized fixed-tick control sequence

`synthesize_plan` chooses reset actions, calibration writes, and a window tick,
then **proves** the tick against *every* allowed interleaving (order preserved,
starts nondecreasing, within `max_stagger`).  A window is advertised only if in
every interleaving the read set is present, settled, single-generation, and
free of raw cells.

* It **pays the wait** owed to late dependent domains and to the *actual* state
  of retained cells: the write/window tick is placed after the worst-case
  settle of every written closure cell.
* A retained cell is trusted **only** if its surviving generation already equals
  this round's token.  If the calibration program does not genuinely rewrite it,
  the plan *fails* rather than relabelling stale data (even when the number is
  identical to what a fresh round would produce).
* If a retained cell is changed to clear with a neighbour, the same program now
  leaves an empty register, so the plan is recomputed and reported as failing
  until the program rewrites it.

```bash
python3 -m calboard.cli plan \
  --board examples/two_domain_local_reset.board.json \
  --program examples/two_domain_local_reset.program.json
# -> ok: true, window_tick: 3, interleavings: 3   (when the late domain is in want)

python3 -m calboard.cli prove \
  --board examples/early_second_port.board.json \
  --reset power_on --round gen1 --want fast_cal slow_cal --tick 2
# -> safe_in_all: false; lists the unsafe start assignments
```

### Mode B — cross-domain generation handshake

Each domain carries this round's token over the finite control lines as it
finishes initialising; a window publishes at the first tick all its parts
answer with the **same** generation that the registers actually hold.

* Requires the closure's domains to be link-connected (`handshake` reports the
  finite link budget).
* Handles the "my neighbour regenerated but I did not reset" case: a healthy
  domain still holding an older generation blocks the seal.  The handshake
  cross-checks content, so acks alone never let stale data through.

```bash
python3 -m calboard.cli handshake \
  --board examples/two_domain_local_reset.board.json --want left_cal right_cal
```

## Running a chosen normal reset order (reviewer flow)

A reviewer picks one permitted reset ordering and drives the board with fresh
measurements; each port records exactly which registers it genuinely read:

```bash
python3 -m calboard.cli scenario \
  --board examples/two_ports_local_reset.board.json \
  --run   examples/two_ports_local_reset.scenario.json
```

The scenario JSON supplies `reset_kind`, `starts` (the chosen stagger), new
`writes`, and the `windows`.  Each window record lists the register cells read,
their values/generations, and why publication did or did not happen.  A
`mutations` array (e.g. `[{"cell":"retained_cal","clear_with":["right"]}]`)
changes a cell's clear scope and forces recomputation.

## Invariants the simulator enforces

1. A reset clears **only** cells whose `clear_scope` includes the reset domain;
   retained cells survive.
2. A reset has latency: cleared cells are unsettled for `latency` ticks; reads
   during settle are refused and writes during settle are **lost** (the pending
   clear wins).
3. A window publishes only if its full closure is present, settled, public,
   single-generation, and (if pinned) matches the required generation.  Numeric
   equality is **never** sufficient — provenance is the generation token.
4. A cell with a value but no generation token is never treated as current.
5. Raw tenant cells (directly or via wiring) are never published.
6. Each window is independent: an already-published window is never retracted
   because another window misses its deadline.

## Layout

```
calboard/
  model.py      Board / Cell / wiring / reset policy
  events.py     Event and ReadRequest (fixed & handshake windows)
  sim.py        tick-by-tick simulator + read records
  planner.py    Mode A plan synthesis, interleaving proof, Mode B feasibility
  handshake.py  Mode B runtime ack reconstruction and refusal analysis
  cli.py        command line
  examples.py   bundled boards & programs
examples/       generated JSON fixtures
tests/          scenario tests
run_tests.py    dependency-free test runner
```
