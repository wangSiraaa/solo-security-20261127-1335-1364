#!/usr/bin/env python3
"""issued.py -- 发行制样入口 (issuance / sample-production entry point).

Produces ONE normal, publishable edition run:

  * a publisher-signed edition manifest (tile maps per edition),
  * the public layout spec,
  * the approved-edition plate PNGs (plain 8-bit RGB, no metadata),
  * human-readable sample-cutting instructions as separate text,
  * a small edition list ("版次清单") for the verifier.

Use --spec to switch to a *reissue* spec (different protected colours) so the
same tool can produce the post-change print run.

    python3 issued.py --out out/issuance
    python3 issued.py --out out/reissue --spec spec_reissue.json
"""
import argparse
import os
import sys

from platedoc import issue, layout
from platedoc.common import sha256_hex, BLOCK, MARK_CELL
from platedoc.content import (PLATE_W, PLATE_H, DEFAULT_PROTECTED_RECTS,
                              DEFAULT_PROTECTED_COLOURS, validate_protected_colours)
from platedoc.iohelp import save_json, ensure_dir, mark_safety_of_protected

PUBLISHER_SEED = bytes.fromhex(
    "5e11ad0000000000000000000000000000000000000000000000000000a11e5e")


def load_spec(path):
    """Return (protected_rects, protected_colours).  Defaults if path None."""
    if not path:
        return list(DEFAULT_PROTECTED_RECTS), list(DEFAULT_PROTECTED_COLOURS)
    spec = __import__("json").load(open(path, encoding="utf-8"))
    rects = [tuple(r) for r in spec.get("protected_rects",
                                        DEFAULT_PROTECTED_RECTS)]
    cols = [tuple(c) for c in spec.get("protected_colours",
                                       DEFAULT_PROTECTED_COLOURS)]
    return rects, cols


def _sample_windows(comp, edition, rects, reg_blocks):
    """Three sample windows: mark-rich, protected, mixed.

    Windows are axis-aligned rectangles an inspector may cut with an ordinary
    tool.  `expected_marks` lists tile indices fully inside; protected flag
    says whether the window crosses a protected patch.
    """
    reg = {i: b for i, b in enumerate(reg_blocks)}

    def w_at(cx, cy, w=320, h=288):
        x0 = max(0, min(PLATE_W - w, cx - w // 2))
        y0 = max(0, min(PLATE_H - h, cy - h // 2))
        return [x0, y0, x0 + w, y0 + h]

    def marks_in(rect):
        x0, y0, x1, y1 = rect
        return [t for t, (bc, br) in reg.items()
                if bc * BLOCK >= x0 and br * BLOCK >= y0 and
                (bc + 1) * BLOCK <= x1 and (br + 1) * BLOCK <= y1]

    def prot_in(rect):
        x0, y0, x1, y1 = rect
        return any(not (x1 <= px0 or px1 <= x0 or y1 <= py0 or py1 <= y0)
                   for (px0, py0, px1, py1) in rects)

    # mark-rich: a permitted block nearest the plate centre
    def not_prot(b):
        r = [b[0] * BLOCK, b[1] * BLOCK, (b[0] + 1) * BLOCK, (b[1] + 1) * BLOCK]
        return not prot_in(r)
    cand = [b for b in reg_blocks if not_prot(b)]
    cand.sort(key=lambda b: abs(b[0] * BLOCK - PLATE_W / 2) +
              abs(b[1] * BLOCK - PLATE_H / 2))
    rich = (cand[0][0] * BLOCK + BLOCK // 2,
            cand[0][1] * BLOCK + BLOCK // 2) if cand else (PLATE_W // 2,
                                                           PLATE_H // 2)
    px0, py0, px1, py1 = rects[0]
    prot = ((px0 + px1) // 2, (py0 + py1) // 2)

    win = []
    r1 = w_at(*rich)
    win.append({"name": "A_mark_rich", "rect": r1,
                "expected_marks": marks_in(r1), "protected_in_rect": prot_in(r1)})
    r2 = w_at(*prot)
    win.append({"name": "B_colour_teaching", "rect": r2,
                "expected_marks": marks_in(r2), "protected_in_rect": prot_in(r2)})
    r3 = w_at((rich[0] + prot[0]) // 2, (rich[1] + prot[1]) // 2)
    win.append({"name": "C_mixed", "rect": r3,
                "expected_marks": marks_in(r3), "protected_in_rect": prot_in(r3)})
    return win


def main(argv=None):
    ap = argparse.ArgumentParser(description="发行制样入口 (issuance)")
    ap.add_argument("--out", default="out/issuance")
    ap.add_argument("--comps", default="0,1,2")
    ap.add_argument("--editions", default="0,1,2,3,4,5")
    ap.add_argument("--seed", type=int, default=20260101)
    ap.add_argument("--spec", default=None,
                    help="JSON spec overriding protected rects/colours")
    args = ap.parse_args(argv)

    comps = [int(x) for x in args.comps.split(",") if x != ""]
    editions = [int(x) for x in args.editions.split(",") if x != ""]
    rects, colours = load_spec(args.spec)
    ok, bad, why = validate_protected_colours(colours)
    if not ok:
        print("SPEC ERROR: protected colours unsafe:", bad, why)
        return 2

    out = ensure_dir(args.out)
    plates_dir = ensure_dir(os.path.join(out, "plates"))

    entries = [{"comp": c, "edition": e, "title": "comp%d-edition%d" % (c, e)}
               for c in comps for e in editions]

    built = layout.build_manifest(entries, PUBLISHER_SEED, protected_rects=rects)
    manifest = built["manifest"]
    save_json(os.path.join(out, "edition_manifest.json"), manifest)
    with open(os.path.join(out, "edition_manifest.sig"), "w") as f:
        f.write(built["signature_hex"] + "\n")
    with open(os.path.join(out, "publisher_pubkey.hex"), "w") as f:
        f.write(built["public_key_hex"] + "\n")
    save_json(os.path.join(out, "layout_spec.json"),
              layout.layout_spec(rects, colours))

    if not layout.verify_manifest(manifest, built["signature_hex"]):
        print("FATAL: manifest signature self-check failed")
        return 3

    plate_hashes = {}
    sample_meta = {}
    for e in entries:
        meta = issue.issue(plates_dir, e["comp"], e["edition"],
                           protected_rects=rects, protected_colours=colours,
                           seed=args.seed)
        plate_hashes[meta["png"]] = meta["sha256"]
        key = "c%d_e%d" % (e["comp"], e["edition"])
        reg_blocks = [tuple(m["block"]) for m in meta["mark_blocks"]]
        windows = _sample_windows(e["comp"], e["edition"], rects, reg_blocks)
        sample_meta[key] = {
            "comp": e["comp"], "edition": e["edition"],
            "marks": {str(i): list(b) for i, b in enumerate(reg_blocks)},
            "windows": windows,
        }

    with open(os.path.join(out, "plates", "plates.sha256"), "w") as f:
        for name in sorted(plate_hashes):
            f.write("%s  %s\n" % (plate_hashes[name], name))

    lines = [
        "发行方裁片指令 / ISSUER SAMPLE INSTRUCTIONS",
        "=" * 64,
        "公开版式 layout_spec.json ; 版次清单 edition_manifest.json",
        "（清单由 publisher_pubkey.hex 验签，Ed25519）",
        "",
        "允许的普通图像变换: 直角旋转 0/90/180/270°；",
        "最近邻整倍放大 1x/2x/3x；低位色阶归并（每通道保留 >=2 bit）。",
        "交付: 普通无损 PNG，去掉文件名与附加字段。",
        "",
        "取片规则: 至少要让一个完整印记的锚点四角落在片内；",
        "不得把刻意遮住区分信息的片当作可读片。",
        "",
    ]
    for key in sorted(sample_meta, key=lambda k: (sample_meta[k]["comp"],
                                                  sample_meta[k]["edition"])):
        sm = sample_meta[key]
        lines.append("-" * 64)
        lines.append("版次 %s  (PNG: plate_%s.png)" % (key, key))
        for w in sm["windows"]:
            r = w["rect"]
            lines.append("  窗口 %-20s x0=%d y0=%d x1=%d y1=%d (%dx%d)"
                         % (w["name"], r[0], r[1], r[2], r[3],
                            r[2] - r[0], r[3] - r[1]))
            lines.append("     含完整印记: %s   覆盖辨色保护区: %s"
                         % (w["expected_marks"] or "无",
                            "是(色值不可动)" if w["protected_in_rect"] else "否"))
        lines.append("")
    with open(os.path.join(out, "SAMPLE_INSTRUCTIONS.txt"), "w",
              encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    save_json(os.path.join(out, "sample_windows.json"), sample_meta)

    safety = mark_safety_of_protected()
    report = {
        "entries": len(entries), "comps": comps, "editions": editions,
        "manifest_verified": True,
        "public_key": built["public_key_hex"],
        "plate_hashes": plate_hashes,
        "protected_rects": [list(r) for r in rects],
        "protected_colours": [list(c) for c in colours],
        "protected_mark_safety_min_distance": safety[0],
        "notes": [
            "PNG: 8-bit RGB, non-interlaced, chunks IHDR/IDAT/IEND only.",
            "No edition metadata is embedded in any PNG.",
            "Within a composition every edition is identical except its marks.",
            "Protected colour patches keep fixed values; marks avoid them.",
        ],
    }
    save_json(os.path.join(out, "ISSUE_REPORT.json"), report)

    print("发行完成 / issuance complete")
    print("  out:", out, "| editions:", len(entries),
          "| signed:", built["public_key_hex"][:16] + "...")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    raise SystemExit(main())
