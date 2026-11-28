# hintdesk — candidate-honest hint issuance

An offline hint station for a puzzle exhibition. A reader may take **two pages
per ticket**; each page must be *useful* (rule out at least one answer that was
still plausible) and the desk must never hand over the answer (at least **two**
distinct answers must remain). Two routes serve the same rules, a public receipt
carries a reader between stations, and an independent verifier replays the
receipt under each hypothesised truth.

```
hintdesk/
  content.py   immutable library: fields, alias groups, statements, pages, questions
  engine.py    THE public rules — one deterministic delivery map for every route
  receipts.py  public, tamper-evident transcript carried between stations
  booklet.py   route A: precompiled branches for an outside hint-giver (no question bank)
  desk.py      route B: inner office, live, honours only material it can redeem
  station.py   serves a reader; opens/continues receipts; keeps a hand-over ledger
  verify.py    independent replay under every hypothesis; cross-path agreement
  curator.py   intake (can a library back two useful pickups?) + round runner
  cli.py       admit / menu / round / verify
examples/      lantern.json, chain5.json, final_asym.json, twoways.json
tests/         51 tests (content, engine, routes, verifier, acceptance)
```

Run the tests (no third-party deps; Python 3.10+):

```
python3 -m unittest discover -s tests -p 'test_*.py' -t tests
```

Try the demo library:

```
python3 -m hintdesk.cli admit  examples/lantern.json
python3 -m hintdesk.cli round  examples/lantern.json --route booklet
python3 -m hintdesk.cli verify examples/lantern.json --choices "现场可能是谁？,灯在哪间屋亮过？"
```

## The rules, in one place

`Engine.delivery_map` partitions the still-consistent worlds by what a question
hands each of them, in one pass:

1. group worlds by the page their **primary** response draws;
2. hand a group its primary page only if it is **safe** (≥ 2 distinct answers
   survive) and, for a pickup, **useful** (strictly fewer distinct answers than
   before);
3. groups that cannot take their primary page are offered the question's
   truthful **substitute** pages, judged by the same rule → status `answered`;
4. whatever is left is `withdrawn`, with a reviewable reason (`over_reveal`,
   `useless`, or `no_material` when the page's material is gone).

A page whose *material* is withheld is never drawn. Final pages need only be
safe. Because page **content** is hashed (not page ids, not verbatim wording),
two different questions that put the same pages into a reader's hands produce
the *same receipt* and therefore replay to the same survivors.

## Candidate accounting — the honest alphabet

A candidate is a **distinct answer** (谜底). Only aliases (同义写法) collapse — they
denote one answer. Different answers are never pooled: if the menu, the drawable
pages, or how the desk refuses can tell them apart, they stay separate. So
"merging two aliases" genuinely removes an answer (see `chain5`), and any claim
of usefulness is measured against this count, not a label.

## Two routes

* **Route A — booklet.** `Booklet.compile(lib)` walks every reachable public
  state and memoises the exact step each truth receives. Shipped to an outside
  hint-giver who knows this round's truth but has *no question bank*: it can only
  replay compiled branches and raises `CannotFulfil(reason=...)` otherwise. It
  holds no engine or library at read time.
* **Route B — inner office.** No precompilation. Each request is resolved live
  from the receipt and the material it can still redeem. Material can be
  withheld; the ledger is part of the public round config, so replays match.

Both produce byte-identical receipts, so a ticket continues across stations
(route A at station 1, route B at station 2, or vice-versa) from the receipt
alone.

## Verification

`Verifier.replay(receipt)` holds no truth. It keeps only the worlds that
reproduce the **entire** record (wording, offered set, delivered content + text,
status, reason), and reports the surviving distinct answers. `replay_many`
groups receipts by `record_signature` (what the reader can tell apart,
independent of which question wording produced it) and asserts same-record
receipts share survivors — the "different paths, same record" case.

## Acceptance coverage

* two effective pickups on **both** routes (`test_acceptance`);
* an unservable round yields a reviewable reason (`too_few_answers`,
  `no_two_useful_pickups`, `menu_exhausted`, …);
* merging two aliases breaks the two-useful promise while the unmerged library
  admits it (`chain5`);
* the same final page is usable on one ticket's history but over-reveals on
  another, and is re-issued as a substitute (`final_asym`);
* two different questions form the same reader record (`twoways`);
* both stations can retrieve the pages they actually handed over (station
  ledger).
