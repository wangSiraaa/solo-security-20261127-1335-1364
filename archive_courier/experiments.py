"""The two requested experiments, plus a divergence demonstration.

1. ``experiment_remap_commitment``
   Change one long word's *representation* (its packed code) in the public
   dictionary, then recompute the packing commitment for the original order.
   The commitment moves because representation drives packed sizes.  Restore
   the dictionary and the commitment returns to the original value.

2. ``experiment_replace_box``
   Restore the dictionary, then have the carrier replace one big box with
   two smaller boxes (a smaller per-box capacity).  Check that the *same*
   pages still ship — identical external record for every option, and the
   recipient still recovers them — so capacity changes leak no option.

3. ``demonstrate_divergent_compression``
   Show the "one option longer, one shorter" case: per-version packing would
   give different box counts, but the reserved-shape plan gives both options
   the same boxes, and the shorter one simply carries more sealed padding.
   It never opens an extra box.
"""

from . import codec, engine
from .crypto import SealedStore
from .util import ACError


def _fresh_store(key=b"recipient-key-0000", tag=b"box-tag-00000000"):
    if len(key) < 1 or len(tag) != 16:
        raise ACError("bad test store parameters")
    return SealedStore(key, box_tag=tag)


def _transmit_all(order, method, dictionary, capacity, key, tag):
    out = {}
    for version in engine.legal_versions(order):
        store = SealedStore(key, box_tag=tag)
        man = engine.transmit(order, version, store, method, dictionary,
                              max_box_plain=capacity)
        out[version] = (man, store, engine.receive(man, store, dictionary))
    return out


def _box_sizes(manifest):
    return [b["bytes"] for b in manifest["boxes"]]


def experiment_remap_commitment(order, method="layout", capacity=None,
                                dictionary=None, slot="longword", new_code=50):
    """Recompute the packing commitment after changing one word's code."""
    from .dictionary import Dictionary
    dictionary = dictionary or Dictionary.from_name("tiny")
    if not isinstance(dictionary, Dictionary):
        raise ACError("dictionary must be a Dictionary")
    versions = engine.legal_versions(order)

    def commit_for(d):
        plan = engine.build_plan(order, method, d, max_box_plain=capacity)
        return plan.commitment(), plan.shapes

    before_commit, before_shapes = commit_for(dictionary)

    # actually ship one version both before and after, to prove the boxes move
    runs_before = _transmit_all(order, method, dictionary, capacity,
                                b"k" * 32, b"t" * 16)

    working = dictionary.copy()
    old_code = working.code_of(slot)
    working.remap(slot, new_code)
    after_commit, after_shapes = commit_for(working)

    runs_after = _transmit_all(order, method, working, capacity,
                               b"k" * 32, b"t" * 16)

    # restore
    restored_commit, restored_shapes = commit_for(dictionary)

    # contrast: changing only the word's *spelling* must change nothing
    respelled = dictionary.copy()
    respelled.respell(slot, respelled.text_of_slot(slot) + "-rev2")
    spelling_commit, spelling_shapes = commit_for(respelled)

    inv = all(
        _box_sizes(runs_before[v][0]) == _box_sizes(runs_before[w][0])
        for v in versions for w in versions)
    inv_after = all(
        _box_sizes(runs_after[v][0]) == _box_sizes(runs_after[w][0])
        for v in versions for w in versions)

    return {
        "slot": slot,
        "old_code": old_code,
        "new_code": new_code,
        "commitment_before": before_commit,
        "commitment_after": after_commit,
        "commitment_restored": restored_commit,
        "commitment_after_respell": spelling_commit,
        "shapes_before": before_shapes,
        "shapes_after": after_shapes,
        "shapes_restored": restored_shapes,
        "shapes_after_respell": spelling_shapes,
        "commitment_changed": before_commit != after_commit,
        "restore_is_identity": restored_commit == before_commit,
        "respell_is_identity": spelling_commit == before_commit,
        "option_indistinguishable_before": inv,
        "option_indistinguishable_after": inv_after,
        "pages_before": {v: runs_before[v][2] for v in versions},
        "pages_after": {v: runs_after[v][2] for v in versions},
    }


def experiment_replace_box(order, method="layout", capacity_big=None,
                           capacity_small=None, dictionary=None):
    """Replace one big box with two small boxes; same pages must still ship."""
    from .dictionary import Dictionary
    dictionary = dictionary or Dictionary.from_name("tiny")

    if capacity_big is None:
        capacity_big = engine.build_plan(
            order, method, dictionary).shapes[0] + 4
    if capacity_small is None:
        # find a capacity at which the same order needs exactly two boxes;
        # if none is found, fall back to half the big capacity.
        capacity_small = _find_capacity_with_boxes(
            order, method, dictionary, target=2)
        if capacity_small is None:
            capacity_small = max(1, capacity_big // 2)

    before = _transmit_all(order, method, dictionary, capacity_big,
                           b"k" * 32, b"t" * 16)
    after = _transmit_all(order, method, dictionary, capacity_small,
                          b"k" * 32, b"t" * 16)

    versions = engine.legal_versions(order)
    before_same = all(
        _box_sizes(before[v][0]) == _box_sizes(before[w][0])
        for v in versions for w in versions)
    after_same = all(
        _box_sizes(after[v][0]) == _box_sizes(after[w][0])
        for v in versions for w in versions)
    content_same = all(
        after[v][2] == before[v][2] for v in versions)

    return {
        "capacity_big": capacity_big,
        "capacity_small": capacity_small,
        "boxes_before": before[versions[0]][0]["box_count"],
        "boxes_after": after[versions[0]][0]["box_count"],
        "sizes_before": _box_sizes(before[versions[0]][0]),
        "sizes_after": _box_sizes(after[versions[0]][0]),
        "commitment_before": before[versions[0]][0]["commitment"],
        "commitment_after": after[versions[0]][0]["commitment"],
        "before_options_indistinguishable": before_same,
        "after_options_indistinguishable": after_same,
        "content_unchanged": content_same,
        "pages_after": {v: after[v][2] for v in versions},
    }


def _naive_box_count(sizes, cap):
    """Greedy packing with no worst-case reservation (per-version)."""
    reserve = 4
    count, cur = 1, reserve
    for s in sizes:
        if cur + 4 + s > cap and cur > reserve:
            count += 1
            cur = reserve
        cur += 4 + s
    return count


def demonstrate_divergent_compression(order, capacity=None,
                                      dictionary=None):
    """One option compresses shorter; both still get the same boxes.

    If ``capacity`` is None we search for a box capacity at which *naive*
    per-version packing yields different box counts for the two options —
    the exact situation the reserved-shape plan must neutralise.
    """
    from .dictionary import Dictionary
    dictionary = dictionary or Dictionary.from_name("tiny")
    versions = engine.legal_versions(order)
    resolved = {v: engine.resolve(order, v, dictionary) for v in versions}
    lengths = {v: [codec.page_len(p, "layout") for p in resolved[v]]
               for v in versions}

    if capacity is None:
        capacity = _find_divergent_capacity(lengths, versions)

    naive_counts = {v: _naive_box_count(lengths[v], capacity) for v in versions}

    runs = _transmit_all(order, "layout", dictionary, capacity,
                         b"k" * 32, b"t" * 16)
    reserved_counts = {v: runs[v][0]["box_count"] for v in versions}
    reserved_sizes = {v: _box_sizes(runs[v][0]) for v in versions}

    return {
        "capacity": capacity,
        "per_version_page_lengths": lengths,
        "naive_box_counts": naive_counts,
        "naive_would_differ": len(set(naive_counts.values())) > 1,
        "reserved_box_counts": reserved_counts,
        "reserved_box_sizes": reserved_sizes,
        "same_boxes_for_all": len({tuple(reserved_sizes[v]) for v in versions}) == 1,
        "no_extra_box_for_either": len(set(reserved_counts.values())) == 1,
        "all_versions_fit": all(
            reserved_sizes[v] == reserved_sizes[versions[0]] for v in versions),
        "pages": {v: runs[v][2] for v in versions},
    }


def _find_divergent_capacity(lengths, versions):
    """Smallest capacity where naive per-version box counts differ."""
    if len(versions) < 2:
        return None
    max_len = max(max(ls) for ls in lengths.values())
    for cap in range(max_len + 4, 4096):
        counts = {_naive_box_count(lengths[v], cap) for v in versions}
        if len(counts) > 1:
            return cap
    return None


def _find_capacity_with_boxes(order, method, dictionary, target):
    """Smallest box capacity that packs the order into exactly ``target`` boxes."""
    try:
        one = engine.build_plan(order, method, dictionary).shapes[0]
    except ACError:
        one = 10_000
    for cap in range(8, one + 1):
        try:
            plan = engine.build_plan(order, method, dictionary, max_box_plain=cap)
        except ACError:
            continue
        if plan.n_boxes() == target:
            return cap
    return None


def _runtime_dictionary():
    from .dictionary import Dictionary
    return Dictionary.from_name("tiny")
