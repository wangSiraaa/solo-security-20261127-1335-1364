#!/usr/bin/env python3
"""Demo driver: acceptance + two delivery modes + perturbations + audio.

Produces, under ./out:
    report.json           full structured report
    fixed/track_A.wav     FIXED delivery two-track lesson (wide-margin course)
    fixed/track_B.wav
    onsite/track_A.wav    ONSITE delivery two-track lesson (narrow-margin course)
    onsite/track_B.wav

All synthetic and offline.  Deterministic (seeded).
"""

import json
import os
import sys

from sonic_lab import pipeline


def _dump(path, obj):
    with open(path, "w") as fh:
        json.dump(obj, fh, indent=2, default=str)


def main(out_dir="out"):
    os.makedirs(out_dir, exist_ok=True)
    report = {}

    # ---- Delivery mode 1: FIXED (offline-fixed boards + bands) -------------
    # A wide-margin course the rotating teacher can run from issued slots.
    fixed = pipeline.plan_lesson("fixed", band_pairs=[(4, 5)])
    report["fixed"] = pipeline.summarize(fixed)
    if fixed.slot:
        report["fixed"]["tracks"] = pipeline.render_tracks(
            fixed.slot, os.path.join(out_dir, "fixed"))

    # ---- Delivery mode 2: ONSITE (on-site test signals) --------------------
    # A narrow-margin low-band course only on-site acceptance can take.
    onsite = pipeline.plan_lesson("onsite", band_pairs=[(0, 3)])
    report["onsite"] = pipeline.summarize(onsite)
    if onsite.slot:
        report["onsite"]["tracks"] = pipeline.render_tracks(
            onsite.slot, os.path.join(out_dir, "onsite"))

    # ---- Perturbation A: lengthen one board's tail (early class) -----------
    report["perturb_tail"] = pipeline.perturb_tail(fixed, band_pairs=[(4, 5)])

    # ---- Perturbation B: restore boards, change only class B's seat --------
    report["perturb_seat_wide"] = pipeline.perturb_seat(fixed, "B2")
    report["perturb_seat_narrow"] = pipeline.perturb_seat(onsite, "B2")

    _dump(os.path.join(out_dir, "report.json"), report)

    # console summary
    def line(tag, d):
        if d.get("contract_ok") is None:
            print(f"{tag:<10} no feasible slot")
            return
        m = d.get("measured", {})
        print(f"{tag:<10} OK  cfg={d.get('config')} seats={d.get('seats')} "
              f"bands={d.get('band_alloc')} margin={d.get('predicted_margin_db')}dB")
        for k, v in m.items():
            print(f"            {k}: own_snr={v['own_snr']:+.1f}dB "
                  f"detect={v['detect']:+.1f}dB")

    print("=== DELIVERY MODES ===")
    line("FIXED", report["fixed"])
    line("ONSITE", report["onsite"])
    print("\n=== PERTURBATION A: board tail lengthened ===")
    pa = report["perturb_tail"]
    print(f"  required gap {pa['old_required_gap_beats']} -> "
          f"{pa['new_required_gap_beats']} beats; "
          f"early class needs extra beat: {pa['early_class_needs_extra_beat']}; "
          f"still feasible: {pa['still_feasible']}")
    print("\n=== PERTURBATION B: seat change (B -> B2) ===")
    for tag in ("perturb_seat_wide", "perturb_seat_narrow"):
        pb = report[tag]
        print(f"  {tag}: original slot still OK: "
              f"{pb.get('original_slot_still_ok')}  -> {pb.get('note')}")
    print(f"\nReport: {os.path.join(out_dir, 'report.json')}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "out"))
