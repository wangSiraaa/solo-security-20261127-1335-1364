# PlateDoc — 画册来源凭单的发行制样与证据复核

A self-contained, dependency-free toolkit for a fictional teaching-plate studio
(教学纹样发行社). It provides **two local entry points** matching the brief:

| Entry point | Role | File |
|---|---|---|
| **发行制样** (issuance / sample production) | publisher issues signed editions and prints plates | `issued.py` |
| **证据复核** (evidence review) | commissioned inspector attributes loose pieces / reprints | `verify.py` |

An additional helper, `framer.py`, plays the **装帧师** (framer) who cuts pieces
with an ordinary image tool and assembles a *swap sheet* (换页清单).

Everything is pure Python 3 standard library (a hand-written PNG codec and a
hand-written Ed25519). No numpy/Pillow/OpenCV needed. Outputs are plain
8-bit RGB PNGs (chunks `IHDR/IDAT/IEND` only) that open in ImageMagick, GIMP,
libpng, browsers, etc.

## Quick start

```bash
# 1) 发行制样: issue a run (signed manifest + plate PNGs)
python3 issued.py --out out/issuance --editions 0,1,2,3,4,5

# 2) 装帧师: cut two pieces from two different editions into one swap sheet
python3 framer.py --issuance out/issuance --out out/swap

# 3) 证据复核: attribute / localise / ask-for-another-sample, reprint
python3 verify.py --issuance out/issuance \
                  --sheet out/swap/swap_sheet.json \
                  --out out/review --reprint

# full end-to-end demo (issuance → framer → review → adversarial → reissue)
python3 run_scenario.py

# self-checking test suite
python3 test_platedoc.py
```

## What the publisher publishes

```
out/issuance/
  layout_spec.json         public geometry (no secrets)
  edition_manifest.json    per-edition tile maps (mark positions)
  edition_manifest.sig     Ed25519 signature over canonical manifest JSON
  publisher_pubkey.hex     publisher public key
  plates/plate_c{c}_e{e}.png   approved-edition plates (plain RGB PNG)
  plates/plates.sha256     hashes of approved plates
  SAMPLE_INSTRUCTIONS.txt  where an inspector may cut (public text, NOT in PNG)
  ISSUE_REPORT.json        summary + checks
```

A verifier needs only **(a) the public layout spec** and **(b) the signed
edition manifest** — exactly the brief's "公开版式说明与发行方签过的版次清单".

## Design

### Edition marks that survive rotation, scaling and colour merging

Each edition plants small square marks. A mark is an **8×8 grid of 4-px cells**
= one 32-px block, with three distinct corner **anchors**:

```
red(0,0)  ....  green(7,0)
   |   data cells  |
blue(0,7)  ....  light(7,7)
```

* The three anchor colours are far apart (L∞ ≥ 128), so **low-bit colour
  merging down to 2 bits/channel** cannot collapse them (1 bit is below the
  information floor and is documented as out of tolerance).
* The red→green and red→blue vectors, divided by 7 cells, form an **oriented
  cell basis**. A right-angle rotation rotates both vectors by the same rigid
  matrix; an integer nearest-neighbour zoom scales both by the same integer.
  So sampling cell *(i,j)* at `red + i·ex + j·ey` in *piece* pixels recovers
  the payload **whatever the rotation or integer scale** — no need to know the
  transform beforehand.
* Payload: `comp(4) | edition(6) | tile_index(12) | crc16(16)` (38 bits in 60
  data cells). The CRC-16 rejects damaged/merged reads, so the verifier never
  reports a corrupted mark as valid.

### Repeated patterns across editions

A *composition* (comp 0/1/2) has **fixed geometry and palette**; every edition
of that composition is **pixel-identical except for its marks**. This is the
"同一构图在各版反复出现 / 重复纹样" case: *the artwork cannot name an edition —
only a readable mark can.* Edition mark positions use a 3×3 lattice-phase
scheme, so editions of one composition have **pairwise-disjoint mark blocks**,
which lets a protected/ambiguous piece point at exactly which block to cut next.

### Protected colour-discrimination regions ("不能标的位置")

Some rectangles are fixed colour-teaching patches (subtle adjacent pairs). Their
values are **supplied by the publication spec** (`spec_default.json`) and must
never change within a run. Marks are **never placed on or next to** them, and
the protected colours are kept far (> tolerance) from every mark colour so a
protected pixel cannot be misread as an anchor or data cell.

### Localisation

From the decoded marks' block registry positions, an affine map
`original = A·piece + b` is fitted (A inverts both the rotation and the integer
zoom). It locates the whole piece's rectangle in the original plate to
**pixel accuracy** (verified 0-px error across all transforms in the tests).
For a protected piece (no marks allowed) the composition is fixed from the
palette and the approximate protected rectangle is reported from the public
geometry.

### Honest conclusions

The verifier's verdicts are:

* `attributed` — exactly one signed edition is proved by CRC-valid marks; it
  reports the edition and the original range, and (with `--reprint`) re-prints
  the clear illustration **from that edition's original plate** (pixel-exact).
* `undecidable` — no readable mark; it **does not** pick "the most similar"
  edition. It reports the composition (if fixable), an approximate range (for
  protected pieces), and **which block(s) to cut** to resolve the edition.
* `ambiguous_multiple_editions` — marks from two editions in one piece; request
  a clean sample.

## Reissue (只改保护范围)

Change **only** the protected scope/values in a spec and re-issue. The layout
and mark scheme are unchanged; the new print holds only the new fixed values
(with zero old-colour residue). See `spec_reissue.json` and
`run_scenario.py` steps 6–7.

```bash
python3 issued.py --out out/reissue --spec spec_reissue.json --editions 0,1,2,3
```

## Framing assumptions / limits

* Transform tolerance: rotations {0,90,180,270}°, integer zoom {1×,2×,3×},
  colour merge to ≥ 2 bits/channel. Non-integer scaling, arbitrary angles or
  lossy formats are **out of scope** (documented, not silently assumed).
* A piece must show at least one *complete* mark (all three anchor corners) to
  be attributable. A rectangle that only clips half a mark is correctly treated
  as insufficient, not guessed.
* The demo plate is 1248×992 px; editions are generated deterministically from a
  fixed publisher seed, so re-runs are reproducible.

## Files

```
platedoc/
  pngcodec.py    dependency-free PNG read/write (all colour types, Adam7)
  ed25519.py     pure-Python Ed25519 (RFC 8032), self-tested
  common.py      mark palette, CRC-16, colour pool
  mark.py        38-bit payload codec + tile rendering
  detect.py      anchor detection, rotation/scale-invariant sampling, affine
  content.py     plate compositions + protected colour patches
  layout.py      mark lattice registry + signed manifest
  issue.py       stamp marks, persist approved plates
  verifylib.py   analysis engine (attribution / localisation / anti-guess)
  transforms.py  crop / 90° rotate / nearest-neighbour scale / bit merge
  iohelp.py      JSON/dir helpers + protected-colour guard
issued.py        ← 发行制样 entry point
verify.py        ← 证据复核 entry point
framer.py          装帧师 helper (cuts pieces + swap sheet)
run_scenario.py    full demonstrator
test_platedoc.py   test suite (24 checks)
spec_default.json / spec_reissue.json   publication specs
```
