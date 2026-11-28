#!/usr/bin/env python3
"""framer.py -- 装帧师裁片入口 (framer / sample-cutter entry point).

Emulates the framer who receives two approved-edition plates, cuts its OWN
chosen rectangles with an ordinary image tool, may rotate each by 90 degrees,
nearest-neighbour upscale, or merge low colour bits, strips filenames and
ancillary metadata, and puts both loose pieces into ONE swap sheet:

    python3 framer.py --issuance out/issuance --out out/swap

Deliverables (all plain lossless PNG, bare pixels):
  out/swap/piece_1.png        from one edition, a mark-rich area
  out/swap/piece_2.png        from a DIFFERENT edition, a protected colour area
  out/swap/swap_sheet.json    {"pieces": [{id, png}, ...]}  (no provenance)
  out/swap/framer_notes.txt   what was done (kept OUTSIDE the pieces)
"""
import argparse
import json
import os
import sys

from platedoc import issue
from platedoc.pngcodec import read_png, write_png
from platedoc.transforms import crop, transform_piece
from platedoc.common import BLOCK, sha256_hex
from platedoc.content import DEFAULT_PROTECTED_RECTS
from platedoc.iohelp import ensure_dir, save_json
from platedoc import layout


def _mark_blocks(issuance, comp, edition):
    m = json.load(open(os.path.join(issuance, "edition_manifest.json")))
    return layout.manifest_tiles(m, comp, edition)


def choose_mark_rich_rect(blocks, rects):
    """Framer picks a rectangle with >=2 full marks and NO protected patch."""
    w, h = 288, 256

    def prot_hit(r):
        x0, y0, x1, y1 = r
        return any(not (x1 <= px0 or px1 <= x0 or y1 <= py0 or py1 <= y0)
                   for (px0, py0, px1, py1) in rects)

    def count_marks(r):
        x0, y0, x1, y1 = r
        return sum(1 for (bc, br) in blocks.values()
                   if bc * BLOCK >= x0 and br * BLOCK >= y0 and
                   (bc + 1) * BLOCK <= x1 and (br + 1) * BLOCK <= y1)

    pts = sorted(blocks.values(),
                 key=lambda b: abs(b[0] * BLOCK - 620) + abs(b[1] * BLOCK - 500))
    best = None
    for anchor in pts:
        cx, cy = anchor[0] * BLOCK + BLOCK // 2, anchor[1] * BLOCK + BLOCK // 2
        x0 = max(0, min(1248 - w, cx - w // 2))
        y0 = max(0, min(992 - h, cy - h // 2))
        # nudge the window to avoid the protected patch, if possible
        for dx in (0, -64, 64, -128, 128):
            for dy in (0, -64, 64, -128, 128):
                xx0 = max(0, min(1248 - w, x0 + dx))
                yy0 = max(0, min(992 - h, y0 + dy))
                r = [xx0, yy0, xx0 + w, yy0 + h]
                if prot_hit(r):
                    continue
                n = count_marks(r)
                if n >= 2:
                    if best is None or n > best[0]:
                        best = (n, r)
        if best and best[0] >= 4:
            break
    if best:
        return best[1]
    # fallback: centred window around the anchor
    cx, cy = pts[0][0] * BLOCK + BLOCK // 2, pts[0][1] * BLOCK + BLOCK // 2
    x0 = max(0, cx - w // 2)
    y0 = max(0, cy - h // 2)
    return [x0, y0, x0 + w, y0 + h]


def choose_protected_rect(rects):
    x0, y0, x1, y1 = rects[0]
    # a window that actually contains the protected patch (and no permitted
    # mark, because marks are excluded from protected areas by design)
    pad = 24
    return [max(0, x0 - pad), max(0, y0 - pad), x1 + pad, y1 + pad]


def main(argv=None):
    ap = argparse.ArgumentParser(description="装帧师裁片入口")
    ap.add_argument("--issuance", default="out/issuance")
    ap.add_argument("--out", default="out/swap")
    ap.add_argument("--piece1", default="c0:0:1:2:4",
                    help="comp:edition:rot_k:factor:quant_bits for piece 1")
    ap.add_argument("--piece2", default="c0:1:2:1:8",
                    help="comp:edition:rot_k:factor:quant_bits for piece 2")
    args = ap.parse_args(argv)

    out = ensure_dir(args.out)
    notes = []

    specs = []
    for tag, s in (("1", args.piece1), ("2", args.piece2)):
        parts = s.replace("c", "").split(":")
        comp, edition, rot, fac, qb = (int(parts[0]), int(parts[1]),
                                       int(parts[2]), int(parts[3]),
                                       int(parts[4]))
        specs.append((tag, comp, edition, rot, fac, qb))

    manifest = json.load(open(os.path.join(args.issuance,
                                           "edition_manifest.json")))
    spec = json.load(open(os.path.join(args.issuance, "layout_spec.json")))
    prot_rects = [tuple(r) for r in spec.get("protected_rects",
                                             DEFAULT_PROTECTED_RECTS)]
    pieces = []
    for tag, comp, edition, rot, fac, qb in specs:
        plate_path = os.path.join(args.issuance, "plates",
                                  "plate_c%d_e%d.png" % (comp, edition))
        plate = read_png(plate_path)
        if tag == "1":
            blocks = layout.manifest_tiles(manifest, comp, edition)
            rect = choose_mark_rich_rect(blocks, prot_rects)
            kind = "mark_rich"
        else:
            rect = choose_protected_rect(prot_rects)
            kind = "protected_colour"
        piece = crop(plate, *rect)
        piece = transform_piece(piece, rotation=rot, factor=fac,
                                quant_bits=qb)
        pid = "piece_%s" % tag
        png = os.path.join(out, "%s.png" % pid)
        write_png(png, piece)          # plain PNG, no metadata
        pieces.append({"id": pid, "png": "%s.png" % pid})
        notes.append(
            "%s: edition c%d-e%d, rect=%s, rot90=%d, scale=%dx, "
            "colour_bits=%d, kind=%s, sha256=%s"
            % (pid, comp, edition, rect, rot, fac, qb, kind,
               sha256_hex(open(png, "rb").read())[:16]))

    swap_sheet = {
        "format": "platedoc-swap-sheet-v1",
        "note": "pieces are bare pixels; provenance intentionally omitted",
        "pieces": pieces,
    }
    save_json(os.path.join(out, "swap_sheet.json"), swap_sheet)
    with open(os.path.join(out, "framer_notes.txt"), "w", encoding="utf-8") as f:
        f.write("装帧师裁片记录 / FRAMER NOTES (kept outside the pieces)\n")
        f.write("=" * 64 + "\n")
        f.write("\n".join(notes) + "\n")
    print("裁片完成 / pieces cut")
    for n in notes:
        print("  " + n)
    return 0


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    raise SystemExit(main())
