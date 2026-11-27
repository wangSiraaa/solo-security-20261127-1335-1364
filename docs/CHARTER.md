# Practice-Material Issuance Contract

_For the teaching lab's random-materials desk. This is a simulator: every
string it hands out is **practice material**, never a real key. The only
"randomness" is prefabricated bit strings, and every "device" is a synthetic
declaration._

## 0. What the bench promises, in one sentence

> For each order it hands you a practice string of the agreed length plus a
> receipt, and it promises that the string came from a pool of material that
> is independent of every other pool it has already spent from -- as long as
> the provenance declaration for that pool is **accepted** and the device is
> **not paused**. It never claims a string is a real key.

## 1. Vocabulary

| Term | Meaning here |
|---|---|
| **Pool** / source | A physical batch of prefabricated bits, identified by `(pool_id, epoch)`. This identity is the *only* thing that decides provenance. |
| **Epoch** | The declared time period. The same box in a different epoch is a different pool. |
| **Chassis / rack** | A synthetic device that *claims* which pools it can draw from. |
| **Declaration** | A device's claim: "I can draw from pool P at epoch E." A claim is not a fact. |
| **Accepted declaration** | A claim backed by something the bench can verify. Only accepted pools can back an independence promise. |
| **Synthetic declaration** | A claim with no independent witness. Kept so the bench can *refuse* it. |
| **Fresh** | Never drawn from, not paused. |

**The provenance rule.** Two orders share a source **iff** they draw from the
same `(pool_id, epoch)` -- *regardless of the literal bits and regardless of
the literal source names.* Two chassis that name the same box are one source;
two chassis that name different boxes are two sources even if the bytes happen
to be identical.

## 2. The independence rule (the part people get wrong)

> Material already spent cannot be the basis of a *new* "separately
> independent" order. Identical or different bytes prove nothing on their own.

Concretely, for **partitioned stock** (Method A, the chosen method):

* A pool of `P` live bytes can back `floor(P / L)` orders of `L` bytes.
* Each draw takes a fresh *disjoint* slice; the cut-off leftover fragment is
  **kept** in the pool and may fund a later, equally independent order -- it is
  disjoint material, not reused material.
* A shared pool is counted **once**, no matter how many chassis name it. Adding
  a second chassis that points at the same box adds **zero** capacity.
* A spent slice is never offered again as "separately independent."

Balance reported to you therefore has two numbers:

* `independent_bytes` -- live bytes over accepted, unpaused, distinct pools.
* `max_independent_orders(L)` -- `sum` over those pools of `floor(live / L)`.

## 3. Which declarations the bench accepts

The bench accepts a declaration only when its pool can be independently
vouched for. It rejects, for purposes of an independence promise:

* pools named **only** by synthetic declarations;
* pools with no live bytes;
* pools that are **paused** (see §5).

We do **not** trust a declaration just because two devices make the same
statement, and we do **not** reject one just because the literal names differ.

## 4. The two accepted methods

You may pick either. **Method A is the contract this desk is run under.**

### Method A -- partitioned stock (chosen)

Independence-guaranteed batches are split into sub-warehouses. Each order draws
from one pool and keeps the leftover fragment. One fresh source backs the
promise for that order. This is what the four-step walkthrough in §6 uses.

### Method B -- combiner (also accepted)

Per the bench combiner contract: XOR several **equal-length** paths. The output
is guaranteed only if **one path (the head) is genuinely independent-uniform of
the rest.** The head must be a fresh, accepted, unpaused pool distinct from
every other path's pool. Dependent paths (used leftovers, synthetic-only pools)
may be mixed in for volume but **never supply the guarantee themselves.** A
batch of only dependent paths produces **no** independent output.

## 5. If a device pauses

While a pool is paused:

* it contributes **nothing** to the independent balance;
* it cannot serve an order and cannot be a combiner head;
* receipts already issued from it **stay valid** (the material is already out).

**What we promise the next customer during a pause:** only verified material --
i.e. `max_independent_orders(L)` computed *with the paused pool subtracted.*
On resume (after re-verification) the paused pool's remaining bytes return to
the balance. See `bench/device.py:run_pause_scenario`.

## 6. The worked walkthrough

Fixture: three boxes (A, B, C), 64 bytes each, `L = 32`, rack-1 -> A,B and
rack-2 -> C.

1. **Two real orders.** `ORD-1` from A, `ORD-2` from B. Both get a 32-byte
   practice string and a receipt. Independent capacity now covers 4 more orders
   (A:1 fragment, B:1 fragment, C:2). A baseline re-walk of a 3-order batch
   serves all 3.
2. **Only the declarations change.** Make rack-1 and rack-2 both name box A
   (literal names kept different). Re-walking the *same* batch from the *same*
   live state now serves **1** order, not 3 -- the shared box is one source.
   This is the "same noise box" collapse.
3. **Restore sources, increase only the next length.** With sources restored,
   `ORD-3` at `2*L = 64` bytes. `independent_bytes` stays 128 (raw volume
   unchanged) but `max_independent_orders(64)` drops to 1 -- only C can cover
   64 bytes, so A and B's 32-byte fragments are too small. `ORD-3` draws 64 from
   C; afterwards no fresh source can cover 64 bytes.
4. **Lost reply after material was taken.** Re-query by order number
   (`engine.refetch("ORD-3")`): the **same** receipt and **same** material come
   back, and the remaining balance is the **true live** balance. Nothing is
   re-spent -- receipt lookup is idempotent.

## 7. Receipts and re-claims

Every order yields a receipt `RC-<orderid>-<digest>` binding the order id,
length, method, basis pools, and material. If the reply is lost after the
material was taken, supply the **order number only**; the desk returns the
identical receipt and material plus the current true balance. Re-sending the
same order number never double-spends; sending the same order number with a
**different length** is refused.

## 8. Limits of this promise (read this)

* Outputs are practice strings. **Never** use them as real keys.
* The promise is scoped to the **declared source and declared epoch**. If a
  declaration is wrong (e.g. two "different" names are actually one box), the
  promise degrades to whatever the provenance really supports -- which is why
  the bench deduplicates by pool identity, not by name or bytes.
* No cryptographic-security claim is made; the combiner is a structural
  contract, not a proof.

---

Machine-readable transcript: `python -m bench.cli --all --json`.
