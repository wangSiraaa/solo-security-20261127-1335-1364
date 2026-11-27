# Reading-Aloud Rentals — offline lending desk + two toy players

A local issue desk (the library counter) and two independent toy trusted players
(`player-A` the traveller on the train, `player-B` the waiting borrower). The
players share **no state** except what passes through the desk. Everything runs
on a simulated clock with toy HMACs — **no real keys, no real time**.

```bash
python3 -m licensing.demo      # narrated, scenarised walkthrough
python3 -m unittest discover -s tests -v
```

## The chosen policy (one of the two offered)

**Offline, chapter-limited, limited-count capability consumed on a non-rollback
player counter.** The desk pre-grants a *lease*: a chapter, a number of normal
listen credits, and a borrower scope. The player spends credits against a local
counter that only ever goes up, and settles later by presenting a **sealed,
device-signed return proof**.

This is why it works for the traveller: they pre-fetch content while connected,
finish the chapter in the tunnel offline, and on an—however brief—return the
still-unspent credits can be lent to another person the moment the desk sees a
sealed proof. (The alternative — per-segment short-lived promises — is noted at
the bottom but not implemented.)

## The consumption boundary of one listen

- A **normal** segment costs **one credit on its first open**.
- **Pause → resume** of the *same* segment on the *same* device within
  `RESUME_WINDOW = 300` sim-seconds is **free**.
- Resuming after the window, or opening any other segment, costs a **fresh
  credit**.
- A **preview-only** segment (the front taster of each chapter) costs **no**
  normal credit, is playable **only live while connected**, and is **never
  cached**.

## What a pause may continue, and what must come back before a refund

- A paused segment may be **resumed mid-line for free** only within the resume
  window and only on the same device; content is replayed from the local cache.
- A **refund / re-lend** of credits is released **only** when the desk holds a
  **sealed, device-signed proof** from **that** device. Sealing means the device
  can no longer open anything for that lease. Until then the whole balance stays
  reserved — the desk will not hand a credit to a waiting borrower while an old
  machine could still open once.
- Consumed credits are **spent forever**: *lines already heard cannot be recalled
  from the listener*. Only the **remainder** is recoverable.

So the answer to "how far can the lease be refunded?" is: **down to the credits
that are provably still unspent on a sealed device** — not a calendar date, but
a counter boundary proven by that device's sealed signature.

The capability is also **time-bounded** (`expires_at`, enforced on the player), so
after expiry the device itself refuses to open further, even with credits left.
Combined, the refundable boundary is:

    min( credits-not-yet-spent , lease-expiry )   -- but release at the desk
                                                      still needs the sealed proof.

## Per-segment provisioning and honest gaps

Provisioning (content arriving) is separate from consuming (spending a credit):

- Pre-fetch the whole chapter while connected → finish offline.
- Pre-fetch only part → on disconnect only the issued segments resume, and the
  later never-issued segments are reported as **MISSING** (honest gaps), never
  faked.

## Return receipts

| Situation                                        | Receipt            | Credits reclaimed |
|--------------------------------------------------|--------------------|-------------------|
| Offline, no data shown                           | `PENDING` (待核)   | none              |
| Offline, count shown but device **not sealed**   | `PENDING` (待核)   | none              |
| Offline, device **sealed** + count shown         | `LIMITED` (有限)   | provable remainder, reconciled later |
| Online, valid sealed proof                       | `SETTLED`          | exact remainder   |

A `LIMITED` provisional release is **reconciled**, not double-counted, when the
real signed proof arrives.

## The no-double-spend rule (the key safety property)

An active lease keeps its credits **reserved out of the pool**. New credits are
granted only up to what remains available, so:

- the desk **refuses** to issue more than the pool holds "while an outstanding
  lease may still open";
- a **receipt swap** (taking an instance that has issued, valid segments and
  retargeting only its return receipt to another chapter) is rejected on a
  **binding check** (`chapter`/`device` bound inside the signed proof), so the
  chapter is **not reclaimed early**;
- restoring the valid receipt lets the reclaim stand, but a borrower narrowed to
  **front-preview-only** can still never take the whole chapter — that is
  enforced by **scope**, not by credits.

A credit-conservation invariant (`available + outstanding + spent == total`) is
asserted across issue / consume / provisional / settle / transfer in the tests.

## Reader capability boundary

Ordinary readers only ever touch a `ReaderCapability` exposing exactly three
verbs:

- `play(lease_id, seg_id?)` — play/pause/resume; preview-only auto-routes to live
- `return_(lease_id, report_consumed?)` — seal, then settle or get pending/limited
- `transfer(lease_id, to_device_id, ...)` — the "转机" path: seal the source, then
  carry the remaining credits to another machine (consumed stays spent)

Sealing, provisioning, and proof construction are **not** exposed to readers.

## Alternative not implemented

The per-segment **short-lived promise** model (desk mints very short-lived
fragments; player caches only what it was given; desk stops future issuance on
return) was the other offered option. It gives tighter centre-side control but
makes *finishing a whole chapter in a tunnel* depend on how much was already
issued. This project implements the **offline limited-count** variant instead,
which the exercise permits ("采用其中一种").
