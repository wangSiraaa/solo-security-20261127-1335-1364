#!/usr/bin/env python3
"""run_scenario.py -- end-to-end demonstration of the whole publication flow.

Steps (all using the normal entry points, no special hooks):
  1. issue a normal run (signed manifest + plates)
  2. framer cuts two pieces from two DIFFERENT editions into one swap sheet
  3. verifier reviews the swap sheet (attribution / localisation / need-more)
  4. adversarial A: same piece, only the cut orientation changes -> same result
  5. adversarial B: same piece with discriminating marks progressively masked
  6. reissue: change ONLY the protected scope; new print must hold new values
  7. reissue review: old piece vs new piece conclusions are compared

Writes out/scenario_report.json and prints a human summary.
"""
import json
import os
import subprocess
import sys

from platedoc import issue, verifylib, layout
from platedoc.pngcodec import read_png, write_png, Image
from platedoc.transforms import crop, transform_piece, rotate_k
from platedoc.common import BLOCK, sha256_hex
from platedoc.content import validate_protected_colours
from platedoc.iohelp import save_json, ensure_dir, load_json, protected_colour_guard

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "out")


def sh(cmd):
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("command failed: %s\n%s\n%s"
                           % (" ".join(cmd), r.stdout, r.stderr))
    return r.stdout


def analyse(img, man, spec):
    return verifylib.analyse_piece(img, man, spec)


def load_run(dirpath):
    man = load_json(os.path.join(dirpath, "edition_manifest.json"))
    spec = load_json(os.path.join(dirpath, "layout_spec.json"))
    sig = open(os.path.join(dirpath, "edition_manifest.sig")).read().strip()
    return man, spec, layout.verify_manifest(man, sig)


def main():
    report = {}
    os.makedirs(OUT, exist_ok=True)

    # ---- 1. issue -------------------------------------------------------
    print("[1] 发行制样 / issuing normal run")
    sh([sys.executable, "issued.py", "--out", "out/issuance",
        "--editions", "0,1,2,3,4,5"])
    man, spec, ok = load_run(os.path.join(OUT, "issuance"))
    report["issuance"] = {"signature_valid": ok,
                          "public_key": man["public_key"]}

    # ---- 2. framer ------------------------------------------------------
    print("[2] 装帧师裁两版实发 PNG / framer cuts two editions")
    sh([sys.executable, "framer.py", "--issuance", "out/issuance",
        "--out", "out/swap"])
    sheet = load_json(os.path.join(OUT, "swap", "swap_sheet.json"))
    notes = open(os.path.join(OUT, "swap", "framer_notes.txt"),
                 encoding="utf-8").read()
    report["framer"] = {"sheet": sheet, "notes": notes.splitlines()[1:]}

    # ---- 3. verifier ----------------------------------------------------
    print("[3] 复核席 / inspector reviews the swap sheet")
    sh([sys.executable, "verify.py", "--issuance", "out/issuance",
        "--sheet", "out/swap/swap_sheet.json", "--out", "out/review",
        "--reprint"])
    rev = load_json(os.path.join(OUT, "review", "review_report.json"))
    report["review"] = rev["summary"]
    report["review_pieces"] = [
        {"id": p["piece_id"], "verdict": p["verdict"],
         "edition": (p["attribution"][0]["edition"] if p["attribution"] else None),
         "comp": (p["attribution"][0]["comp"] if p["attribution"] else None),
         "original_range": p.get("original_range"),
         "needs_another_sample": p["needs_another_sample"],
         "notes": p["notes"]}
        for p in rev["pieces"]]
    report["reprint_verified"] = _check_reprint(rev, man)

    # ---- 4. adversarial A: orientation only ----------------------------
    print("[4] 对照A：仅换裁切方向 / orientation-only comparison")
    report["orientation"] = _orientation_test(man, spec)

    # ---- 5. adversarial B: mask discriminating marks --------------------
    print("[5] 对照B：遮去区分信息 / mask discriminating marks")
    report["masking"] = _masking_test(man, spec)

    # ---- 6. reissue (change only protected scope) ----------------------
    print("[6] 再版：仅改保护范围 / reissue changes only protected scope")
    sh([sys.executable, "issued.py", "--out", "out/reissue",
        "--spec", "spec_reissue.json", "--editions", "0,1,2,3,4,5"])
    rman, rspec, rok = load_run(os.path.join(OUT, "reissue"))
    report["reissue"] = _reissue_check(rspec, rok)

    # ---- 7. reissue review: new print holds new values -----------------
    print("[7] 新印样复核 / review new print holds new colour values")
    report["reissue_print"] = _reissue_print_check(rspec)

    save_json(os.path.join(OUT, "scenario_report.json"), report)
    _print_summary(report)
    return 0


def _check_reprint(rev, man):
    """Re-printed crop must match the original plate crop exactly."""
    for p in rev["pieces"]:
        if p["verdict"] != "attributed" or not p.get("reprint"):
            continue
        conf = p["attribution"][0]
        src = read_png(os.path.join(OUT, "issuance", "plates",
                                    "plate_c%d_e%d.png" % (conf["comp"],
                                                           conf["edition"])))
        rp = read_png(p["reprint"]["path"])
        x0, y0, x1, y1 = p["original_range"]
        diff = sum(1 for y in range(rp.height) for x in range(rp.width)
                   if rp.pixels[y][x] != src.pixels[y0 + y][x0 + x])
        return {"piece": p["piece_id"], "pixel_diff_vs_original": diff,
                "ok": diff == 0}
    return None


def _orientation_test(man, spec):
    img, placed, _ = issue.render_edition(0, 0)
    blocks = [tuple(m["block"]) for m in placed]
    rect = (448, 464, 736, 720)
    base = crop(img, *rect)
    out = []
    for rot in range(4):
        p = rotate_k(base, rot)
        r = analyse(p, man, spec)
        out.append({
            "rotation_deg": rot * 90,
            "verdict": r["verdict"],
            "edition": r["attribution"][0]["edition"] if r["attribution"] else None,
            "original_range": r.get("original_range"),
        })
    same = len({(o["edition"], tuple(o["original_range"] or ()))
                for o in out}) == 1
    return {"runs": out, "consistent": same}


def _masking_test(man, spec):
    img, placed, _ = issue.render_edition(0, 0)
    rect = (448, 464, 736, 720)
    base = crop(img, *rect)
    blocks = [tuple(m["block"]) for m in placed]
    covered = [(bc, br) for bc, br in blocks
               if rect[0] <= bc * BLOCK and rect[1] <= br * BLOCK
               and (bc + 1) * BLOCK <= rect[2] and (br + 1) * BLOCK <= rect[3]]
    out = []
    for n_mask in range(0, len(covered) + 1):
        m2 = [row[:] for row in base.pixels]
        for k in range(n_mask):
            bc, br = covered[k]
            mx, my = bc * BLOCK - rect[0], br * BLOCK - rect[1]
            bg = base.pixels[max(0, my - 2)][max(0, mx - 2)]
            for yy in range(my, my + BLOCK):
                for xx in range(mx, mx + BLOCK):
                    if 0 <= yy < base.height and 0 <= xx < base.width:
                        m2[yy][xx] = bg
        im = Image(base.width, base.height, m2)
        r = analyse(im, man, spec)
        out.append({
            "marks_masked": n_mask,
            "verdict": r["verdict"],
            "edition": r["attribution"][0]["edition"] if r["attribution"] else None,
            "evidence": r["distinct_content_evidence"],
            "valid_marks": r["marks_valid"],
        })
    never_guess = all(o["edition"] is not None for o in out
                      if o["valid_marks"] > 0) and \
        all(o["edition"] is None for o in out if o["valid_marks"] == 0)
    return {"runs": out, "never_guesses": never_guess}


def _reissue_check(spec, ok):
    rects = [tuple(r) for r in spec["protected_rects"]]
    cols = [tuple(c) for c in spec["protected_colours"]]
    safe, bad, why = validate_protected_colours(cols)
    img = read_png(os.path.join(OUT, "reissue", "plates", "plate_c0_e0.png"))
    probs = protected_colour_guard(img, rects, cols)
    old = [(196, 140, 92), (200, 144, 96), (72, 156, 180), (76, 160, 184)]
    old_hits = 0
    for (x0, y0, x1, y1) in rects:
        for y in range(y0, y1, 3):
            for x in range(x0, x1, 3):
                if img.pixels[y][x] in old:
                    old_hits += 1
    return {"signature_valid": ok, "protected_colours_safe": safe,
            "guard_problems": probs, "old_colour_residue": old_hits}


def _reissue_print_check(rspec):
    """Cut a new print piece from the protected region; new colours must hold."""
    cols = [tuple(c) for c in rspec["protected_colours"]]
    rman = load_json(os.path.join(OUT, "reissue", "edition_manifest.json"))
    img = read_png(os.path.join(OUT, "reissue", "plates", "plate_c0_e1.png"))
    x0, y0, x1, y1 = rspec["protected_rects"][0]
    piece = transform_piece(crop(img, x0 - 24, y0 - 24, x1 + 24, y1 + 24),
                            rotation=1, factor=2, quant_bits=8)
    # new print piece: contains new protected colours, no edition mark
    r = analyse(piece, rman, rspec)
    new_hits = 0
    for y in range(0, piece.height, 2):
        for x in range(0, piece.width, 2):
            if piece.pixels[y][x] in cols:
                new_hits += 1
    return {"piece_verdict": r["verdict"],
            "protected_overlap_detected": r["protected_overlap"],
            "new_colour_pixels": new_hits,
            "needs_another_sample": r["needs_another_sample"]}


def _print_summary(report):
    print("\n" + "=" * 64)
    print("场景总结 / SCENARIO SUMMARY")
    print("=" * 64)
    rp = report["review_pieces"]
    for p in rp:
        tag = ("版次 c%d-e%d" % (p["comp"], p["edition"])
               if p["edition"] is not None else "无法定版(需补样)")
        rng = ("定位 %s" % (p["original_range"],)) if p["original_range"] else ""
        print("  %-8s -> %-10s %s %s" % (p["id"], p["verdict"], tag, rng))
    print("  reprint pixel-exact:", report["reprint_verified"])
    print("  orientation-only consistent:", report["orientation"]["consistent"])
    print("  masking never guesses:", report["masking"]["never_guesses"])
    r = report["reissue"]
    print("  reissue holds new values:", not r["guard_problems"],
          "| old residue:", r["old_colour_residue"])
    rp2 = report["reissue_print"]
    print("  new print protected detected:", rp2["protected_overlap_detected"],
          "| new-colour px:", rp2["new_colour_pixels"])


if __name__ == "__main__":
    sys.path.insert(0, ROOT)
    raise SystemExit(main())
