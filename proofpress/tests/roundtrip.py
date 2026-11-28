"""Round-trip self-test for the geometry core: impose random books with
every contract variant, fold the sheets back, and require pixel-exact
recovery of the reading sequence.  Run: python3 tests/roundtrip.py
"""

import os
import random
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from prooflib.contract import reader_rotation_for, spine_of
from prooflib.image import Img, normalized_hash, save_png_file
from prooflib.imposition import render_sheet
from prooflib.foldcheck import _read_book

PAGE_W, PAGE_H = 40, 56

CONTRACTS = {
    "A": {"id": "A", "paper_w": 200, "paper_h": 240, "margin": 20,
          "flip_axis": "vertical",
          "folds": [{"axis": "v", "move": "left", "place": "over"},
                    {"axis": "h", "move": "bottom", "place": "over"}],
          "cuts": ["S"]},
    "B": {"id": "B", "paper_w": 220, "paper_h": 220, "margin": 20,
          "flip_axis": "horizontal",
          "folds": [{"axis": "h", "move": "top", "place": "under"},
                    {"axis": "v", "move": "right", "place": "over"}],
          "cuts": ["N"]},
    "C": {"id": "C", "paper_w": 200, "paper_h": 240, "margin": 20,
          "flip_axis": "vertical",
          "folds": [{"axis": "h", "move": "bottom", "place": "over"},
                    {"axis": "v", "move": "left", "place": "over"}],
          "cuts": ["S"]},
    "D": {"id": "D", "paper_w": 200, "paper_h": 240, "margin": 20,
          "flip_axis": "horizontal",
          "folds": [{"axis": "v", "move": "right", "place": "under"},
                    {"axis": "h", "move": "top", "place": "over"}],
          "cuts": ["N"]},
}


def random_page(rng):
    img = Img(PAGE_W, PAGE_H)
    img.px[:] = bytes(rng.randrange(256) for _ in range(PAGE_W * PAGE_H * 3))
    return img


def main():
    rng = random.Random(20261128)
    failures = 0
    for name, contract in CONTRACTS.items():
        for direction in ("ltr", "rtl"):
            try:
                spine = spine_of(contract)
                rot = reader_rotation_for(contract, direction)
            except ValueError as e:
                print("SKIP contract %s dir=%s: %s" % (name, direction, e))
                continue
            positions = list(range(1, 9))
            pages = {p: random_page(rng) for p in positions}
            front, back = render_sheet(
                dict(contract), positions, pages, contract["folds"],
                contract["flip_axis"], rot, "T")
            approved = {normalized_hash(p): p for p in pages.values()}
            with tempfile.TemporaryDirectory() as td:
                fp, bp = os.path.join(td, "f.png"), os.path.join(td, "b.png")
                save_png_file(front, fp)
                save_png_file(back, bp)
                sheet = {"front": fp, "back": bp,
                         "paper_w": contract["paper_w"],
                         "paper_h": contract["paper_h"],
                         "margin": contract["margin"],
                         "folds": contract["folds"],
                         "flip_axis": contract["flip_axis"]}
                (hashes, _imgs), _openings = _read_book(
                    [{"main": sheet, "inserts": []}], direction,
                    contract["cuts"], PAGE_W, PAGE_H, approved)
            want = [normalized_hash(pages[p]) for p in positions]
            ok = hashes == want
            failures += 0 if ok else 1
            print("%s contract %s dir=%s spine=%s rot=%d" %
                  ("OK " if ok else "FAIL", name, direction, spine, rot))
            if not ok:
                print("  want:", [w[:8] for w in want])
                print("  got :", [g[:8] if g else None for g in hashes])
    if failures:
        sys.exit(1)
    print("all geometry round-trips passed")


if __name__ == "__main__":
    main()
