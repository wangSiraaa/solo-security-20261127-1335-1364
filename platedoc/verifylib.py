"""Analysis engine used by the verifier.

Given a received piece (already stripped to bare pixels), decide:
  * which edition it can be attributed to, from readable marks,
  * where it sits in that edition's original plate,
  * or that it is undecidable and another sample is needed.

Everything here relies only on the public layout spec + the publisher-signed
manifest, exactly what a commissioned inspector is allowed to hold.
"""
import math
from . import detect, layout
from .common import BLOCK, MARK_K, MARK_CELL
from .content import DEFAULT_PROTECTED_COLOURS, DEFAULT_PROTECTED_RECTS

MARK_PX = MARK_K * MARK_CELL


SUPPORTED_ROT = [0, 90, 180, 270]
SUPPORTED_SCALE = [1, 2, 3]


def _tile_to_block(manifest, comp, edition):
    return layout.manifest_tiles(manifest, comp, edition)


def _region_for_block(block):
    bc, br = block
    return [bc * BLOCK, br * BLOCK, (bc + 1) * BLOCK, (br + 1) * BLOCK]


def analyse_piece(img, manifest, spec, piece_id=None, report=None):
    """Return an analysis dict for one received piece."""
    report = report or {}
    findings = detect.detect_marks(img)
    # keep only CRC-valid marks
    valid = [f for f in findings if f["crc_ok"] and f["corner_ok"]]
    crc_bad = [f for f in findings if not f["crc_ok"]]

    out = {
        "piece_id": piece_id,
        "size": [img.width, img.height],
        "marks_detected": len(findings),
        "marks_valid": len(valid),
        "crc_failures": len(crc_bad),
        "attribution": [],
        "localisation": None,
        "protected_overlap": False,
        "distinct_content_evidence": 0,
        "verdict": None,
        "needs_another_sample": False,
        "notes": [],
    }

    # which (comp, edition) pairs explain the decoded marks?
    pairs = {}
    for f in valid:
        key = (f["comp"], f["edition"])
        pairs.setdefault(key, []).append(f)

    # verify each candidate pair against the signed manifest registry
    confirmed = []
    for (comp, edition), fs in pairs.items():
        try:
            tiles = _tile_to_block(manifest, comp, edition)
            verified = [f for f in fs if f["tile"] in tiles]
        except KeyError:
            out["notes"].append(
                "marks claim comp=%d edition=%d but no such signed edition"
                % (comp, edition))
            continue
        if not verified:
            continue
        # localise piece in that edition's original via affine fit
        lay = {"cell": BLOCK,
               "marks": [{"tile": t, "block": b} for t, b in tiles.items()]}
        A, b, info = detect.solve_piece_to_original(verified, lay)
        loc = None
        if A is not None:
            bbox = detect.piece_bbox_in_original(A, b, img.width, img.height)
            loc = {"bbox_original": bbox, "affine": info,
                   "n_marks_used": info["n_corr"]}
        confirmed.append({
            "comp": comp, "edition": edition,
            "n_marks": len(verified),
            "tiles": sorted(f["tile"] for f in verified),
            "localisation": loc,
        })

    out["attribution"] = confirmed

    # ---- distinct content evidence (to defeat "most similar" guessing) ----
    # Count marks whose tile index would belong to *different* editions at the
    # same block -> proves the fragment's mark layout is edition-specific.
    if confirmed:
        conf = confirmed[0]
        comp = conf["comp"]
        other_editions = [e for e in _editions_of(manifest, comp)
                          if e != conf["edition"]]
        # a decoded mark at tile t maps to block B; that block belongs to the
        # current edition only.  If the block also appears in another edition's
        # registry (it shouldn't), it's not discriminating.
        cur_blocks = set(_tile_to_block(manifest, comp, conf["edition"]).values())
        discriminated = 0
        for f in pairs[(comp, conf["edition"])]:
            if f["tile"] not in _tile_to_block(manifest, comp,
                                               conf["edition"]):
                continue
            blk = _tile_to_block(manifest, comp, conf["edition"])[f["tile"]]
            if any(blk in set(_tile_to_block(manifest, comp, e).values())
                   for e in other_editions):
                continue
            discriminated += 1
        out["distinct_content_evidence"] = discriminated

    # ---- protection / colour-discrimination check ----
    out["protected_overlap"] = _overlaps_protected(img, spec)

    # ---- decision ----
    if len(confirmed) == 1:
        conf = confirmed[0]
        out["verdict"] = "attributed"
        out["localisation"] = conf["localisation"]
        if conf["localisation"]:
            bbox = conf["localisation"]["bbox_original"]
            out["original_range"] = bbox
            tiles = _tile_to_block(manifest, conf["comp"], conf["edition"])
            out["mark_regions"] = [
                {"tile": t, "block": list(tiles[t]),
                 "region": _region_for_block(tiles[t])}
                for t in conf["tiles"] if t in tiles]
        if out["distinct_content_evidence"] == 0:
            out["notes"].append(
                "edition named from mark payload; visible content may recur"
                " identically in other editions")
    elif len(confirmed) > 1:
        # marks from more than one edition: ambiguous / two editions mixed
        out["verdict"] = "ambiguous_multiple_editions"
        out["needs_another_sample"] = True
        out["notes"].append("marks from more than one edition in one piece")
    else:
        out["verdict"] = "undecidable"
        out["needs_another_sample"] = True
        # Try to at least fix the COMPOSITION from palette, and (for a
        # protected piece) give an approximate location, plus name specific
        # blocks whose mark would resolve which edition it is.
        comp_guess, comp_score, comp_margin = fingerprint_composition(img)
        if comp_guess is not None and comp_margin >= 0.08:
            out["composition_guess"] = comp_guess
            out["composition_confidence"] = round(comp_score, 3)
            eds = _editions_of(manifest, comp_guess)
            near = None
            prot_loc = localise_protected(img, spec) if out["protected_overlap"] \
                else None
            if prot_loc:
                out["approximate_range"] = prot_loc["original_range"]
                out["location_confidence"] = prot_loc["confidence"]
                pr = prot_loc["original_range"]
                near = ((pr[0] + pr[2]) // 2, (pr[1] + pr[3]) // 2)
            out["discriminating_blocks"] = discriminating_blocks(
                manifest, comp_guess, eds, near=near)
            out["notes"].append(
                "composition fixed from palette as comp %d; edition still"
                " undetermined" % comp_guess)
            out["notes"].append(
                "cut any one listed discriminating block to tell editions"
                " %s apart" % ", ".join(str(e) for e in eds))
        if out["marks_valid"] == 0 and out["crc_failures"] > 0:
            out["notes"].append("marks present but CRC failed (damaged/merged)")
        elif out["protected_overlap"]:
            out["notes"].append(
                "visible area is a protected colour region: no edition mark"
                " allowed here; request another sample from a permitted area")
        else:
            out["notes"].append(
                "no readable edition mark in this piece; request another"
                " sample (do not name the most similar edition)")

    # anti-guess guard: never name an edition when evidence is insufficient
    if out["verdict"] != "attributed":
        out["attribution"] = []
    return out


def _editions_of(manifest, comp):
    return [int(e) for e in manifest["compositions"][str(comp)]["editions"]]


def fingerprint_composition(img, tol=10):
    """Guess which composition a piece belongs to, from its palette.

    Compositions use fixed, mutually distant palettes, so counting sampled
    pixels near each composition's six role colours usually identifies the
    composition even when the piece carries no readable mark.  Returns
    (comp, score, margin) or (None, 0, 0) if inconclusive.
    """
    from .content import comp_palette
    scores = {}
    step_x = max(1, img.width // 90)
    step_y = max(1, img.height // 90)
    samples = []
    for y in range(0, img.height, step_y):
        for x in range(0, img.width, step_x):
            samples.append(img.pixels[y][x])
    n = max(1, len(samples))
    for comp in (0, 1, 2):
        pal = [tuple(v) for v in comp_palette(comp).values()]
        hits = 0
        for px in samples:
            for c in pal:
                if (abs(px[0] - c[0]) <= tol and abs(px[1] - c[1]) <= tol
                        and abs(px[2] - c[2]) <= tol):
                    hits += 1
                    break
        scores[comp] = hits / n
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    best, s1 = ranked[0]
    s2 = ranked[1][1]
    if s1 < 0.15:
        return None, s1, s1 - s2
    return best, s1, s1 - s2


def discriminating_blocks(manifest, comp, candidate_editions, near=None,
                          limit_per_edition=2):
    """For each candidate edition, name registry blocks whose mark is specific
    to that edition (positions are disjoint across editions) -> cutting one
    resolves the ambiguity.  `near` = (x,y) original point to prefer nearby.
    """
    out = []
    maps = {e: layout.manifest_tiles(manifest, comp, e)
            for e in candidate_editions}
    for e in candidate_editions:
        blocks = list(maps[e].values())
        if near is not None:
            blocks.sort(key=lambda b: (b[0] * BLOCK - near[0]) ** 2 +
                        (b[1] * BLOCK - near[1]) ** 2)
        picks = []
        for (bc, br) in blocks:
            # specific to e if no other candidate edition marks this block
            if any((bc, br) in maps[o].values() for o in candidate_editions
                   if o != e):
                continue
            picks.append({"tile": _tile_for_block(maps[e], (bc, br)),
                          "block": [bc, br],
                          "region": _region_for_block((bc, br))})
            if len(picks) >= limit_per_edition:
                break
        out.append({"edition": e, "discriminating_blocks": picks})
    return out


def _tile_for_block(tilemap, block):
    for t, b in tilemap.items():
        if b == block:
            return t
    return None


def localise_protected(img, spec):
    """If the piece shows a fixed protected colour patch, locate it.

    The protected rectangles and their four fixed colours are public.  We find
    the dominant protected-colour extents in the piece, identify which public
    rectangle it is (by the 2x2 colour arrangement, which also reveals rotation),
    and return the corresponding original rectangle as an approximate range.
    """
    cols = [tuple(c) for c in (spec.get("protected_colours")
                                or DEFAULT_PROTECTED_COLOURS)]
    rects = [tuple(r) for r in (spec.get("protected_rects")
                                or DEFAULT_PROTECTED_RECTS)]
    tol = 10
    xs, ys = [], []
    for y in range(img.height):
        for x in range(img.width):
            px = img.pixels[y][x]
            for c in cols:
                if (abs(px[0] - c[0]) <= tol and abs(px[1] - c[1]) <= tol and
                        abs(px[2] - c[2]) <= tol):
                    xs.append(x)
                    ys.append(y)
                    break
    if len(xs) < 200:
        return None
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    bw, bh = x1 - x0 + 1, y1 - y0 + 1
    if bw < 8 or bh < 8:
        return None
    # quadrant colours of the detected patch (TL, TR, BL, BR)
    def qcol(cx, cy):
        px = img.pixels[cy][cx]
        best, bd = None, 10 ** 9
        for c in cols:
            d = max(abs(px[0] - c[0]), abs(px[1] - c[1]), abs(px[2] - c[2]))
            if d < bd:
                bd, best = d, c
        return best if bd <= tol else None
    mx, my = (x0 + x1) // 2, (y0 + y1) // 2
    quad = (qcol((x0 + mx) // 2, (y0 + my) // 2),
            qcol((mx + x1) // 2, (y0 + my) // 2),
            qcol((x0 + mx) // 2, (my + y1) // 2),
            qcol((mx + x1) // 2, (my + y1) // 2))
    ca, cb, cc, cd = cols[0], cols[1], cols[2], cols[3]
    # expected quadrants under rotation k (clockwise)
    base = (ca, cb, cc, cd)
    rot = None
    for k in range(4):
        # rotating the patch by k*90 clockwise permutes quadrants
        perm = _rotate_quadrants(base, k)
        if quad == perm or quad == _rotate_quadrants(base, (k + 1) % 4):
            rot = k
            break
    if rot is None:
        rot = 0
    # pick the public rectangle whose aspect ratio best matches (allow rotation)
    best_rect, best_err = None, 1e9
    for (ax0, ay0, ax1, ay1) in rects:
        aw, ah = ax1 - ax0, ay1 - ay0
        for (pw, ph) in ((bw, bh), (bh, bw)):
            err = abs(pw / max(1, ph) - aw / max(1, ah))
            if err < best_err:
                best_err, best_rect = err, (ax0, ay0, ax1, ay1)
    return {"original_range": list(best_rect), "approx": True,
            "rotation_deg": rot * 90,
            "detected_patch_px": [x0, y0, x1, y1],
            "confidence": "approximate (public protected rectangle)"}


def _rotate_quadrants(q, k):
    tl, tr, bl, br = q
    for _ in range(k % 4):
        tl, tr, bl, br = bl, tl, br, tr   # 90 deg clockwise
    return (tl, tr, bl, br)


def _block_for_tile(manifest, comp, edition, tile):
    return layout.manifest_tiles(manifest, comp, edition)[tile]


def _overlaps_protected(img, spec):
    """Does the piece visibly contain a protected colour-discrimination patch?

    Counts pixels within a small tolerance of the fixed protected colours on a
    dense, size-adaptive grid.  A genuine teaching patch fills a large region,
    so it yields many hits; ordinary artwork (kept far from these colours) does
    not.
    """
    cols = [tuple(c) for c in (spec.get("protected_colours")
                                or DEFAULT_PROTECTED_COLOURS)]
    tol = 12
    step_x = max(1, img.width // 160)
    step_y = max(1, img.height // 160)
    hits = 0
    total = 0
    for y in range(0, img.height, step_y):
        for x in range(0, img.width, step_x):
            total += 1
            px = img.pixels[y][x]
            for c in cols:
                if (abs(px[0] - c[0]) <= tol and abs(px[1] - c[1]) <= tol and
                        abs(px[2] - c[2]) <= tol):
                    hits += 1
                    break
    # a genuine patch covers a sizeable fraction (>= 6%) of the piece
    return total > 0 and (hits / total) >= 0.06
