# sealink — 交接代理与岛站拼录台

A simulation of a shore observation station, round-trip carrier vessels, and
island merge desks, communicating **only** by exchanging small files (the
`FsWorld` layer uses local directories to model this). Exactly the scenario the
brief describes:

> 中继船提前离港，离岸观测站这一袋原始资料应让哪条船继续保管？

Two layers:

| Layer | Module | What it shows |
|-------|--------|---------------|
| Pure logic | `sealink/model.py`, `engine.py`, `guarantee.py` | bags, custody, receipts, recovery semantics |
| On-disk sim | `sealink/fsstore.py`, `verify.py` | shore / ships / islands as directories; actors go offline and reopen their directory to resume |

## The chosen strategy

The brief offers two strategies and asks to pick one:

1. a **single custody chain** — release the local copy only after a signed
   receipt comes back; give important material a **second, independent** route;
2. hand out **different coding blocks** and release batches against verifiable
   receipts, at the cost of storing and checking independent blocks.

This project implements **strategy (1), the custody-chain approach**, and
explicitly *rejects* the distributed-coding approach. Rationale: it keeps each
piece a plain, independently-verifiable slice of the observation, so any island
can open a whole observation from a small set of files and re-check the inner
digests without needing a global decoder. The trade-off — extra storage and
per-piece checking — is exactly what the coding approach would have avoided,
and the task asks us to accept one or the other.

## Key invariants (each has a test)

| Brief clause | Where | Test |
|---|---|---|
| 现场只认最终可独立打开的整条观测 | `IslandStore.can_open` / `merge` returns a `Guarantee` only if the whole window reassembles, inner digests verify, and segment order is continuous | `test_island_recognises_only_a_fully_openable_whole_observation` |
| 内容有公开及仅能由指定岛站解封的两类 | `Bag.klass` = PUBLIC / SEALED, `Bag.can_open_by` | `test_sealed_bag_only_unsealable_by_designated_island` |
| 普通承运者仅收封件 | `HandoffEngine.load` / `FsWorld.load` refuse PUBLIC for an ordinary carrier | `test_ordinary_carrier_rejects_public_bag_and_accepts_sealed` |
| 签收了一袋也不等于岛站得到了有用的新片 | `meet()` returns `receipt` and a separate `useful` / `new_pieces` signal | `test_signing_a_bag_is_not_the_same_as_a_useful_new_piece` |
| 船可承载的字节和再次会合窗口由港口控制 | `PortOrder` caps and contacts; `Voyage.cap_bytes` | `test_voyage_byte_cap_is_enforced_and_miss_is_allowed` |
| 港口随后撤去一笔约定接触 | `PortOrder.drop_contact` → `meet_skipped`, no receipt | `test_port_withdraws_a_contact_and_voyage_skips_without_receipt` |
| 允许航次临时错过 | a missed / no-usable slot returns `receipt=None`, never raises | same two tests |
| 船上只看实际交换回单并能重开目录续运 | `FsWorld.ship_manifest` / `ship_receipts` read the directory back | `test_ship_reopens_directory_to_resume` |
| 实交后带回签收再释放本地副本 | `return_receipt` is the only trigger that clears the local copy | `test_local_copy_released_only_after_returned_receipt` (+ FS variant) |
| 为一份资料指定单一保管链 | `CustodyChain(mode="single")`, one answerable carrier | `test_single_chain_cannot_hold_matter_on_two_carriers` |
| 为重要资料安排第二条独立运输路 | `CustodyChain(mode="dual")`, `independent=True` | `test_dual_route_requires_independence_and_copy_never_doubles` |
| 复印同一袋不得产生两份可承诺的恢复能力 | `Bag.fingerprint` is content-addressed (ignores `bag_id`); `duplicate_routes` not in `committable_routes()` | same test |
| 两个岛各拿自己的轨段日历和先前入库的重叠片，独立拼出 | `Calendar` (轨段), `IslandStore.held` (重叠片), `FsWorld.merge` | demo + `test_two_islands_each_settle...` |
| 收到同样字节数的两岛将有不同缺口 | coverage is positional, not a byte count | `test_same_byte_count_but_different_gaps` |
| 只变某袋与既有资料的实际重叠…缺口不同 | changing `held` changes `missing()` | `test_overlap_change_alters_the_gap` |
| 在条件充足的航次至少向两个岛各交清一笔 | sufficient voyage settles both islands | `test_two_islands_settle_on_a_sufficient_voyage` |
| 保管责任和最终点收哪份资料应跟实际装运改变 | `CustodyChain` responsibility follows actual shipment | `test_custody_and_final_settlement_follow_actual_shipment` |
| 复核内层摘要 | `verify_obsf` recomputes every inner digest + window root + continuity | `test_merged_obsf_verifies_standalone` / `test_tampered_obsf_fails_verification` |

## Design notes

**Why "receiving a bag ≠ a useful new piece."** A receipt certifies that files
physically crossed the rendezvous. Coverage is tracked *positionally*
(`(segment, time)`), so a bag full of already-held pieces is receipted but
`useful=False`. The merge desk never inflates recovery capability from a
head-count or a byte total.

**Why copies can't double the promise.** A bag's `fingerprint` is computed from
`(class, designated seal, obs id, segment root)` — not from its `bag_id`. A
photocopy or renamed duplicate has the same fingerprint, so the custody logic
records it as a `duplicate_route` and excludes it from `committable_routes()`.
A genuinely independent second route must carry a distinct split.

**Ground-truth verification.** The final artifact is a `.obsf`: a JSON header
with (id, inner digest, length) per segment followed by the raw bytes, in
window order. `verify_obsf` needs only the file — it re-hashes every segment,
recomputes the window root, and confirms the ids are continuously sequenced
for the declared window.

## Running

```bash
python3 demo.py          # logic scenario + on-disk simulation
python3 demo.py --fs     # on-disk only (shows .obsf artifacts with --keep)
python3 run_tests.py     # all tests (stdlib runner, no pytest needed)
python3 -m sealink.verify path/to/file.obsf   # verify a single merged file
```

No third-party libraries are required. Tamper-evidence uses stdlib `hmac` +
SHA-256 with per-station keys; content addressing uses SHA-256.

## Layout

```
sealink/
  model.py      segments, manifest, bags, custody, port orders
  guarantee.py  IslandStore merge desk + Guarantee (window-rooted)
  engine.py     HandoffEngine, Voyage, hash-chained keyed Ledger
  fsstore.py    FsWorld: shore/ships/islands as directories (the real sim)
  verify.py     standalone .obsf verifier (复核内层摘要)
  scenarios.py  in-memory scenario used by demo and tests
  fs_demo.py    on-disk demo
tests/
  test_sealink.py   pure-logic requirements
  test_fsstore.py   on-disk simulation requirements
run_tests.py        stdlib test runner (offline)
```

`ships/<ship>/manifest.json` is the directory a vessel reopens to resume; its
`receipts/` are the actual exchange receipts it carries back.
