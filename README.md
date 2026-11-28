# 失电保全 · 复机交付演练台 — power-loss drill

A deterministic, runnable simulation of a teaching machine room losing one of
two power feeds. It answers the operational question:

> **回单上写着“保存成功”的那些练习，在主电源失掉之后，到底还能接着写吗？**

The drill separates the **server process** (which accepts writes, flushes to
disk, and mints signed receipts) from the **recovery process** (a fresh
interpreter that sees only the disk, the power telemetry, and the receipts the
teacher is holding — never the live config).

```
python -m drill run     --root DIR [--mode online|graded] [--policy commit|cache]
                        [--delay-index CLASS/ASSIGN] [--flush-ticks N]
python -m drill recover --root DIR --held HELDDIR [--out FILE]
python -m drill topology
python examples/demo.py [--mode ...] [--policy ...] [--delay-index ...]
python -m unittest tests.test_drill -v
```

## The room

| concept | model |
|---|---|
| 两路电源 | feeds **A**, **B**, each with its own breaker capacity (120 W) |
| 机箱 | **R1** (A-only), **R2** (B-only), **R3** (dual-fed A+B) |
| 电池能量 | single reservoir the battery must fund once a feed is gone |
| 每拍承载 | `flushes_per_tick` — write points that can land per tick |
| 磁盘落盘时间 | `flush_ticks` — ticks a write occupies a lane before landing |
| 备用机启动峰值 | `startup_peak` drawn for one tick at cold start |
| 最低服务时段 | `min_service` — a chassis is never shed inside its paid window |
| 两册作业簿 | `vol1` / `vol2`; some orders write data + index across both |
| 落盘点 | a write is **data**, **index**, then **commit** — each a real disk op |

A submission only becomes **durable** when every one of its write points has
landed (`vol*/wal.log`) *and* its commit line is in `journal.log`. Until then it
is an accepted-but-volatile cache entry that is lost on power-off.

## Power accounting

Per tick, deterministically:

* powered cabinets draw `base_load`; cold starters add `startup_peak`;
* dual-fed cabinets are assigned to the feed that minimises the worst-case
  branch load (`power.best_assignment` searches the assignment);
* a per-feed overload can be shaved by the battery up to `DISCHARGE_RATE` for
  one tick, otherwise a chassis is shed (lowest cost-of-service first, never
  one inside `min_service`);
* sustained load above total capacity drains the battery;
* after a feed is lost, the disk subsystem is isolated onto the battery bus, so
  **every flush draws `cost` from the battery** — this bounds the real flush
  window;
* cross-volume (cross-book-reference) write points are prioritised, so while
  residual energy lasts the referenced order completes first.

## Receipt policy (`--policy`)

* `commit` (**safe, default**): a receipt is minted only after the order is
  fully durable. Near the energy boundary the server downgrades to a
  **pending** receipt instead of promising a save it may not finish.
* `cache` (**unsafe, for demonstration**): a receipt is minted the moment the
  order is accepted. It is then proven a lie — such receipts become `phantom`
  unless the bytes independently reach disk.

## What recovery guarantees

Recovery (`Recovery.recover`) reads **only** `journal.log`, `vol*/wal.log`,
`power.json`, and the held receipts. It refuses to read `cache.json` as durable.

1. **No stale index.** An index record whose referenced data page is not on
   disk is flagged and not served.
2. **No half-receipt.** A confirmed cross-volume order shows *both* halves, or
   is honestly downgraded to `partial` — never a half-presented "saved".
3. **No cache resurrection.** A page or whole order that existed only in the
   volatile cache is reported under `rejected_cache_only` / `phantom`.
4. **Numbers honoured.** Reopen (`reopen`) redeems the exact receipt number that
   was returned, per order.
5. **Independent classrooms.** Each order is verified against its own
   classroom's HMAC key, so C101 and C102 recover independently.

## Topology question

`python -m drill topology` compares the base wiring against the fault where one
branch is re-wired so a single feed is **shared by two chassis**. It reports
which cold-start promises can be honoured together in each case. In the base
wiring R2+R3 can start in the same tick; under the shared-branch fault they
cannot — the fault turns a startup promise into a conflict.

## Fault / scenario knobs

* `--delay-index CLASS/ASSIGN` — slip that order's index write by exactly one
  tick on its first attempt (磁盘落盘顺序变化). The order then either still
  completes (both halves together) or is honestly left `pending`.
* `--flush-ticks N` — make disks slower; in-flight writes stay volatile and are
  lost at the kill if they have not landed.
* `--mode graded` — pre-plan a tiered shutdown / off-site copy: only promise the
  combo whose full flush set fits the safe window; everything else is explicitly
  aborted (never half-served).

## Layout

```
drill/model.py        data model, config
drill/power.py        per-feed capacity + startup feasibility
drill/storage.py      crash-consistent dual-volume store (WAL + journal + cache)
drill/receipts.py     HMAC-signed receipts
drill/engine.py       server process: tick loop, power, flush, mint
drill/recovery.py     separate-process recovery + reopen
drill/credentials.py  classroom keys (kept out of the recovery import graph)
drill/scenario.py     the standard drill + shared-feed fault
drill/__main__.py     CLI (run / recover / topology)
tests/test_drill.py   end-to-end + true two-subprocess tests
examples/demo.py      human-readable drill report
```

Recovery's import graph deliberately excludes `drill.engine` and
`drill.scenario`; `tests/test_drill.py::test_separate_processes_end_to_end`
runs both processes as real subprocesses to prove it.
