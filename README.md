# 匿名邮站 / Anonymous Relay

A dependency-free Node.js model of the situation described in the task: a
**trusted local post station (邮站)** that routes anonymous letters between an
**anonymous sender (来信人)** and **two independently-queryable reply rooms
(两间回函室)**, while keeping the sender's identity, mailbox address and the two
rooms mutually unlinkable.

Run:

```sh
npm test      # 18 behavioural tests
npm run demo  # narrated end-to-end walkthrough
```

No external dependencies; uses only Node's built-in `crypto` and `node:test`.

## Actors and trust model

| Actor | Knows | Must never know |
|---|---|---|
| **Citizen** (person) | own `identityKey`, per-case vouchers | — |
| **Mailbox / device** | own `ownerSecret`, recyclable `alias` | — |
| **Post station** (trusted) | citizen⇄mailbox⇄case linkage, routes, held letters | — (it is the trust anchor) |
| **Reply room A / B** | its *own* case number, its *own* pseudonym, an **opaque** return route, per-letter accounting | the sender, the address, the owner secret, the *other* room's case number, any shared marker |

The two rooms are minted **entirely independently**: each room generates its own
case number and pseudonym; the post only hands each room an unguessable opaque
return route. Nothing derivable is shared, so **querying both rooms together
cannot join them** — see `Verifier.checkRoomUnlinkability()` and
`checkRoomsHoldNoAddressSecrets()` in `src/verifier.js`.

## Aliases can be recycled safely

`openMailbox` may reuse a retired alias, but every box also gets a fresh,
never-recycled `mailboxId`. Cases bind to the **mailboxId**, never to the alias,
so an old reply is never re-pointed to a new alias holder
(`test: recycled alias does not re-route old replies to the new owner`).

## Deposit vs. pickup accounting

The clerk successfully **depositing** a letter (`ReplyRoom.deposit`) and the
owner actually **collecting** it (`ReplyRoom.pickup`) are separate registrations.
A letter sits in `delivered_unclaimed` ("已投未取") until picked up, then
`picked_up`.

## Decisions the spec leaves to the implementation

### 1. How control is handed back and what the post may retain

Two methods are implemented, both issued a signed **pickup voucher** (a bearer
capability the rooms can verify against their own pseudonym without the post
resolving any person link):

* **Voucher method** — `eraseByVoucher`
  The post hands the per-case voucher to the citizen and **immediately clears the
  case→person mapping**. It can then honestly sign a **`FULL_ERASURE`** certificate.
  Consequence: if the device **and** the voucher are both lost, there is nothing
  left to recover by (`collectRecovered` → `NOT_RECOVERABLE`), but a genuine
  voucher holder can always still collect their own reply directly at the room.

* **Escrow method** — `eraseByEscrow` (by the citizen's delegation)
  The post **drops the address now** but privately retains the case→person
  relation (under `rec.escrow`, keyed by `citizenId`) until a deadline. It signs
  **`DELIVERY_RELATION_DELETED`** immediately (the earlier, weaker receipt the
  spec asks for). Until the deadline the citizen can still **recover this case
  by identity alone** (`recoverByEscrow` / `collectRecovered`) and migrate it
  (`migrateByEscrowIdentity`). At expiry, `finalizeEscrow` signs
  **`FULL_ERASURE`**.

So: *"when can we prove the case path is forgotten?"* — the post can issue
`DELIVERY_RELATION_DELETED` as soon as the address is dropped (escrow), but can
only issue `FULL_ERASURE` once the person link is truly gone (voucher handover,
escrow expiry, or early voucher-clearing).

### 2. Reclaiming a case after a lost device

* Under **escrow** a lost device/voucher is still findable **within the window**
  by identity — this is exactly the "找回" case.
* Under the **voucher** method, losing the voucher closes recovery permanently.

### 3. Early clearing on presenting the original voucher

`requestEarlyErasure(voucher)` accelerates the escrow deadline to *now* and signs
`FULL_ERASURE`. It **never purges the true bearer's own unclaimed letter by
default** (`purge: false`); a forgery is rejected (`BAD_VOUCHER_SIG`). An erased
case whose voucher is lost can be `suspendCase`d at the post, yet a genuine
voucher holder can **still collect** at the room — "normal holder must get their
reply".

### 4. Disposition of the stop-delivery order — `stopCase`

On stop-delivery the post:
* **recalls** already-delivered-but-unclaimed replies from the room and holds them
  at the post;
* **diverts** in-transit replies to the post hold instead of depositing;
* **leaves** already-collected replies with the owner;
* tells the room to **refuse any further deposit** for that case.
Held letters remain collectable in person (`collectHeld`).

### 5. Per-case migration authorisation — `beginMigration`

The old box owner authorises migrating **only one case**, optionally toward a box
whose address is not yet known (a 3-day forwarding window). In-window replies
are held at the post until `completeForwarding` learns the new address, then they
flow to the **new** box. Other cases on the same old box are untouched.

* `revokeMigration` withdraws **only that authorisation** and forces the case to
  `held_at_post` (留邮站待领); unclaimed replies are recalled to the post.
  This is distinct from `stopCase`.
* Keeping the authorisation while collecting only **another** case leaves the
  migrated case showing **delivered_unclaimed**.

## Layout

```
src/util.js          hashing / ids / canonical JSON
src/reply_room.js    the two independently-queryable rooms
src/post.js          trusted station: routing, migration, stop, escrow, certs
src/verifier.js      unlinkability / leak auditor
src/index.js         createSystem() wiring one post + two rooms
demo/run.js          narrated walkthrough
test/system.test.js  18 behavioural tests
```

## What the verifier can show

`verifier.fullAudit()` returns three checks; `inspectCase` exposes what the post
*actually retains* per case (citizen link? address? escrow? held letters?); and
`crossCheckCase` juxtaposes the post's private view with the room's view of the
same case to confirm the room exposes no address or citizen identity.
