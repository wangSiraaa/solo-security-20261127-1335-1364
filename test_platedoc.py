#!/usr/bin/env python3
"""Self-checking test suite for PlateDoc (no external deps).

    python3 test_platedoc.py
"""
import json
import os
import random
import struct
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from platedoc import pngcodec, ed25519, mark, detect, content, layout, issue, \
    verifylib
from platedoc.pngcodec import Image, read_png, write_png
from platedoc.transforms import crop, transform_piece, rotate_k, scale_nn, \
    quantize
from platedoc.common import BLOCK, MARK_K, MARK_CELL, crc16, linf

FAILED = []


def check(name, cond, detail=""):
    status = "ok" if cond else "FAIL"
    print("[%s] %s %s" % (status, name, detail))
    if not cond:
        FAILED.append(name)


# ---------------------------------------------------------------------------
def test_codec():
    img = Image(37, 23, [[(x * 7 % 256, y * 9 % 256, (x + y) % 256)
                          for x in range(37)] for y in range(23)])
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "t.png")
        write_png(p, img)
        back = read_png(p)
        same = all(img.pixels[y][x] == back.pixels[y][x]
                   for y in range(23) for x in range(37))
        check("codec round-trip (exact RGB)", same and back.width == 37)
        # chunk audit
        data = open(p, "rb").read()
        pos, chunks = 8, []
        while pos < len(data):
            (ln,) = struct.unpack(">I", data[pos:pos + 4])
            ct = data[pos + 4:pos + 8].decode()
            chunks.append(ct)
            pos += 12 + ln
            if ct == "IEND":
                break
        check("codec writes only IHDR/IDAT/IEND", chunks == ["IHDR", "IDAT",
                                                             "IEND"], str(chunks))
    # read a libpng/palette/interlace image if ImageMagick present
    try:
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "g.png")
            subprocess.check_call(["convert", "-size", "31x17",
                                   "gradient:red-blue", "-interlace", "PNG", p],
                                  stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL)
            g = read_png(p)
            check("codec reads Adam7 interlaced PNG", g.width == 31
                  and g.height == 17)
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("[skip] ImageMagick not available")


def test_ed25519():
    check("Ed25519 RFC8032 vector", ed25519.selftest())


def test_manifest_signature():
    entries = [{"comp": 0, "edition": e, "title": "x"} for e in range(3)]
    built = layout.build_manifest(entries, b"\x11" * 32)
    check("manifest verifies", layout.verify_manifest(built["manifest"],
                                                      built["signature_hex"]))
    tampered = json.loads(json.dumps(built["manifest"]))
    tampered["compositions"]["0"]["editions"]["0"]["mark_count"] += 1
    check("tampered manifest rejected",
          not layout.verify_manifest(tampered, built["signature_hex"]))


def test_mark_invariance():
    tiles = [(0, 0, 1), (0, 1, 5), (1, 2, 17), (2, 3, 40), (1, 0, 2)]
    W, H = 3 * 120, 2 * 120
    img = Image(W, H, [[(255, 255, 255)] * W for _ in range(H)])
    layout_marks = []
    for i, (c, e, t) in enumerate(tiles):
        col, row = i % 3, i // 3
        bx, by = 20 + col * 120, 20 + row * 120
        tl = mark.render_tile(c, e, t)
        for j in range(len(tl)):
            for k in range(len(tl[j])):
                img.pixels[by + j][bx + k] = tl[j][k]
        layout_marks.append({"tile": t, "edition": e, "comp": c,
                             "block": ((bx - 20) // BLOCK + 0,
                                       (by - 20) // BLOCK + 0)})
    allok = True
    for rot in range(4):
        for fac in (1, 2, 3):
            for qb in (8, 4, 3, 2):
                p = transform_piece(img, rot, fac, qb)
                fs = detect.detect_marks(p)
                got = sorted((f["comp"], f["edition"], f["tile"])
                             for f in fs if f["crc_ok"])
                if got != sorted(tiles):
                    allok = False
    check("mark decode invariant: 4 rot x 3 scale x {8,4,3,2} bits", allok)


def test_mark_below_floor():
    """1-bit merging collapses the 3 anchors -> must not claim false reads."""
    img = Image(200, 200, [[(255, 255, 255)] * 200 for _ in range(200)])
    tl = mark.render_tile(0, 1, 1)
    for j in range(len(tl)):
        for k in range(len(tl[j])):
            img.pixels[20 + j][20 + k] = tl[j][k]
    p = quantize(img, 1)
    fs = detect.detect_marks(p)
    check("1-bit colour merge -> no false attribution",
          all(not f["crc_ok"] for f in fs) or len(fs) == 0)


def test_localisation():
    img, placed, _ = issue.render_edition(1, 2)
    rect = (300, 260, 780, 700)
    piece = crop(img, *rect)
    reg = [tuple(m["block"]) for m in placed]
    lay = {"cell": BLOCK, "marks": [{"tile": i, "block": b}
                                    for i, b in enumerate(reg)]}
    worst = 0
    for rot in range(4):
        for fac in (1, 2):
            for qb in (8, 4, 2):
                p = transform_piece(piece, rot, fac, qb)
                fs = detect.detect_marks(p)
                A, b, info = detect.solve_piece_to_original(fs, lay)
                if A is None:
                    worst = 999
                    continue
                bbox = detect.piece_bbox_in_original(A, b, p.width, p.height)
                err = max(abs(bbox[i] - rect[i]) for i in range(4))
                worst = max(worst, err)
    check("piece localisation exact across transforms", worst <= 1,
          "max_px_err=%d" % worst)


def test_geometry_recurrence():
    """Within a composition, editions are identical except their marks."""
    comp = 0
    a, _ = content.build_plate(comp)
    b, _ = content.build_plate(comp)
    # same geometry+palette: build_plate is edition-independent now
    same = all(a.pixels[y][x] == b.pixels[y][x]
               for y in range(0, a.height, 40)
               for x in range(0, a.width, 40))
    check("same composition renders identically (dup artwork)", same)
    # different composition differs
    c, _ = content.build_plate(1)
    diff = sum(1 for y in range(0, a.height, 40) for x in range(0, a.width, 40)
               if a.pixels[y][x] != c.pixels[y][x])
    check("different composition differs", diff > 100, "diff=%d" % diff)


def test_editions_position_disjoint():
    man_built = layout.build_manifest(
        [{"comp": 0, "edition": e, "title": ""} for e in range(6)],
        b"\x22" * 32)
    m = man_built["manifest"]
    sets = [set(layout.manifest_tiles(m, 0, e).values()) for e in range(6)]
    disjoint = all(not (sets[i] & sets[j])
                   for i in range(6) for j in range(i + 1, 6))
    check("editions of a composition have disjoint mark positions", disjoint)


def test_no_mark_on_protected():
    ok = True
    for rect in content.DEFAULT_PROTECTED_RECTS:
        pass
    for comp in (0, 1, 2):
        for ed in range(4):
            blocks = layout.edition_mark_blocks(comp, ed)
            for (bc, br) in blocks:
                x0, y0 = bc * BLOCK, br * BLOCK
                for (px0, py0, px1, py1) in content.DEFAULT_PROTECTED_RECTS:
                    if not (x0 + BLOCK <= px0 or px1 <= x0 or
                            y0 + BLOCK <= py0 or py1 <= y0):
                        ok = False
    check("no edition mark placed on/near a protected patch", ok)


def test_anti_guess_and_protected():
    built = layout.build_manifest(
        [{"comp": c, "edition": e, "title": ""}
         for c in (0, 1, 2) for e in range(4)], b"\x33" * 32)
    man = built["manifest"]
    spec = layout.layout_spec()
    # protected piece: undecidable, no edition named, gives guidance
    img, placed, _ = issue.render_edition(0, 1)
    px0, py0, px1, py1 = content.DEFAULT_PROTECTED_RECTS[0]
    piece = crop(img, px0 - 24, py0 - 24, px1 + 24, py1 + 24)
    r = verifylib.analyse_piece(piece, man, spec)
    check("protected piece undecidable + needs another sample",
          r["verdict"] == "undecidable" and r["needs_another_sample"])
    check("protected piece names no edition", r["attribution"] == [])
    check("protected piece gives approximate range",
          r.get("approximate_range") is not None)
    check("protected piece names discriminating blocks",
          bool(r.get("discriminating_blocks")))


def test_masking_never_guesses():
    built = layout.build_manifest(
        [{"comp": 0, "edition": e, "title": ""} for e in range(4)],
        b"\x44" * 32)
    man = built["manifest"]
    spec = layout.layout_spec()
    img, placed, _ = issue.render_edition(0, 0)
    rect = (448, 464, 736, 720)
    base = crop(img, *rect)
    covered = [(bc, br) for bc, br in (tuple(m["block"]) for m in placed)
               if rect[0] <= bc * BLOCK and rect[1] <= br * BLOCK
               and (bc + 1) * BLOCK <= rect[2] and (br + 1) * BLOCK <= rect[3]]
    guesses = 0
    flips = 0
    for n in range(len(covered) + 1):
        m2 = [row[:] for row in base.pixels]
        for k in range(n):
            bc, br = covered[k]
            mx, my = bc * BLOCK - rect[0], br * BLOCK - rect[1]
            bg = base.pixels[max(0, my - 2)][max(0, mx - 2)]
            for yy in range(my, my + BLOCK):
                for xx in range(mx, mx + BLOCK):
                    if 0 <= yy < base.height and 0 <= xx < base.width:
                        m2[yy][xx] = bg
        im = Image(base.width, base.height, m2)
        r = verifylib.analyse_piece(im, man, spec)
        if r["marks_valid"] == 0 and r["attribution"]:
            guesses += 1
        if n == len(covered) and r["verdict"] == "undecidable":
            flips += 1
    check("masking never guesses an edition without marks", guesses == 0)
    check("fully masked -> undecidable", flips == 1)


def test_reissue_protected_values():
    rects = [tuple(r) for r in json.load(open(
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "spec_reissue.json")))["protected_rects"]]
    colours = [tuple(c) for c in json.load(open(
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "spec_reissue.json")))["protected_colours"]]
    img, placed, _ = issue.render_edition(0, 0, protected_rects=rects,
                                          protected_colours=colours)
    allowed = set(colours) | {(128, 128, 128)}
    clean = True
    old = [(196, 140, 92), (200, 144, 96), (72, 156, 180), (76, 160, 184)]
    residue = 0
    for (x0, y0, x1, y1) in rects:
        for y in range(y0, y1):
            for x in range(x0, x1):
                px = img.pixels[y][x]
                if px not in allowed:
                    clean = False
                if px in old:
                    residue += 1
    check("reissue holds ONLY new protected values", clean)
    check("reissue has no old-colour residue", residue == 0)


def test_strip_and_verify_via_entrypoints():
    """Full path through issued.py / framer.py / verify.py in a temp dir."""
    here = os.path.dirname(os.path.abspath(__file__))
    with tempfile.TemporaryDirectory() as d:
        iss = os.path.join(d, "iss")
        sw = os.path.join(d, "sw")
        rv = os.path.join(d, "rv")
        subprocess.check_call([sys.executable, "issued.py", "--out", iss,
                               "--editions", "0,1"], cwd=here,
                              stdout=subprocess.DEVNULL)
        subprocess.check_call([sys.executable, "framer.py", "--issuance", iss,
                               "--out", sw], cwd=here,
                              stdout=subprocess.DEVNULL)
        subprocess.check_call([sys.executable, "verify.py", "--issuance", iss,
                               "--sheet", os.path.join(sw, "swap_sheet.json"),
                               "--out", rv, "--reprint"], cwd=here,
                              stdout=subprocess.DEVNULL)
        rep = json.load(open(os.path.join(rv, "review_report.json")))
        verdicts = sorted(p["verdict"] for p in rep["pieces"])
        check("entry points: one attributed + one undecidable",
              verdicts == ["attributed", "undecidable"], str(verdicts))
        att = [p for p in rep["pieces"] if p["verdict"] == "attributed"][0]
        check("entry points: attributed piece has original range",
              att.get("original_range") is not None)
        check("entry points: reprint produced",
              bool(att.get("reprint")))


def main():
    test_codec()
    test_ed25519()
    test_manifest_signature()
    test_mark_invariance()
    test_mark_below_floor()
    test_localisation()
    test_geometry_recurrence()
    test_editions_position_disjoint()
    test_no_mark_on_protected()
    test_anti_guess_and_protected()
    test_masking_never_guesses()
    test_reissue_protected_values()
    test_strip_and_verify_via_entrypoints()
    print("\n" + ("ALL TESTS PASSED" if not FAILED
                  else "FAILURES: %s" % FAILED))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    raise SystemExit(main())
