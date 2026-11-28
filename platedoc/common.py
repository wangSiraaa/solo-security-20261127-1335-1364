"""Shared constants, colour taxonomy and small utilities for PlateDoc.

The whole verification scheme is built on one idea: an edition mark is a small
square tile whose three corners carry three *distinct* colours (red / green /
blue).  The vectors green->red and blue->red, divided by (k-1) cells, form an
oriented local basis.  Because a right-angle rotation and an integer
nearest-neighbour scaling are rigid similarities, that basis stays valid after
either transform, so cell (i, j) can always be sampled in *tile coordinates*
no matter how the received crop was turned or zoomed.  Colour values are chosen
far apart and on coarse quantisation centres so that low-bit colour merging
cannot collapse them.
"""
import json
import hashlib
import zlib

# --- mark palette (frame-anchored, quantisation-safe) -----------------------
MARK_TOL = 64                      # L-infinity colour tolerance when reading
ANCHOR_TL = (224, 32, 32)          # red   -> tile corner (0, 0)
ANCHOR_TR = (32, 224, 32)          # green -> tile corner (k-1, 0)
ANCHOR_BL = (32, 32, 224)          # blue  -> tile corner (0, k-1)
ANCHOR_BR = (224, 224, 224)        # light -> tile corner (k-1, k-1)
CELL_DARK = (32, 32, 32)           # data bit 1
CELL_LIGHT = (224, 224, 224)       # data bit 0

MARK_COLOURS = {
    "R": ANCHOR_TL,
    "G": ANCHOR_TR,
    "B": ANCHOR_BL,
    "L": CELL_LIGHT,
    "D": CELL_DARK,
}

# Public geometric parameters of the mark (also written to layout_spec.json).
MARK_K = 8          # 8x8 cells
MARK_CELL = 4       # 4 px per cell at 1x  -> 32x32 px tile
BLOCK = MARK_K * MARK_CELL      # one mark fills exactly one block
CORNER_CELLS = {(0, 0), (MARK_K - 1, 0), (0, MARK_K - 1),
                (MARK_K - 1, MARK_K - 1)}

# Payload field widths (bits): comp:4 edition:6 tile:12 crc16:16 = 38 bits.
F_COMP, F_EDITION, F_TILE, F_CRC = 4, 6, 12, 16
PAYLOAD_BITS = F_COMP + F_EDITION + F_TILE + F_CRC          # 38
DATA_CELLS = MARK_K * MARK_K - len(CORNER_CELLS)             # 60
MARK_STRIDE = 3          # mark lattice period, in blocks (96 px)
BLOCK_PX = BLOCK         # 32 px: one mark fills one block
GRID_COLS, GRID_ROWS = 39, 31   # plate is 1248 x 992 px


def linf(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]), abs(a[2] - b[2]))


def min_dist_to_marks(rgb):
    return min(linf(rgb, c) for c in MARK_COLOURS.values())


def classify_mark_colour(rgb, tol=MARK_TOL):
    """Return 'R','G','B','L','D' or None for a pixel colour."""
    best, bestd = None, 10 ** 9
    for tag, c in MARK_COLOURS.items():
        d = linf(rgb, c)
        if d < bestd:
            best, bestd = tag, d
    return best if bestd <= tol else None


def crc16(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 \
                else (crc << 1) & 0xFFFF
    return crc


def canonical_bytes(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


# --- generation of well-separated "content" colours -------------------------
def _content_colour_pool(min_from_mark=152, step=16):
    """Deterministically yield RGB colours that are far from every mark colour.

    Ordered by a fixed key so results are reproducible across runs.
    """
    pool = []
    for r in range(0, 256, step):
        for g in range(0, 256, step):
            for b in range(0, 256, step):
                c = (r, g, b)
                if min_dist_to_marks(c) >= min_from_mark:
                    pool.append(c)
    pool.sort(key=lambda c: (c[0] * 7 + c[1] * 13 + c[2] * 17, c))
    return pool


_COLOUR_POOL = None


def colour_pool():
    global _COLOUR_POOL
    if _COLOUR_POOL is None:
        _COLOUR_POOL = _content_colour_pool()
    return _COLOUR_POOL


def pick_colours(n, used=None, sep=200, pool=None):
    """Pick n colours pairwise sep apart (L-inf) and away from `used`."""
    used = list(used or [])
    pool = pool or colour_pool()
    out = []
    for c in pool:
        ok = True
        for u in used + out:
            if linf(c, u) < sep:
                ok = False
                break
        if ok:
            out.append(c)
            if len(out) == n:
                return out
    raise RuntimeError("colour pool exhausted (wanted %d)" % n)
