"""Layout registry + signed edition manifest.

A *run* covers several compositions (comp 0..2) with several approved
editions each.  Within a composition the geometry and palette are IDENTICAL
across editions, so the artwork alone cannot name an edition -- only a readable
edition mark can.  Edition marks are planted on a 3-block lattice (96 px pitch)
at blocks whose full 32x32 px tile lies in a permitted (modifiable) area, i.e.
not on or beside a protected colour patch.

Each edition of a composition uses a *different* lattice phase (0,1,2), so the
mark positions differ across editions even though the artwork is the same.  The
signed manifest records, per (comp, edition), the tile map
tile_index -> block, so a verifier can (a) decode which tile an observed mark
is, and (b) locate the fragment in the original from the registry positions.

Signature: Ed25519 over the canonical JSON of the manifest (no whitespace).
"""
import json
from . import ed25519
from .common import (BLOCK, GRID_COLS, GRID_ROWS, MARK_STRIDE, MARK_CELL,
                     canonical_bytes, MARK_K)
from .content import (PLATE_W, PLATE_H, DEFAULT_PROTECTED_RECTS,
                      DEFAULT_PROTECTED_COLOURS)

MARK_PX = MARK_K * MARK_CELL
assert MARK_PX == BLOCK, "one mark must fill exactly one block"


def protected_block_set(rects, margin_blocks=1):
    blocked = set()
    for (x0, y0, x1, y1) in rects:
        ex0, ey0 = x0 - margin_blocks * BLOCK, y0 - margin_blocks * BLOCK
        ex1, ey1 = x1 + margin_blocks * BLOCK, y1 + margin_blocks * BLOCK
        bc0 = max(0, ex0 // BLOCK)
        bc1 = min(GRID_COLS - 1, (ex1 - 1) // BLOCK)
        br0 = max(0, ey0 // BLOCK)
        br1 = min(GRID_ROWS - 1, (ey1 - 1) // BLOCK)
        for br in range(br0, br1 + 1):
            for bc in range(bc0, bc1 + 1):
                blocked.add((bc, br))
    return blocked


def _clear(bc, br, blocked):
    if bc < 0 or br < 0:
        return False
    if (bc + 1) * BLOCK > PLATE_W or (br + 1) * BLOCK > PLATE_H:
        return False
    return (bc, br) not in blocked


def edition_mark_blocks(comp, edition, protected_rects=None):
    """Deterministic, ordered list of mark blocks for (comp, edition).

    Lattice points are congruent mod MARK_STRIDE in both coordinates.  Giving
    each edition a distinct (ax, ay) phase pair makes the *position sets* of
    different editions pairwise disjoint (two distinct lattice congruences
    cannot share a point).  Nine phases are available (3x3); editions cycle
    through them with a per-composition offset, so up to nine editions of a
    composition are position-separable in addition to being payload-separable.
    """
    rects = DEFAULT_PROTECTED_RECTS if protected_rects is None \
        else protected_rects
    blocked = protected_block_set(rects, margin_blocks=1)
    n_phase = MARK_STRIDE * MARK_STRIDE
    phase = (edition + comp * 2) % n_phase
    ax = phase % MARK_STRIDE
    ay = phase // MARK_STRIDE
    blocks = []
    for br in range(ay, GRID_ROWS, MARK_STRIDE):
        for bc in range(ax, GRID_COLS, MARK_STRIDE):
            if _clear(bc, br, blocked):
                blocks.append((bc, br))
    blocks.sort(key=lambda t: (t[1], t[0]))
    return blocks


def layout_spec(protected_rects=None, protected_colours=None):
    protected_rects = (DEFAULT_PROTECTED_RECTS if protected_rects is None
                       else protected_rects)
    protected_colours = (DEFAULT_PROTECTED_COLOURS if protected_colours is None
                         else protected_colours)
    return {
        "format": "platedoc-layout-v1",
        "plate_size": [PLATE_W, PLATE_H],
        "block_px": BLOCK,
        "grid": [GRID_COLS, GRID_ROWS],
        "mark": {
            "cells": MARK_K, "cell_px": MARK_CELL, "px": MARK_PX,
            "anchors": {"origin": "red(0,0)", "x_axis": "green(K-1,0)",
                        "y_axis": "blue(0,K-1)", "corner": "light(K-1,K-1)"},
            "cell_colours": {"one": "dark(32,32,32)",
                             "zero": "light(224,224,224)"},
            "payload_bits": {"comp": 4, "edition": 6, "tile": 12, "crc16": 16},
        },
        "mark_stride_blocks": MARK_STRIDE,
        "protected_rects": [list(r) for r in protected_rects],
        "protected_colours": [list(c) for c in protected_colours],
        "transform_tolerance": {
            "rotation_deg": [0, 90, 180, 270],
            "integer_scale": [1, 2, 3],
            "min_colour_bits": 2,
        },
        "sample_rule": "cut tile edges + anchor corners + an interior point;"
                       " never embed data fields in the PNG",
        "localise_rule": "registry block of the decoded tile places the"
                         " fragment in the original",
    }


def build_manifest(entries, key_seed, issued="2026-11-28",
                   protected_rects=None):
    """entries: [{'comp':c,'edition':e,'title':str}, ...].

    Returns dict with manifest, canonical body bytes, signature, pubkey.
    """
    seed, pub = ed25519.keygen(key_seed)
    comps = {}
    for e in sorted(entries, key=lambda x: (x["comp"], x["edition"])):
        comp, edition = e["comp"], e["edition"]
        blocks = edition_mark_blocks(comp, edition, protected_rects)
        tilemap = [{"tile": idx, "block": [bc, br]}
                   for idx, (bc, br) in enumerate(blocks)]
        ckey = str(comp)
        comps.setdefault(ckey, {"composition": comp, "editions": {}})
        comps[ckey]["editions"][str(edition)] = {
            "edition": edition,
            "title": e.get("title", ""),
            "mark_count": len(tilemap),
            "tilemap": tilemap,
        }
    manifest = {
        "format": "platedoc-manifest-v1",
        "issued": issued,
        "publisher": "教学纹样发行社 (fictional)",
        "public_key": pub.hex(),
        "compositions": comps,
    }
    body = canonical_bytes(manifest)
    sig = ed25519.sign(seed, pub, body)
    return {"manifest": manifest, "body": body,
            "signature_hex": sig.hex(), "public_key_hex": pub.hex()}


def verify_manifest(manifest, signature_hex):
    pub = bytes.fromhex(manifest["public_key"])
    body = canonical_bytes(manifest)
    try:
        return ed25519.verify(pub, body, bytes.fromhex(signature_hex))
    except Exception:
        return False


def manifest_tiles(manifest, comp, edition):
    e = manifest["compositions"][str(comp)]["editions"][str(edition)]
    return {m["tile"]: tuple(m["block"]) for m in e["tilemap"]}
