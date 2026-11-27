# dntool.py — anonymized route-change exchange

A local tool for the machine room ("机房") to hand a contractor an **editable
anonymous design**, receive an edit back, and **preload / land** the change on
the real network — without ever exposing raw addresses, private node names, or
out-of-domain detail to the contractor.

## The model (small width)

Two synthetic partitions **A** and **B** re-use the *same* short block
`10.0.0.0/28` (16 addresses). Each partition is its own forwarding table, so
the same address routes differently in A and B.

- **Trusted scope**: `10.0.0.0/29` inside partition A (`.0`–`.7`).
- **Hidden from the contractor**: partition B, A's untrusted half
  (`10.0.0.8/29`, also the expiring return/transit range), private transit
  nodes, and everything outside the scope.

The contractor gets:
- fictitious prefixes under `198.18.0.0/29` that **preserve containment**
  (`/30`→`/30`, `/32`→`/32`), so a "more-specific" edit stays more-specific;
- opaque interface tokens `1/2/3` labelled only by behaviour (`direct/L1/L2`);
- no `nbr` names (only `nbr=0` = direct is allowed).

Two equivalent editing surfaces, both landing on the same real edit:
1. **Fake-prefix view** — edit rows in `package.anon`.
2. **Anon-group view** — `route --group <name> --to <L1|L2|direct>`.

## Trust boundary / anti-leak

`package.anon` is the only contractor artifact. `init` builds it solely from
in-scope A routes, mirrors each prefix, and assigns tokens; it emits **no**
real address, private node name, or B/untrusted/out-of-scope detail.
`check <file>` re-scans any file and fails (`exit 2`) if it finds `10.0.0.`,
`coreA`, or other originals. `package.manifest.json` is an **internal**
manifest — it says a hidden range *exists* (counts/labels) but never which real
prefix it is on.

## What "landing the change" guarantees

`apply` translates the design back, then **atomically**:
1. rejects any row outside the fake scope / non-landing destination token;
2. requires the design to still **cover the whole trusted scope**;
3. verifies partition **B is byte-identical** and **no out-of-scope A address**
   changes LPM (`change leaks outside the trusted scope` → reject);
4. only then commits, bumping the epoch.

Any failure rejects the **whole** change — nothing is half-applied
(`epoch` unchanged). Results are appended to `review.jsonl` keyed by change id;
`review --change <id> --trials` replays the per-flow trial for that change.

## Ambiguity (why scoping matters)

`10.0.0.0/30` exists in **both** A and B. Merged across partitions the short
address has two candidates → an edit is ambiguous. Because an apply is *fixed to
partition A*, the same edit is expressible **unambiguously** (A changes, B stays
on its original line). `dntool.py` reports the cross-partition candidates so
this is visible rather than silent.

## Commands

```
init                       seed real world + write package.anon
package [--out F]          regenerate the anonymous design
check F                    leak-scan a file (exit 2 on leak)
groups [--anon]            behavioural groups
trial                      exhaustive probe grid (all 16 addrs × A,B)
divert --segment P --to L  fake-prefix edit: move one more-specific segment
route --group G --to L     anon-group edit: set a group's outcome
apply [--design F]         land a hand-edited package.anon
round                      advance a round; expiring return routes lose an object
restore                    restore the original routing
reprioritize --prefix P --ifc I --pri N   edit only a priority relation
refresh-return [--dry-run] how much of a returning range survives local corrections
alias --backup|--remove I [--prefix P]|--restore   back up / withdraw one landing
unhide --add P             reveal a soft-denied/out-of-domain range
review [--change ID] [--trials]            replay a recorded change
walkthrough                end-to-end demo (writes all artifacts)
```

## The walkthrough covers each requirement

- **A normal reroute** (STEP 3): divert only `198.18.0.0/30`→L2; the
  same-numbered sibling `198.18.0.4/30` and **partition B stay on the original
  line**.
- **Contractor sees only structure** (STEP 0–2): package + manifest + leak check.
- **Unambiguous expression** (STEP 4): the cross-partition candidate report.
- **Reject whole, no partial** (STEP 5): an out-of-scope row rejects the change
  with epoch unchanged.
- **Priority-only edit orphans a prior return** (STEP 6): only `pri` changes;
  the return packet's applicable object is superseded.
- **Return re-refresh under a local correction** (STEP 7): `refresh-return`
  reports `6/8 addresses = 75%` usable, `2` shadowed.
- **Restore + withdraw one backup** (STEP 8): restore original, withdraw exactly
  `10.0.0.6/32` on ifc 20, re-trial → only `.6` moves (to `.4/30` ifc 10).
- **Reviewable per-change trials** (STEP 9): `review.jsonl`.

## Tests

```
python3 test_dntool.py      # 22 tests, no pytest required
```
