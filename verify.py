#!/usr/bin/env python3
"""verify.py -- 证据复核入口 (evidence review entry point).

Reads a "换页清单" (swap sheet) describing loose pieces, treats each piece as
bare pixels (no filename, no ancillary chunks -- the swap sheet carries only an
opaque piece id and the PNG path), and reports for every piece:

  * the edition it can be attributed to (only if proved by readable marks),
  * the range in the original plate where the piece was cut from,
  * or that it is undecidable and another sample is required.

It will NOT force a name onto the "most similar" edition.

    python3 verify.py --issuance out/issuance --sheet out/swap_sheet.json \
                      --out out/review [--reprint]
"""
import argparse
import io
import json
import os
import sys

from platedoc import layout, verifylib
from platedoc.pngcodec import read_png, write_png
from platedoc.common import BLOCK, sha256_hex
from platedoc.transforms import crop
from platedoc.iohelp import save_json, ensure_dir, load_json


def strip_piece(path):
    """Re-read a piece as bare pixels (drops filename context & any chunks)."""
    img = read_png(path)
    return img


def load_issuance(path):
    manifest = load_json(os.path.join(path, "edition_manifest.json"))
    sig = open(os.path.join(path, "edition_manifest.sig")).read().strip()
    spec = load_json(os.path.join(path, "layout_spec.json"))
    verified = layout.verify_manifest(manifest, sig)
    return manifest, sig, spec, verified


def original_plate_path(issuance, comp, edition):
    return os.path.join(issuance, "plates", "plate_c%d_e%d.png"
                        % (comp, edition))


def reprint_crop(issuance, comp, edition, bbox, out_path):
    """Re-print the corresponding clear illustration from the edition's
    original plate, using the located range."""
    src = original_plate_path(issuance, comp, edition)
    plate = read_png(src)
    x0, y0, x1, y1 = bbox
    x0 = max(0, x0); y0 = max(0, y0)
    x1 = min(plate.width, x1); y1 = min(plate.height, y1)
    crop_img = crop(plate, x0, y0, x1, y1)
    write_png(out_path, crop_img)
    return {"path": out_path, "size": [crop_img.width, crop_img.height],
            "source": src}


def main(argv=None):
    ap = argparse.ArgumentParser(description="证据复核入口 (verification)")
    ap.add_argument("--issuance", default="out/issuance")
    ap.add_argument("--sheet", required=True)
    ap.add_argument("--out", default="out/review")
    ap.add_argument("--reprint", action="store_true",
                    help="re-print attributed pieces from the original plate")
    args = ap.parse_args(argv)

    out = ensure_dir(args.out)
    manifest, sig, spec, verified = load_issuance(args.issuance)
    if not verified:
        print("WARNING: edition manifest signature did NOT verify")

    sheet = load_json(args.sheet)
    pieces = sheet.get("pieces", [])
    results = []
    reprint_dir = ensure_dir(os.path.join(out, "reprints"))

    for p in pieces:
        pid = p.get("id") or os.path.splitext(os.path.basename(p["png"]))[0]
        path = p["png"]
        if not os.path.isabs(path):
            path = os.path.join(os.path.dirname(args.sheet), path)
        img = strip_piece(path)
        res = verifylib.analyse_piece(img, manifest, spec, piece_id=pid)
        res["source_sha256"] = sha256_hex(open(path, "rb").read())

        if args.reprint and res["verdict"] == "attributed" \
                and res.get("original_range"):
            conf = res["attribution"][0]
            bbox = res["original_range"]
            rp = os.path.join(reprint_dir, "reprint_%s.png" % pid)
            info = reprint_crop(args.issuance, conf["comp"], conf["edition"],
                                bbox, rp)
            res["reprint"] = info
        results.append(res)

    summary = {
        "issuance": args.issuance,
        "manifest_signature_valid": verified,
        "pieces_reviewed": len(results),
        "attributed": sum(1 for r in results if r["verdict"] == "attributed"),
        "need_another_sample": sum(1 for r in results
                                   if r["needs_another_sample"]),
    }
    save_json(os.path.join(out, "review_report.json"),
              {"summary": summary, "pieces": results})

    print("复核完成 / review complete")
    print("  pieces:", len(results), "| attributed:", summary["attributed"],
          "| need-another-sample:", summary["need_another_sample"],
          "| manifest-valid:", verified)
    return 0


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    raise SystemExit(main())
