# Offline archive courier

An offline pack-and-receive tool for archive transmittals. The carrier
(外包承运商) may see, per transmittal, **how many boxes**, their **sequence**,
and **each box's byte size**. It must **not** be able to tell which *option*
(public template vs. the secret one) each short page used. The recipient,
holding the local recipient key, reconstructs every page.

Everything runs locally; no network. The "sealed box" is simulated by
role-isolated local storage: the warehouse holds opaque blobs, the recipient
key is separate.

## The model

A work order is a list of pages. Each token is either

```python
("pub", slot)                    # a plain public word
("opt", slot, {version: symbol})  # word + a secret-option symbol per version
```

Words are addressed by a stable **slot** (e.g. `"longword"`), never by text.
The public mini dictionary maps each slot to a packed **code** plus a spelling.
Only the **code** travels in a page. Secret options come from a public symbol
table (`OPTIONS`); the *chosen* symbol is what must not leak.

Two legal versions share the same public template and differ only in their
option symbols.

## Two packing paths (choose one per order)

- **`layout` — reserved shapes.** Reserve one box shape per box from the
  **worst case over all legal versions**, pack the whole order into those
  shapes, and pad each box (inside the sealed plaintext) up to its reserved
  shape. Variable-width codes give common words short codes. Cost: the whole
  order ships as one window; shapes are sized for the worst option.

- **`shard` — constant-length pages.** Every page is encoded at a length that
  depends only on its **token count** (fixed 4 bytes per token: 2-byte public
  index + 2-byte secret index, `0xFFFF` = no secret). Any option yields the
  same length, so pages leave the archive independently. Cost: the usual cheap
  short codes are traded away — some common pages no longer get ordinary
  compression.

Both satisfy the headline invariant:

> The external record (box count, box sequence, per-box byte size, manifest)
> is **identical for every legal option**.

## Why the carrier can't tell the options apart

- In `layout`, box shapes come from the worst case across versions; the
  shorter option simply carries more sealed padding. Box count and byte sizes
  are version-independent.
- In `shard`, each page's length is a function of token count only.
- The sealed blob is `tag ‖ nonce ‖ ciphertext ‖ MAC`; nonces are fresh, so
  ciphertexts differ, but the plaintext never appears and lengths match.
- A version **never opens an extra box** for its own surplus. When two options
  compress to different lengths, both still receive the *same* reserved boxes.

## Files

| file | role |
|------|------|
| `archive_courier/dictionary.py` | public codebook: slots, codes (representation), spellings; option table; fingerprint (codes only) |
| `archive_courier/codec.py` | `layout` (bitmap + varints) and `shard` (fixed 4B/token) page codecs |
| `archive_courier/crypto.py` | role-isolated sealed-box store (HMAC authenticated) |
| `archive_courier/container.py` | box framing and in-box pad marker |
| `archive_courier/engine.py` | plan/reserve, transmit, manifest, receive |
| `archive_courier/experiments.py` | the two experiments + divergence demo |
| `archive_courier/cli.py` | `export` / `manifest` / `receive` |
| `demo.py` | end-to-end walkthrough |
| `tests/run_tests.py` | dependency-free test runner (39 tests) |

## Usage

```bash
# Export a carrier manifest (warehouse is carrier-safe; key stays local)
python3 -m archive_courier export --order demo --method layout \
        --out-warehouse warehouse.json --out-key key.json

# Carrier's view
python3 -m archive_courier manifest --warehouse warehouse.json

# Recipient reads the order back
python3 -m archive_courier receive --warehouse warehouse.json --key key.json
```

Run the walkthrough and tests:

```bash
python3 demo.py
python3 tests/run_tests.py
```

## The two requested experiments

**1. Change one long word's representation, recompute the commitment.**
`experiments.experiment_remap_commitment` (code `200 → 50`, crossing the
1→2-byte varint boundary) changes box shapes and therefore the packing
commitment; restoring the dictionary returns the commitment exactly. Changing
only the word's *spelling* changes nothing (text never travels). Options stay
indistinguishable before and after.

**2. Restore the dictionary, replace one big box with two small ones.**
`experiments.experiment_replace_box` turns the carrier's single box into two
smaller boxes. The same pages still ship, recovered exactly by the recipient,
and both options still produce identical external records — so a capacity
change leaks no option.

**Divergent compression.** `experiments.demonstrate_divergent_compression`
auto-finds a box capacity where naive per-version packing would need 3 boxes
for one option and 2 for the other. The reserved-shape plan gives both the
same 3 boxes; the shorter option just fills more of its box with sealed
padding. No option gets a private extra box.
