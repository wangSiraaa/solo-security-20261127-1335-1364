"""Shift-change proofreader: independently fold the delivered press sheets
and read off the final book.

This module never looks at the expected page order in the signed manifest
to *produce* its result.  It physically folds the printed sheets (its own
simulation), cuts the contracted edges, inserts the insert leaves, turns
the leaves around the spine, and hashes what actually stands upright on
each page.  Page identity and print rotation are recovered from the sheet
pixels alone (a printed page is recognized by matching the approved
content hashes in all four orientations -- exactly what a human
proofreader does when turning a sheet around).  Only afterwards is the
derived sequence compared against the signed manifest.
"""

import json
import os

from . import geom
from .image import Img, load_png_file, normalized_hash, save_png_file


def _extract_cell(img, sheet, W, H, c, r):
    """Crop cell (c,r) (r=0 bottom row) out of a delivered sheet image."""
    m = sheet["margin"]
    cw = (sheet["paper_w"] - 2 * m) // W
    ch = (sheet["paper_h"] - 2 * m) // H
    x = m + c * cw
    y = m + (H - 1 - r) * ch
    cell = Img(cw, ch)
    for yy in range(ch):
        si = ((y + yy) * img.w + x) * 3
        di = yy * cw * 3
        cell.px[di:di + cw * 3] = img.px[si:si + cw * 3]
    return cell


def _recover(cell_img, page_w, page_h, approved_by_hash):
    """Recognize the page printed in a cell and its print rotation.

    Returns (upright_img, rho, hash) where rho is the quarter-turns CW the
    content was printed with, or (None, None, None) if the content is not
    an approved page."""
    cropped = cell_img.center_crop(page_w, page_h)
    for k in (0, 1, 2, 3):
        img = cropped.rot90_cw(k) if k else cropped
        h = normalized_hash(img)
        if h in approved_by_hash:
            return img, (4 - k) % 4, h
    return None, None, None


def _sheet_rotation_from_view(rho_view, flip_axis):
    """Invert the view-rotation formula: find the flat-sheet print rotation
    rho such that content printed with it shows view-rotation rho_view in
    the delivered back-side view."""
    if flip_axis == "vertical":
        T = lambda v: (-v[0], v[1], -v[2])
    else:
        T = lambda v: (v[0], -v[1], -v[2])
    for rho in range(4):
        if geom.RENDER_CW[T(geom.rotz_cw_k(geom.Y, rho))] == rho_view:
            return rho
    raise AssertionError("no sheet rotation maps to view rotation")


def _load_sheet_cells(sheet, page_w, page_h, approved_by_hash):
    """Build content-framed leaves from the delivered sheet images."""
    folds = sheet["folds"]
    W, H = geom.grid_dims_from_folds(folds)
    front = load_png_file(sheet["front"])
    back = load_png_file(sheet["back"])
    flip = sheet["flip_axis"]
    grid = {}
    for c in range(W):
        for r in range(H):
            cell_a = _extract_cell(front, sheet, W, H, c, r)
            img_a, rho_a, h_a = _recover(cell_a, page_w, page_h,
                                         approved_by_hash)
            if flip == "vertical":
                cv, rv = W - 1 - c, r
            else:
                cv, rv = c, H - 1 - r
            cell_b = _extract_cell(back, sheet, W, H, cv, rv)
            img_b, rho_b, h_b = _recover(cell_b, page_w, page_h,
                                         approved_by_hash)
            if h_a is None or h_b is None:
                raise ValueError(
                    "cell (%d,%d) of %s holds content that is not an "
                    "approved page" % (c, r, sheet["front"]))
            # content frames: side A rotates with the sheet; side B's
            # delivered view is the sheet seen around the flip axis, so
            # the recovered view rotation is mapped back to a sheet
            # rotation by inverting the view transform
            fa = [geom.rotz_cw_k(v, rho_a)
                  for v in (geom.X, geom.Y, geom.Z)]
            fb0 = geom.back_frame((geom.X, geom.Y, geom.Z), flip)
            rho_b_sheet = _sheet_rotation_from_view(rho_b, flip)
            fb = [geom.rotz_cw_k(v, rho_b_sheet) for v in fb0]
            leaf = geom.Leaf((img_a, h_a), (img_b, h_b), fa, fb)
            grid[(c, r)] = [leaf]
    return grid, W, H


def _read_book(sheets_per_signature, direction, cuts, page_w, page_h,
               approved_by_hash):
    """Fold every signature, insert inserts, and read the book.

    Returns (read_hashes, openings) with openings a list of (left, right)
    hash pairs as the reader sees them.
    """
    read_hashes = []
    read_images = []
    for sig in sheets_per_signature:
        grid, W, H = _load_sheet_cells(sig["main"], page_w, page_h,
                                       approved_by_hash)
        stack, closed = geom.fold_sheet(grid, W, H, sig["main"]["folds"])
        spine = closed - set(cuts)
        if len(spine) != 1:
            raise ValueError("cannot open the book: closed edges %s, cuts %s"
                             % (sorted(closed), sorted(cuts)))
        spine_dir = next(iter(spine))
        if not set(cuts) <= closed:
            raise ValueError("contract cuts an edge that is not folded")
        for ins in sig["inserts"]:
            igrid, _, _ = _load_sheet_cells(ins["sheet"], page_w, page_h,
                                            approved_by_hash)
            ileaf = igrid[(0, 0)][0]
            stack.insert(ins["after_leaf"], ileaf)  # after Nth leaf (1-based)
        # orient the packet as the reader would: cover upright
        top = stack[0]
        first = top.fa if top.fa[2] == geom.Z else top.fb
        if first[1] == geom.Y:
            rot = 0
        elif first[1] == geom.neg(geom.Y):
            rot = 180
        else:
            raise ValueError("book is not side-bound; unsupported")
        if rot == 180:
            for leaf in stack:
                leaf.rotz180()
            spine_dir = geom.rot_dir_180(spine_dir)
        want = "W" if direction == "ltr" else "E"
        if spine_dir != want:
            raise ValueError("spine on %s but binding is %s"
                             % (spine_dir, direction))
        # read: turn each leaf around the (vertical) spine
        for leaf in stack:
            for frames, payload in ((leaf.fa, leaf.front),
                                    (leaf.fb, leaf.back)):
                if frames[2] == geom.Z:
                    read_hashes.append(payload[1])
                    read_images.append(payload[0])
                    if frames[1] != geom.Y:
                        raise ValueError(
                            "page not upright in the finished book")
            leaf.flip_v()  # reader turns the leaf
            for frames, payload in ((leaf.fa, leaf.front),
                                    (leaf.fb, leaf.back)):
                if frames[2] == geom.Z:
                    read_hashes.append(payload[1])
                    read_images.append(payload[0])
                    if frames[1] != geom.Y:
                        raise ValueError(
                            "page not upright in the finished book")
    openings = []
    for k in range(0, len(read_hashes) - 2, 2):
        a = (read_hashes[k + 1], read_images[k + 1])
        b = (read_hashes[k + 2], read_images[k + 2])
        openings.append((a, b) if direction == "ltr" else (b, a))
    return (read_hashes, read_images), openings


def fold_and_read(output_index_path, contract, manifest, approved_by_hash,
                  out_dir):
    """Fold the imposed output and verify it against the signed manifest.

    approved_by_hash: {normalized_hash: Img} of the approved pages (taken
    from the verified mail -- used only to recognize pages, never their
    expected order).
    """
    os.makedirs(out_dir, exist_ok=True)
    with open(output_index_path) as f:
        index = json.load(f)
    direction = manifest["binding_direction"]
    cuts = contract.get("cuts", [])

    dims = {(im.w, im.h) for im in approved_by_hash.values()}
    if len(dims) != 1:
        raise ValueError("heterogeneous page sizes unsupported")
    page_w, page_h = next(iter(dims))

    sigs = []
    for sig_entry in index["signatures"]:
        main_sheet = sig_entry["sheets"][0]
        inserts = []
        for ins in sig_entry["inserts"]:
            inserts.append({"after_leaf": ins["after_leaf"],
                            "sheet": ins["sheets"][0]})
        sigs.append({"main": main_sheet, "inserts": inserts})

    (read_hashes, read_images), openings = _read_book(
        sigs, direction, cuts, page_w, page_h, approved_by_hash)

    expected = list(manifest["page_order"])
    report = {
        "contract": contract["id"],
        "positions_read": len(read_hashes),
        "order_matches_manifest": read_hashes == expected,
        "openings_checked": [],
        "approvals": [],
    }

    for ap in manifest.get("approvals", []):
        if ap["scope"] != "opening":
            continue
        li, ri = ap["left"], ap["right"]
        m = (min(li, ri) - 1) // 2  # opening index in the reading sequence
        ok = False
        stitched_hash = None
        if 0 <= m < len(openings):
            (lh, limg), (rh, rimg) = openings[m]
            ok = (lh == expected[li - 1] and rh == expected[ri - 1])
            stitched = limg.hconcat(rimg)
            stitched_hash = normalized_hash(stitched)
            if ap.get("spread_sha256"):
                ok = ok and (stitched_hash == ap["spread_sha256"])
                save_png_file(stitched, os.path.join(
                    out_dir, "spread_p%d_p%d.png" % (li, ri)))
        report["openings_checked"].append({"left": li, "right": ri, "ok": ok})
        report["approvals"].append({
            "scope": "opening", "left": li, "right": ri,
            "text": ap["text"], "verified": bool(ok),
            "stitched_sha256": stitched_hash})
    for ap in manifest.get("approvals", []):
        if ap["scope"] == "signature":
            report["approvals"].append({
                "scope": "signature", "signature": ap["signature"],
                "text": ap["text"],
                "verified": bool(report["order_matches_manifest"])})
        elif ap["scope"] == "book":
            report["approvals"].append({
                "scope": "book", "text": ap["text"],
                "verified": bool(report["order_matches_manifest"])})

    with open(os.path.join(out_dir, "reading_order.json"), "w") as f:
        json.dump({"positions": read_hashes}, f, indent=1)
    with open(os.path.join(out_dir, "fold_report.json"), "w") as f:
        json.dump(report, f, indent=1, ensure_ascii=False)
    return report
