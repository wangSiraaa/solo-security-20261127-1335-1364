# Offline age-gate wallet — a worked design

A self-contained, dependency-free reference implementation for the exercise:
*"the ticket seller only needs to know whether the viewer cleared the age
threshold — what should the electronic credential hand over?"*

Everything runs offline, in pure standard-library Python 3.11+.

```bash
python3 -m festival.story assertion     # or: preissue
python3 -m unittest discover -s tests -v
```

## The decision: which eligibility path

The brief offers two paths and asks to **pick one**. This repo implements *both*
so they can be compared, and the story is a single function parametrised by path.

| | Path A — pre-issued tokens | Path B — local proof service |
|---|---|---|
| How the desk learns the age is enough | a **finite** grid of `(date × tier)` tokens signed in advance | a **trusted local service** answers "met tier T on day D?" for one transaction |
| Door's conclusion | "this wallet cleared tier T exactly on date D" | same, signed `result = met-on-day` |
| Unknown / newly-added date | ❌ cannot sell or admit (no token on the grid) | ✅ works — the answer is derived from the DOB live |
| If the issuer/service is away | new dates are unpromisable | a proof **already issued** for that screening still admits; brand-new dates need the service back |
| Material each side retains | signed token + desk stub; the desk keeps its token grid + a sold-stub ledger | signed assertion + desk stub; the desk keeps per-screening assertions + a sold-stub ledger |
| DOB exposure | wallet + (during issue) the desk momentarily | wallet + the local proof service only |

`festival/paths.py` encodes this table and `plan_program()` reports, per show,
whether the current path can *promise* it — which is exactly the "when the issuer
is away, which shows can we commit to?" question.

**B is the recommended choice** because it survives ad-hoc date changes (the
story's "move the late show one day earlier" and "add a brand-new date" cases)
without pre-registering a grid. Path A is kept to demonstrate its precise failure
mode.

## What the credential hands to the seller/door

Never the birth date, never a festival-wide holder number independent of the
programme, never another counter's history. Instead:

* **rotating nullifier** `H(exercise_id ‖ program_id)` — same holder ⇒ same
  handle at every desk *of one programme* (so admission records line up), but a
  different handle in every other programme (so shops can't correlate people).
* **program-scoped binding key** — stubs and eligibility are pinned to it;
  non-transferability holds without exposing any cross-programme key.
* **the answer, not the input** — a token or assertion saying "tier cleared on
  this date", plus a per-stub secret commitment the presenter must reveal.

## Threat model covered by the tests

* **Stitching two viewers** — a bundle must come from one holder handle + one
  binding key (`MixedHolderError`), so "Carol's late proof + Alice's early proof"
  cannot become one double ticket.
* **Borrowing an early-show proof** — the wallet checks the age on the exact
  date; a buyer with a birthday *between* the two shows is refused the early
  18+ show and allowed the late 12+ show.
* **Replay at the door** — a stub admits at most once and never frees its seat;
  a second scan is refused, the screening's earlier receipts stay queryable.
* **Moved screening date** — the door compares the stub's date to *its own*
  current screening; the old eligibility is refused, no seat is allocated,
  already-posted receipts remain valid.
* **Non-transferability** — a stolen stub + leaked secret fails because the
  presented holder signature doesn't match the stub's binding key.
* **Tampering** — any edited stub field breaks the desk signature.
* **Retention audit** — `festival/audit.py` searches exactly what each counter
  and door retained for the DOB, the exercise id, the other programme's data, and
  attempts a cross-shop join. All come back clean; the door holds one date only.

## Layout

```
festival/
  crypto.py      Schnorr signatures over RFC-3526 group 14; Pedersen generators
  calendar.py    deterministic test calendar + weekend programme
  credential.py  drill identity, per-programme nullifier & signing key, local mint
  preissue.py    PATH A: finite (date × tier) token grid
  assertion.py   PATH B: trusted local proof service, sign-only-the-answer
  wallet.py      DOB custody, path-specific proof acquisition, eligibility per show
  tickets.py     desk sale, single-holder bundles, stubs, reconciliation
  door.py        offline replay-safe terminal, entry/exit receipts
  paths.py       the path choice + per-show feasibility planner
  audit.py       "what did each party keep, and can it be joined?"
  story.py       the full narrative, parametrised by path
tests/
  test_festival.py   60 tests, story run under both paths + unit tests
```

## Note on the cryptography

Signatures are real asymmetric Schnorr signatures in a 2048-bit safe-prime
group, so a door can *verify* eligibility without being able to *mint* it. They
are not constant-time and are meant for an exercise, not production. The privacy
claims rest on the *structure* of what is signed (the rotating nullifier, the
programme-scoped key, the answer-only assertion), which is what the tests check;
a production system would add constant-time primitives and a vetted proof system
if it needed to hide the age itself rather than only the birth date.
