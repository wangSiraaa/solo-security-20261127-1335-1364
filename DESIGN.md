# Design: a shareable input method with revocable personal learning

## The question

A shared tablet learns "after this word, that word follows" from sentences
several family members contribute.  Someone may have taught the same word pair
as someone else.  When a member leaves:

- the tablet must still be able to complete **new sentence starts** from what
  the remaining members (and public base) taught;
- **nothing** about the departed member's private contribution may leak through
  suggestion order, multi-step association, or a cache that survives a reboot;
- words that **other people also taught** must still surface.

The correctness yardstick is exact: after removal, the tablet must behave like
one that **never contained that member's teaching**, with all other input the
same.

## The two candidate architectures, and the choice

The brief offers two shapes:

1. **Per-owner libraries, combined only while present.**  Each member's
   sentences live in their own library; the tablet predicts from the union of
   libraries belonging to members currently "present".
2. **One merged model on the device, plus exact compensating receipts**, used to
   cancel a member's contribution when they leave.

**This implementation uses architecture 2.**  Reasons:

- Architecture 1 loses the member's contribution the moment their library is
  offline.  The brief explicitly notes this ("the former loses that part of its
  learning while the personal library is offline") and it conflicts with the
  requirement that the tablet "actually complete new sentence starts" from
  members who are not physically present.  On a shared home tablet, members are
  usually absent at prediction time.
- Architecture 1 also has to hold a token for every present member at prediction
  time.  After a member walks away with their token and library, their
  contribution cannot even be *identified* to remove it, let alone removed
  exactly.
- Architecture 2 can revoke **without the member's token** (the device holds
  the compensating receipt), which matches "家人带走自己的词库后" — the family
  member has already taken their library.  The tablet still knows exactly what
  to subtract.

The cost of architecture 2 is that the device must "leave enough precise
material to cancel" while "not letting ordinary users look up who taught what."
Both are handled by sealing (below).

## How the merged model is kept exact

The model is a set of counts:

- `model[prev][next]` — how many times `next` followed `prev`;
- the same table's `prev = ""` row counts **sentence-start** first words;
- the public base corpus contributes its own counts, kept separately so it is
  never removed by a member's exit.

Teaching a sentence applies `+1` (per distinct sentence, per member) to each of
its `(prev, next)` pairs.  A member's **compensating receipt** is the exact list
of their distinct sentences.  Revoking a member applies `-1` for each of those
sentences — the exact inverse of what they added.

Therefore, immediately after revocation:

```
model_after = model_base + Σ_{remaining members} transitions
```

which is *byte-for-byte* the model a device would hold had that member never
taught anything.  The randomised test `test_random_corpora_match_never_had_baseline`
asserts the two models' internal tables are equal for many random splits, and
that every suggestion, count vector, multi-step chain, and vocabulary matches.
This is stronger than comparing suggestion order alone: it is model equality.

Because counts are the only shared state, "same word taught by two people" is
just `count = 2`; removing one person leaves `count = 1`, so the word still
appears.  Reteaching one member's sentence to another word removes their old
transition and adds the new one; if no other member taught the old pair, it
disappears.

## Why no leak through order, multi-step, or cache

- **Suggestion order** is `(count desc, word asc)` over owner-free counts.
  Ties are broken by the word's *content*, never by member id, insertion order,
  or recency.  Tests assert that the same multiset of taught sentences yields
  identical ordering regardless of authorship or teach order, across a restart.
- **Multi-step association** reuses the same owner-free table, so it cannot
  reach a withdrawn transition either.
- **Cache.**  No decrypted sentence and no per-owner text is ever written to
  disk.  The only persisted model is the sealed count table; a reboot reloads
  exactly the post-revocation counts.  Tests confirm a withdrawn sentence
  appears nowhere in the on-disk state after a fresh load.

## Keeping the receipts private from ordinary users

The count table and every member's compensating receipt are **sealed** with the
device master key before being written.  The state file therefore contains:

- one-way token hashes and pseudonymous ids (`u` + hash prefix) — no names;
- the sealed count table;
- sealed per-member receipts;
- revocation epoch numbers (needed for replay protection), which reveal only
  that *some* pseudonymous token left, not what they taught.

A normal user of the tablet never sees plaintext sentences or authorship; the
sentence list behind a receipt is only reachable with the device key.  For a
stronger posture, keep the master key **outside** the state file
(`--key-file`); then even someone who copies the JSON cannot open the receipts.

Sealing is encrypt-then-MAC built from HMAC-SHA256 (the platform has no
third-party crypto library): a counter-mode HMAC keystream plus an HMAC tag over
length-prefixed AAD.  Records bind member, epoch and op id into the AAD, so
tampering or cross-context reuse fails authentication.  Payloads are padded to a
fixed bucket so ciphertext length does not reveal sentence length.  (This is a
sound stdlib construction; in a deployment with a vetted library, swap in
AES-256-GCM or XChaCha20-Poly1305 behind the same `seal`/`unseal` interface.)

## Repeated exit, re-learning, and replay

Each member has an **epoch**.  `exit` removes the live contribution and records
`revoked[token_hash].last_epoch = epoch`.  Consequences:

- **Repeated exit** is idempotent (`already-revoked`).
- **Re-learning after exit** re-joins at `epoch + 1`; the old receipt is gone,
  and the new contribution is independent, so previously withdrawn words do not
  return.
- **Stale backup replay** is refused: a bundle carries the epoch it was taken
  at, and restore rejects any bundle whose epoch is `<= last_epoch`.  A member
  cannot resurrect withdrawn material by replaying an old backup.
- **Backups** are sealed under the *member's own token*, so they are portable
  but unreadable without it.

## Revocation without the token

`revoke(member_id)` performs the same exact subtraction as `exit`, using the
device-sealed receipt.  This is the path for "the family member already took
their library and is gone."  Authorising who may call it is an application
policy decision; the cryptographic subtraction does not need the member.

## Local disabled words

The requirement "restore learning material; change only the current user's
disabled words; the suggestion should move to the next available candidate" is
implemented as a **post-filter**: `suggest` and `chain` drop disabled words
after ranking.  Disabling changes neither the count table nor the model — tests
assert the counts are identical before and after disabling, while the top
candidate changes.  Disabled lists are per current user and never persisted into
the model.

## What is and is not proven

Proven by tests: exact model equality vs. the never-had baseline (randomised),
shared-word survival, other-members' retention across multiple exits, reteach
disappearance, order independence, reboot non-resurrection, idempotent/repeat
exit, replay refusal, per-user local filtering, and sealing/tamper detection.

Not claimed: sealing cannot hide *counts* from someone who can read the sealed
model with the device key, and it cannot hide *that* a pseudonymous member
existed before revocation.  Hiding aggregate statistics from a file-level
attacker would require holding the counts encrypted at rest and only ever
querying through a keyed process — a deployment hardening step, not a change to
the revocation logic.
