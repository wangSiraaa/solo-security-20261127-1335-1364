# privacydesk — reading-room, privacy-budgeted visitor statistics

A small exercise implementation of a privacy budget behind a reading-room
display board.  The perturbation in `privacydesk/core.py` is a **deliberately
toy, deterministic mechanism**.  Nothing here estimates privacy strength from
random samples, and no perturbation algorithm is invented beyond a mock; the
point of the exercise is the *accounting* and the *presentation rules*.

## The pieces

| Module | Role |
| --- | --- |
| `core.py` | Household unit, per-household `Budget`, mock `Mechanism`, `ReleaseRecord` (true total stays here, only `public_value` leaves), `Exhibit` (public-only board input). |
| `ledger.py` | Persistent spend keyed by **query/release**, not by request. Sign reserves, ack commits, a lost ack rolls back. |
| `query_desk.py` | `ask_detailed` (direct route) and `ask_table` (published-table route); `ack` / `retry`; `deliver_exhibit`. |
| `board.py` | Read-only `DisplayBoard`, `PublicationIndex` (metadata-only release→period stamp), trend statement with independence. |
| `catalog.py` | The "ordinary entrance": registers new synthetic check-in batches, which are then queried like anything else. |

## What the rules guarantee

- **Charge per affected household, once per release.** A household in two
  slots of one query is charged once for that release; two releases charge it
  twice. Caps follow the household, never the slot number.
- **Exact re-send costs nothing.** The ledger is keyed by release; publishing
  the same release again returns the recorded figure and spends nothing.
- **Never re-roll on retry.** The figure is *born once*. A lost acknowledgment
  is rolled back (zero real spend), and an honest retry with the same key
  reuses the same perturbation and figure.
- **A hot household runs out first.** A new base release touching an exhausted
  household raises `BudgetExceeded`; nothing is reserved, and already
  published releases can still be re-sent.
- **Direct route** keeps a genuine fresh release per new question (so a
  follow-up about a sub-range costs budget again). **Published-table route**
  publishes one agreed range up front; later slices are composed from stored
  cells — no extra spend — but cannot show a dimension the table never had.
- **Board shows only received exhibits**, rebuildable from public numbers.
  An exhibit whose publication belongs to another period cannot be hung as the
  current period, even if relabeled (checked via `PublicationIndex`).
- **Independence is about perturbation evidence, not overlap.** Two views of
  one publication (or any pair sharing a fingerprint/lineage) are **one**
  observation and cannot be signed as "two independent observations". Two
  genuinely separate paid releases are two observations; when both show a
  rise the statement signs as a rising trend.

## Run

```bash
python3 -m unittest discover -s tests -v   # 24 tests
python3 demo.py                            # narrative walkthrough
```

## Tests by requirement

- `tests/test_signing.py` — per-household charging, idempotent re-send,
  lost-ack refund + same-figure retry, hot-household exhaustion.
- `tests/test_board.py` — period rejection, relabel refusal, shared-evidence
  single observation, two real releases = two observations, both routes.
- `tests/test_scenarios.py` — both query orders reconcile to the same real
  spend; lost-ack retry spends once; period rollover; rising pair signed;
  shared-evidence pair refused; new batch through the ordinary entrance.
