"""Imposition: turn the approved reading sequence into front/back press
sheets with registration marks, per the shop's binding contract.

Method: build the flat sheet of leaves, fold it forward with the contract's
fold sequence, and see where every leaf lands in the reader's packet.  The
reading positions are then assigned to leaf sides in packet order, and each
page is printed rotated so that it stands upright once the reader orients
the finished booklet (the reader rotation is fixed by the spine side the
binding direction demands).
"""

import json
import os

from . import contract as contract_mod
from . import geom
from .image import Img, save_png_file
from .pdf import write_pdf

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
GRAY = (190, 190, 190)
RED = (200, 30, 30)


def cell_origin(contract, W, H, c, r):
    """Image-space (y down) origin of cell (c,r) (r=0 is the bottom row)."""
    m = contract["margin"]
    cw = (contract["paper_w"] - 2 * m) // W
    ch = (contract["paper_h"] - 2 * m) // H
    return m + c * cw, m + (H - 1 - r) * ch, cw, ch


def _draw_marks(img, contract, W, H, label):
    m = contract["margin"]
    pw, ph = contract["paper_w"], contract["paper_h"]
    off = max(6, m // 2)
    for cx, cy in ((off, off), (pw - 1 - off, off),
                   (off, ph - 1 - off), (pw - 1 - off, ph - 1 - off)):
        img.crosshair(cx, cy, max(5, m // 4), BLACK)
    img.crosshair(pw // 2, off, max(4, m // 5), BLACK)
    img.crosshair(pw // 2, ph - 1 - off, max(4, m // 5), BLACK)
    img.crosshair(off, ph // 2, max(4, m // 5), BLACK)
    img.crosshair(pw - 1 - off, ph // 2, max(4, m // 5), BLACK)
    for c in range(W):
        for r in range(H):
            x, y, cw, ch = cell_origin(contract, W, H, c, r)
            img.rect(x, y, x + cw, y + ch, GRAY)
            t = 6
            for tx, ty in ((x, y), (x + cw - 1, y), (x, y + ch - 1),
                           (x + cw - 1, y + ch - 1)):
                img.hline(tx - t, tx + t + 1, ty, BLACK)
                img.vline(tx, ty - t, ty + t + 1, BLACK)
    img.draw_text(m, max(2, m // 2 - 4), label, RED, scale=2)


def plan_sheet(positions, folds, flip_axis, reader_rot):
    """Fold the sheet forward and compute, for every sheet cell and side,
    which reading position it carries and the print rotation (quarter
    turns CW, in sheet coords) that makes it upright in the reader's book.

    Returns {(c, r): {"A": (pos, rho), "B": (pos, rho)}}, W, H.
    """
    W, H = geom.grid_dims_from_folds(folds)
    n_leaves = W * H
    if len(positions) != 2 * n_leaves:
        raise ValueError("positions do not match fold grid")
    # fold cell-tagged leaves forward to learn every leaf's packet position
    tagged = {}
    for c in range(W):
        for r in range(H):
            fa = (geom.X, geom.Y, geom.Z)
            tagged[(c, r)] = [geom.Leaf((c, r), (c, r), fa,
                                        geom.back_frame(fa, flip_axis))]
    stack, _closed = geom.fold_sheet(tagged, W, H, folds)
    plan = {}
    for k, leaf in enumerate(stack):
        cell = leaf.front  # the tag
        fa, fb = leaf.fa, leaf.fb
        if reader_rot == 180:
            fa = [geom.rotz180(v) for v in fa]
            fb = [geom.rotz180(v) for v in fb]
        sides = {}
        for name, frame in (("A", fa), ("B", fb)):
            pos = positions[2 * k] if frame[2] == geom.Z else positions[2 * k + 1]
            # a flat-sheet print rotation rho is conjugated by the folds;
            # for sides whose folded normal is -Z the conjugation flips
            # the rotation's sense, so the inverse map applies
            if frame[2] == geom.Z:
                rho = geom.DEROTATE_CW[frame[1]]
            else:
                rho = geom.RENDER_CW[frame[1]]
            if rho in (1, 3):
                raise ValueError(
                    "contract produces 90-degree page rotation; unsupported "
                    "for non-square pages")
            sides[name] = (pos, rho)
        plan[cell] = sides
    return plan, W, H


def render_sheet(contract, positions, images_by_pos, folds, flip_axis,
                 reader_rot, label):
    """Render one sheet (front & back).  positions: reading-order list of
    global positions for this sheet's leaves (length 2 * W * H)."""
    plan, W, H = plan_sheet(positions, folds, flip_axis, reader_rot)
    pw, ph = contract["paper_w"], contract["paper_h"]
    front = Img.filled(pw, ph, WHITE)
    back = Img.filled(pw, ph, WHITE)
    _draw_marks(front, contract, W, H, label + " FRONT")
    _draw_marks(back, contract, W, H, label + " BACK")
    for (c, r), sides in plan.items():
        # side A: printed rotation rho in sheet coords
        pos_a, rho_a = sides["A"]
        x, y, cw, ch = cell_origin(contract, W, H, c, r)
        _place(front, images_by_pos[pos_a], rho_a, x, y, cw, ch)
        # side B: the delivered back image is the view around the flip axis
        pos_b, rho_b = sides["B"]
        if flip_axis == "vertical":
            cv, rv = W - 1 - c, r
            T = lambda v: (-v[0], v[1], -v[2])
        else:
            cv, rv = c, H - 1 - r
            T = lambda v: (v[0], -v[1], -v[2])
        up_in_view = T(geom.rotz_cw_k(geom.Y, rho_b))
        rho_view = geom.RENDER_CW[up_in_view]
        x2, y2, cw2, ch2 = cell_origin(contract, W, H, cv, rv)
        _place(back, images_by_pos[pos_b], rho_view, x2, y2, cw2, ch2)
    return front, back


def _place(canvas, page_img, rot_cw, x, y, cw, ch):
    img = page_img.rot90_cw(rot_cw) if rot_cw else page_img
    if img.w > cw or img.h > ch:
        raise ValueError("page does not fit the cell")
    canvas.paste(img, x + (cw - img.w) // 2, y + (ch - img.h) // 2)


def impose_book(manifest, images_by_pos, contract, out_dir, label_prefix):
    """Impose the whole approved book.  Returns an output index dict."""
    os.makedirs(out_dir, exist_ok=True)
    reader_rot = contract_mod.reader_rotation_for(
        contract, manifest["binding_direction"])
    index = {"contract": contract["id"],
             "binding_direction": manifest["binding_direction"],
             "reader_rotation": reader_rot,
             "signatures": [], "pdf": None}
    pdf_pages = []
    for sig in sorted(manifest["signatures"], key=lambda s: s["id"]):
        c_sig = contract_mod.contract_for_signature(contract, sig["id"])
        main = sig["positions"]
        label = "%s SIG%d" % (label_prefix, sig["id"])
        front, back = render_sheet(c_sig, main, images_by_pos,
                                   c_sig["folds"], c_sig["flip_axis"],
                                   reader_rot, label)
        f_path = os.path.join(out_dir, "sig%d_sheet_front.png" % sig["id"])
        b_path = os.path.join(out_dir, "sig%d_sheet_back.png" % sig["id"])
        save_png_file(front, f_path)
        save_png_file(back, b_path)
        pdf_pages += [front, back]
        sig_entry = {"id": sig["id"], "sheets": [{
            "role": "main", "front": f_path, "back": b_path,
            "paper_w": c_sig["paper_w"], "paper_h": c_sig["paper_h"],
            "margin": c_sig["margin"], "folds": c_sig["folds"],
            "flip_axis": c_sig["flip_axis"]}], "inserts": []}
        for ins in sorted(sig.get("inserts", []),
                          key=lambda i: i["after_leaf"]):
            poslist = ins["positions"]
            for k in range(0, len(poslist), 2):
                pair = poslist[k:k + 2]
                i_contract = dict(c_sig)
                i_contract["paper_w"] = (c_sig["paper_w"] - 2 * c_sig["margin"])
                i_contract["paper_h"] = (c_sig["paper_h"] - 2 * c_sig["margin"])
                i_contract["margin"] = max(8, c_sig["margin"] // 2)
                ilabel = "%s SIG%d INSERT AFTER-L%d" % (
                    label_prefix, sig["id"], ins["after_leaf"])
                ifront, iback = render_sheet(i_contract, pair, images_by_pos,
                                             [], c_sig["flip_axis"],
                                             reader_rot, ilabel)
                ip_f = os.path.join(
                    out_dir, "sig%d_insert%d_front.png" % (sig["id"], k // 2 + 1))
                ip_b = os.path.join(
                    out_dir, "sig%d_insert%d_back.png" % (sig["id"], k // 2 + 1))
                save_png_file(ifront, ip_f)
                save_png_file(iback, ip_b)
                pdf_pages += [ifront, iback]
                sig_entry["inserts"].append({
                    "after_leaf": ins["after_leaf"],
                    "sheets": [{"role": "insert", "front": ip_f, "back": ip_b,
                                "paper_w": i_contract["paper_w"],
                                "paper_h": i_contract["paper_h"],
                                "margin": i_contract["margin"], "folds": [],
                                "flip_axis": c_sig["flip_axis"]}]})
        index["signatures"].append(sig_entry)
    pdf_path = os.path.join(out_dir, "imposed_book.pdf")
    write_pdf(pdf_path, pdf_pages)
    index["pdf"] = pdf_path
    with open(os.path.join(out_dir, "output_index.json"), "w") as f:
        json.dump(index, f, indent=1)
    return index
