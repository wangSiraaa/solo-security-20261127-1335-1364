"""Put edition marks onto a plate and persist approved-issue artefacts.

Marks are planted only on blocks the layout registry marks permitted; the
stamper asserts no mark touches a protected rectangle.  Output is a plain
8-bit RGB PNG (IHDR/IDAT/IEND only).
"""
import os
from .pngcodec import write_png
from .common import MARK_K, MARK_CELL, BLOCK, sha256_hex
from .content import (build_plate, DEFAULT_PROTECTED_RECTS,
                      DEFAULT_PROTECTED_COLOURS)
from .layout import edition_mark_blocks
from .mark import render_tile

MARK_PX = MARK_K * MARK_CELL


def _overlaps_protected(bc, br, protected_rects):
    x0, y0 = bc * BLOCK, br * BLOCK
    for (px0, py0, px1, py1) in protected_rects:
        if not (x0 + MARK_PX <= px0 or px1 <= x0 or
                y0 + MARK_PX <= py0 or py1 <= y0):
            return True
    return False


def stamp_marks(img, comp, edition, protected_rects=None):
    rects = DEFAULT_PROTECTED_RECTS if protected_rects is None \
        else protected_rects
    blocks = edition_mark_blocks(comp, edition, rects)
    placed = []
    for tile, (bc, br) in enumerate(blocks):
        assert not _overlaps_protected(bc, br, rects), \
            "mark would touch a protected region"
        tile_px = render_tile(comp, edition, tile)
        x0, y0 = bc * BLOCK, br * BLOCK
        for j in range(MARK_PX):
            row = img.pixels[y0 + j]
            trow = tile_px[j]
            for i in range(MARK_PX):
                row[x0 + i] = trow[i]
        placed.append({"tile": tile, "block": [bc, br]})
    return placed


def render_edition(comp, edition, protected_rects=None,
                   protected_colours=None, seed=20260101):
    img, palette = build_plate(comp, protected_rects, protected_colours, seed)
    placed = stamp_marks(img, comp, edition, protected_rects)
    return img, placed, palette


def issue(output_dir, comp, edition, protected_rects=None,
          protected_colours=None, seed=20260101):
    os.makedirs(output_dir, exist_ok=True)
    img, placed, palette = render_edition(comp, edition, protected_rects,
                                          protected_colours, seed)
    png_path = os.path.join(output_dir, "plate_c%d_e%d.png" % (comp, edition))
    write_png(png_path, img)
    with open(png_path, "rb") as f:
        digest = sha256_hex(f.read())
    return {
        "comp": comp, "edition": edition,
        "png": os.path.basename(png_path),
        "size": [img.width, img.height],
        "sha256": digest,
        "palette": {k: list(v) for k, v in palette.items()},
        "mark_blocks": placed,
    }
