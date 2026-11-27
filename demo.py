#!/usr/bin/env python3
"""End-to-end walkthrough of the archive courier.

Runs offline and prints, in order:

  A. Export a carrier manifest for a work order, then read it back as the
     recipient (both 'layout' and 'shard' packing).
  B. The carrier's view: box count, box sequence, per-box byte size — and
     that two legal options produce an identical view.
  C. Experiment 1: change one long word's *representation* and recompute the
     packing commitment; restoring is exact.  Spelling changes do nothing.
  D. Experiment 2: restore the dictionary, then replace the carrier's one big
     box with two smaller boxes and check the same pages still ship.
  E. The divergent-compression case: naive per-version packing would open an
     extra box for the longer option; reserved shapes prevent it.
"""

import json

from archive_courier import engine, experiments, fixtures
from archive_courier.crypto import SealedStore
from archive_courier.dictionary import Dictionary

KEY = b"recipient-key-0000-demo-0000"
TAG = b"box-tag-00000000"


def rule(title):
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def carrier_view(manifest):
    return {
        "box_count": manifest["box_count"],
        "boxes": [{"seq": b["seq"], "bytes": b["bytes"]} for b in manifest["boxes"]],
        "commitment": manifest["commitment"][:16] + "...",
    }


def section_a():
    rule("A. Export manifest, then receive (offline round trip)")
    order = fixtures.demo_order()
    d = Dictionary.from_name("tiny")
    for method in ("layout", "shard"):
        store = SealedStore(KEY, box_tag=TAG)
        man = engine.transmit(order, "protected", store, method, d,
                              max_box_plain=40)
        pages = engine.receive(man, store, d)
        print(f"\n[{method}] carrier manifest:")
        print(json.dumps(carrier_view(man), indent=2))
        print(f"[{method}] recipient reads back:")
        for i, words in enumerate(pages):
            print(f"  page {i}: {' '.join(words)}")


def section_b():
    rule("B. Carrier view is identical for both legal options")
    order = fixtures.demo_order()
    d = Dictionary.from_name("tiny")
    views = {}
    for v in engine.legal_versions(order):
        store = SealedStore(KEY, box_tag=TAG)
        man = engine.transmit(order, v, store, "layout", d, max_box_plain=33)
        views[v] = carrier_view(man)
    for v, view in views.items():
        print(f"\noption {v!r} -> {json.dumps(view)}")
    print("\nidentical external record:", len(
        {json.dumps(v, sort_keys=True) for v in views.values()}) == 1)


def section_c():
    rule("C. Experiment 1: change one long word's representation")
    r = experiments.experiment_remap_commitment(fixtures.demo_order(), capacity=40)
    print(f"slot            : {r['slot']}")
    print(f"code            : {r['old_code']} -> {r['new_code']}")
    print(f"box shapes      : {r['shapes_before']} -> {r['shapes_after']}")
    print(f"commitment      : {r['commitment_before'][:16]}... "
          f"-> {r['commitment_after'][:16]}...")
    print(f"commitment moved: {r['commitment_changed']}")
    print(f"restore exact   : {r['restore_is_identity']} "
          f"(shapes back to {r['shapes_restored']})")
    print(f"spelling-only change moves nothing: {r['respell_is_identity']}")
    print(f"each option still indistinguishable before/after: "
          f"{r['option_indistinguishable_before']}/"
          f"{r['option_indistinguishable_after']}")


def section_d():
    rule("D. Experiment 2: restore dict, replace one big box with two small")
    r = experiments.experiment_replace_box(fixtures.demo_order())
    print(f"capacity        : {r['capacity_big']}B (big) -> "
          f"{r['capacity_small']}B (small)")
    print(f"box count       : {r['boxes_before']} -> {r['boxes_after']}")
    print(f"box sizes       : {r['sizes_before']} -> {r['sizes_after']}")
    print(f"options indistinguishable before: "
          f"{r['before_options_indistinguishable']}")
    print(f"options indistinguishable after : "
          f"{r['after_options_indistinguishable']}")
    print(f"recipient content unchanged     : {r['content_unchanged']}")
    print("pages after re-boxing:")
    for v, pages in r["pages_after"].items():
        print(f"  option {v!r}:")
        for i, words in enumerate(pages):
            print(f"    page {i}: {' '.join(words)}")


def section_e():
    rule("E. Divergent compression: no extra box for either option")
    r = experiments.demonstrate_divergent_compression(fixtures.demo_order())
    print(f"auto box capacity       : {r['capacity']}B")
    print(f"per-version page lengths: {r['per_version_page_lengths']}")
    print(f"naive per-version boxes : {r['naive_box_counts']} "
          f"(would differ: {r['naive_would_differ']})")
    print(f"reserved-shape boxes    : {r['reserved_box_counts']}")
    print(f"reserved box sizes      : {r['reserved_box_sizes']}")
    print(f"same boxes for every option: {r['same_boxes_for_all']}")
    print(f"no extra box for either    : {r['no_extra_box_for_either']}")


def main():
    section_a()
    section_b()
    section_c()
    section_d()
    section_e()
    print("\nAll sections completed offline.")


if __name__ == "__main__":
    main()
