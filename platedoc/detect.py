"""Detect and decode edition marks inside an arbitrary received piece, and
recover the affine mapping from piece pixels back to original-image pixels.

Why this is invariant to the allowed transformations
---------------------------------------------------
A mark is an 8x8 block of square cells.  Its three coloured corner anchors
(red at cell (0,0), green at cell (K-1,0), blue at cell (0,K-1)) are unique
colours.  In tile coordinates:

    green = red + (K-1) * ex        (ex = one cell step along i)
    blue  = red + (K-1) * ey        (ey = one cell step along j)

If the received piece is rotated by a multiple of 90 degrees, every centroid is
mapped by the same rigid matrix Q, so ex and ey rotate too; the *tile
coordinate basis* (ex, ey anchored at red) still indexes the very same cells.
Integer nearest-neighbour scaling multiplies both basis vectors by the same
integer factor.  Therefore we can always sample cell (i,j) at
``red + i*ex + j*ey`` in piece pixels, whatever the rotation or scale.

The same basis is exactly the linear part of the original->piece map (because a
mark cell is `cell_px` original pixels wide), so inverting it locates the whole
piece inside the original image.
"""
import math
from .common import (MARK_K, MARK_CELL, CORNER_CELLS, classify_mark_colour,
                     MARK_TOL, CELL_DARK, CELL_LIGHT)
from .mark import decode_bits

ANCHOR_TAGS = ("R", "G", "B")


def _collect_anchor_pixels(img):
    sets = {"R": set(), "G": set(), "B": set()}
    for y, row in enumerate(img.pixels):
        for x, px in enumerate(row):
            t = classify_mark_colour(px)
            if t in sets:
                sets[t].add((x, y))
    return sets


def _components(coords):
    """4-connected components of a set of (x,y) pixels."""
    remaining = set(coords)
    comps = []
    while remaining:
        seed = remaining.pop()
        stack = [seed]
        members = [seed]
        while stack:
            x, y = stack.pop()
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if (nx, ny) in remaining:
                    remaining.discard((nx, ny))
                    stack.append((nx, ny))
                    members.append((nx, ny))
        xs = [m[0] for m in members]
        ys = [m[1] for m in members]
        cx = sum(xs) / len(xs)
        cy = sum(ys) / len(ys)
        comps.append({
            "centroid": (cx, cy),
            "bbox": (min(xs), min(ys), max(xs), max(ys)),
            "area": len(members),
            "side": ((max(xs) - min(xs)) + (max(ys) - min(ys))) / 2.0 + 1.0,
            "members": members,
        })
    return comps


def _sample_cell(patch, w, h, red, ex, ey, i, j):
    cx = red[0] + i * ex[0] + j * ey[0]
    cy = red[1] + i * ex[1] + j * ey[1]
    xi = int(round(cx))
    yi = int(round(cy))
    if 0 <= xi < w and 0 <= yi < h:
        return patch[yi][xi]
    return None


def _decode_from_anchors(patch, w, h, red, green, blue):
    ex = ((green[0] - red[0]) / (MARK_K - 1),
          (green[1] - red[1]) / (MARK_K - 1))
    ey = ((blue[0] - red[0]) / (MARK_K - 1),
          (blue[1] - red[1]) / (MARK_K - 1))
    tags = {}
    for j in range(MARK_K):
        for i in range(MARK_K):
            px = _sample_cell(patch, w, h, red, ex, ey, i, j)
            tags[(i, j)] = classify_mark_colour(px) if px else None
    # corner sanity: red/green/blue/light at the four corners
    corner_ok = (tags.get((0, 0)) == "R" and tags.get((MARK_K - 1, 0)) == "G"
                 and tags.get((0, MARK_K - 1)) == "B")
    bits = []
    good = 0
    total = 0
    for j in range(MARK_K):
        for i in range(MARK_K):
            if (i, j) in CORNER_CELLS:
                continue
            total += 1
            t = tags[(i, j)]
            if t == "D":
                bits.append(1)
                good += 1
            elif t == "L":
                bits.append(0)
                good += 1
    info = decode_bits(bits)
    # perpendicularity quality
    ax, ay = ex, ey
    la = math.hypot(*ax) or 1e-9
    lb = math.hypot(*ay) or 1e-9
    sin = abs(ax[0] * ay[1] - ax[1] * ay[0]) / (la * lb)
    info.update({"corner_ok": corner_ok, "cells_good": good,
                 "cells_total": total, "orthogonality": round(sin, 3),
                 "red": red, "green": green, "blue": blue,
                 "ex": ex, "ey": ey})
    return info


def _pair_anchors(reds, greens, blues):
    """Yield (red_component, green_component, blue_component) triples whose
    geometry matches a mark tile (two equal, perpendicular arms ~7*side)."""
    pairs = []
    for r in reds:
        side = r["side"]
        if side < 1.5:
            continue
        for g in greens:
            dg = math.hypot(g["centroid"][0] - r["centroid"][0],
                            g["centroid"][1] - r["centroid"][1])
            if not (4.0 * side <= dg <= 11.0 * side):
                continue
            gax = (g["centroid"][0] - r["centroid"][0],
                   g["centroid"][1] - r["centroid"][1])
            for b in blues:
                db = math.hypot(b["centroid"][0] - r["centroid"][0],
                                b["centroid"][1] - r["centroid"][1])
                if abs(db - dg) > max(2.5, 1.2 * side):
                    continue
                bax = (b["centroid"][0] - r["centroid"][0],
                       b["centroid"][1] - r["centroid"][1])
                cosang = (gax[0] * bax[0] + gax[1] * bax[1]) / (dg * db + 1e-9)
                if abs(cosang) > 0.35:      # want near-perpendicular
                    continue
                score = abs(cosang) + abs(db - dg) / (dg + 1e-9)
                pairs.append((score, r, g, b))
    pairs.sort(key=lambda t: t[0])
    used_r = set()
    out = []
    for score, r, g, b in pairs:
        key = id(r)
        if key in used_r:
            continue
        used_r.add(key)
        out.append((r, g, b))
    return out


def detect_marks(img):
    """Find and decode every complete edition mark in `img`.

    Returns a list of dicts: comp/edition/tile/crc_ok + geometry.
    """
    sets = _collect_anchor_pixels(img)
    reds = _components(sets["R"])
    greens = _components(sets["G"])
    blues = _components(sets["B"])
    w, h = img.width, img.height
    patch = img.pixels
    findings = []
    for r, g, b in _pair_anchors(reds, greens, blues):
        info = _decode_from_anchors(patch, w, h, r["centroid"],
                                    g["centroid"], b["centroid"])
        if not info["corner_ok"]:
            continue
        info["anchor_side"] = r["side"]
        info["red_bbox"] = r["bbox"]
        info["anchor_area"] = r["area"]
        findings.append(info)
    return findings


# ---------------------------------------------------------------------------
# global piece -> original affine fit
# ---------------------------------------------------------------------------
def solve_piece_to_original(findings, layout):
    """Fit o = A*p + b mapping piece pixels to original pixels.

    The mark basis vectors ex, ey measured in the piece are the images of the
    original cell steps (MARK_CELL, 0) and (0, MARK_CELL).  Therefore, for the
    piece->original map A we need A*ex = (MARK_CELL,0) and A*ey = (0,MARK_CELL):

        A = MARK_CELL * inverse([ex | ey])

    which correctly inverts both the rotation and the integer zoom.
    """
    marks = {m["tile"]: m for m in layout["marks"]}
    cell = layout["cell"]
    corr = []
    for f in findings:
        t = f["tile"]
        if t not in marks:
            continue
        bc, br = marks[t]["block"]
        # red anchor centroid sits at the centre of the first cell
        o_red = (bc * cell + MARK_CELL * 0.5, br * cell + MARK_CELL * 0.5)
        corr.append((f["red"], o_red, f))
    if not corr:
        return None, None, {"reason": "no registered marks"}

    best = max(corr, key=lambda c: c[2]["orthogonality"])
    ex, ey = best[2]["ex"], best[2]["ey"]
    # E columns are ex, ey (per-cell steps, measured in the received piece)
    e00, e10 = ex[0], ex[1]
    e01, e11 = ey[0], ey[1]
    det = e00 * e11 - e01 * e10
    if abs(det) < 1e-9:
        return None, None, {"reason": "degenerate mark basis"}
    inv = [[e11 / det, -e01 / det], [-e10 / det, e00 / det]]
    # original cell step is MARK_CELL px, so A maps one piece cell vector to
    # MARK_CELL original px along the corresponding original axis.
    a00 = MARK_CELL * inv[0][0]
    a01 = MARK_CELL * inv[0][1]
    a10 = MARK_CELL * inv[1][0]
    a11 = MARK_CELL * inv[1][1]
    A = [[a00, a01], [a10, a11]]

    bs = []
    for p_red, o_red, f in corr:
        bx = o_red[0] - (a00 * p_red[0] + a01 * p_red[1])
        by = o_red[1] - (a10 * p_red[0] + a11 * p_red[1])
        bs.append((bx, by))
    b = (sum(t[0] for t in bs) / len(bs), sum(t[1] for t in bs) / len(bs))
    res = 0.0
    for p_red, o_red, f in corr:
        px = a00 * p_red[0] + a01 * p_red[1] + b[0]
        py = a10 * p_red[0] + a11 * p_red[1] + b[1]
        res = max(res, math.hypot(px - o_red[0], py - o_red[1]))
    scale = math.hypot(a00, a10)
    info = {"n_corr": len(corr), "scale_piece_to_orig": round(scale, 4),
            "max_residual_px": round(res, 3),
            "A": [[round(a00, 6), round(a01, 6)],
                  [round(a10, 6), round(a11, 6)]]}
    return A, b, info


def invert_map(A, b):
    det = A[0][0] * A[1][1] - A[0][1] * A[1][0]
    if abs(det) < 1e-9:
        return None
    inv = [[A[1][1] / det, -A[0][1] / det],
           [-A[1][0] / det, A[0][0] / det]]
    # o = A p + b  ->  p = inv (o - b)
    return inv


def piece_bbox_in_original(A, b, w, h):
    """Map the piece rectangle corners forward to original coordinates."""
    xs, ys = [], []
    for (px, py) in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        ox = A[0][0] * px + A[0][1] * py + b[0]
        oy = A[1][0] * px + A[1][1] * py + b[1]
        xs.append(ox)
        ys.append(oy)
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    return (int(math.floor(x0)), int(math.floor(y0)),
            int(math.ceil(x1)), int(math.ceil(y1)))


def original_to_piece(A, b, ox, oy):
    """Map an original-image point into piece coordinates (for crop checks)."""
    inv = invert_map(A, b)
    if inv is None:
        return None
    px = inv[0][0] * (ox - b[0]) + inv[0][1] * (oy - b[1])
    py = inv[1][0] * (ox - b[0]) + inv[1][1] * (oy - b[1])
    return (px, py)
