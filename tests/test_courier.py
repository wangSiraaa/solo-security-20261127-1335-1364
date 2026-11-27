import hashlib
import hmac
import struct

import pytest

from archive_courier import codec, container, crypto, dictionary, engine, experiments, fixtures
from archive_courier.crypto import SEAL_OVERHEAD, SealedStore
from archive_courier.util import ACError

KEY = b"recipient-key-0000-demo-0000"
TAG = b"box-tag-00000000"


# --------------------------------------------------------------------------
# codec: round-trip and public-length invariance
# --------------------------------------------------------------------------
@pytest.mark.parametrize("method", ["layout", "shard"])
def test_codec_roundtrip(method):
    triples = [(0, None), (200, 300), (5, None), (1, 17)]
    blob = codec.encode_page(triples, method)
    n, back = codec.decode_page(blob, method)
    assert n == len(triples)
    assert back == triples


@pytest.mark.parametrize("method", ["layout", "shard"])
def test_codec_length_independent_of_secret(method):
    a = [(0, 1), (200, 300), (5, 17)]
    b = [(0, 4000), (200, 2), (5, 1)]
    assert codec.page_len(a, method) == codec.page_len(b, method)


def test_shard_constant_length_across_whole_page():
    p1 = [(0, 1)] * 10
    p2 = [(200, 4000)] * 10
    assert len(codec.encode_page(p1, "shard")) == len(codec.encode_page(p2, "shard"))


def test_shard_length_is_function_of_token_count_only():
    for n in (1, 5, 50):
        lens = {
            len(codec.encode_page([(p, s) for p, s in variants], "shard"))
            for variants in (
                [(0, None)] * n,
                [(200, 4000)] * n,
                [(i % 300, (i * 7) % 4000) for i in range(n)],
            )
        }
        assert len(lens) == 1


def test_layout_varint_width_matters():
    # code < 128 is 1 byte, >= 128 is 2 bytes -> representation drives length
    small = [(0, None)]
    big = [(200, None)]
    assert codec.page_len(big, "layout") == codec.page_len(small, "layout") + 1


def test_codec_rejects_oversized_shard_code():
    with pytest.raises(ACError):
        codec.encode_page([(70000, None)], "shard")


def test_layout_trailing_bytes_rejected():
    blob = codec.encode_page([(0, None)], "layout")
    with pytest.raises(ACError):
        codec.decode_page(blob + b"\x00", "layout")


# --------------------------------------------------------------------------
# sealed boxes
# --------------------------------------------------------------------------
def test_sealed_box_roundtrip_and_opacity():
    s = SealedStore(KEY, box_tag=TAG)
    s.seal("b0", b"secret page data")
    assert s.open("b0") == b"secret page data"
    blob = s.blob("b0")
    assert b"secret" not in blob
    assert s.peek("b0") == {"box": "b0", "bytes": len(blob)}


def test_sealed_box_wrong_key_fails():
    s = SealedStore(KEY, box_tag=TAG)
    s.seal("b0", b"x")
    other = SealedStore(b"wrong-key", box_tag=TAG)
    with pytest.raises(ACError):
        other.open("b0")


def test_sealed_box_byte_size_is_plaintext_plus_overhead():
    s = SealedStore(KEY, box_tag=TAG)
    s.seal("b0", b"abcde")
    assert s.peek("b0")["bytes"] == 5 + SEAL_OVERHEAD


def test_sealed_box_tamper_detected():
    s = SealedStore(KEY, box_tag=TAG)
    s.seal("b0", b"hello")
    blob = bytearray(s.blob("b0"))
    blob[-1] ^= 0xFF
    s._boxes["b0"] = bytes(blob)
    with pytest.raises(ACError):
        s.open("b0")


def test_duplicate_seal_rejected():
    s = SealedStore(KEY, box_tag=TAG)
    s.seal("b0", b"a")
    with pytest.raises(ACError):
        s.seal("b0", b"b")


# --------------------------------------------------------------------------
# container padding
# --------------------------------------------------------------------------
def test_pad_and_strip_roundtrip():
    pages = [b"aa", b"bbbb", b"c"]
    body = container.pack_box(pages)
    padded = container.pad_body(body, len(body) + 5)
    assert len(padded) == len(body) + 5
    assert container.strip_padding(padded) == pages


def test_pad_exact_length_no_marker():
    body = container.pack_box([b"abc"])
    assert container.pad_body(body, len(body)) == body
    assert container.strip_padding(body) == [b"abc"]


def test_pad_body_too_small_raises():
    body = container.pack_box([b"abc"])
    with pytest.raises(ACError):
        container.pad_body(body, len(body) - 1)


# --------------------------------------------------------------------------
# engine: the core guarantees
# --------------------------------------------------------------------------
def _manifest_sizes(man):
    return [b["bytes"] for b in man["boxes"]]


@pytest.mark.parametrize("method", ["layout", "shard"])
def test_manifest_identical_across_versions(method):
    order = fixtures.demo_order()
    versions = engine.legal_versions(order)
    d = dictionary.Dictionary.from_name("tiny")
    manifests = {}
    for v in versions:
        store = SealedStore(KEY, box_tag=TAG)
        man = engine.transmit(order, v, store, method, d, max_box_plain=40)
        manifests[v] = man
        assert _manifest_sizes(man) == _manifest_sizes(man)  # sanity
    base = manifests[versions[0]]
    for v in versions[1:]:
        assert manifests[v] == base


@pytest.mark.parametrize("method", ["layout", "shard"])
def test_recipient_recovers_exact_pages(method):
    order = fixtures.demo_order()
    d = dictionary.Dictionary.from_name("tiny")
    store = SealedStore(KEY, box_tag=TAG)
    man = engine.transmit(order, "protected", store, method, d, max_box_plain=40)
    pages = engine.receive(man, store, d)
    flat = " ".join(" ".join(p) for p in pages)
    assert "seal-at-night" in flat
    assert "withhold" in flat
    assert "defer" in flat


def test_no_version_gets_extra_box():
    order = fixtures.demo_order()
    d = dictionary.Dictionary.from_name("tiny")
    counts = {}
    for v in engine.legal_versions(order):
        store = SealedStore(KEY, box_tag=TAG)
        man = engine.transmit(order, v, store, "layout", d, max_box_plain=33)
        counts[v] = man["box_count"]
    assert len(set(counts.values())) == 1


def test_reserved_shape_covers_worst_version():
    order = fixtures.demo_order()
    d = dictionary.Dictionary.from_name("tiny")
    plan = engine.build_plan(order, "layout", d, max_box_plain=None)
    # worst-case page size must be >= every version's page size
    for v in engine.legal_versions(order):
        resolved = engine.resolve(order, v, d)
        for i, triples in enumerate(resolved):
            assert codec.page_len(triples, "layout") <= plan.page_sizes[i]


def test_capacity_too_small_raises():
    order = fixtures.demo_order()
    d = dictionary.Dictionary.from_name("tiny")
    with pytest.raises(ACError):
        engine.build_plan(order, "layout", d, max_box_plain=5)


def test_illegal_version_rejected():
    order = fixtures.demo_order()
    d = dictionary.Dictionary.from_name("tiny")
    store = SealedStore(KEY, box_tag=TAG)
    with pytest.raises(ACError):
        engine.transmit(order, "nope", store, "layout", d)


def test_mismatched_version_sets_rejected():
    bad = {
        "id": "bad",
        "pages": [
            [("opt", "seal", {"A": 1, "B": 2})],
            [("opt", "seal", {"A": 1})],
        ],
    }
    with pytest.raises(ACError):
        engine.legal_versions(bad)


def test_unknown_slot_rejected():
    bad = {"id": "x", "pages": [[("pub", "no-such-word")]]}
    d = dictionary.Dictionary.from_name("tiny")
    with pytest.raises(ACError):
        engine.resolve(bad, "default", d)


def test_receive_rejects_wrong_dictionary():
    order = fixtures.demo_order()
    d = dictionary.Dictionary.from_name("tiny")
    store = SealedStore(KEY, box_tag=TAG)
    man = engine.transmit(order, "public", store, "layout", d, max_box_plain=40)
    wrong = dictionary.Dictionary.from_name("wide")  # different longword code
    with pytest.raises(ACError):
        engine.receive(man, store, wrong)


def test_receive_with_matching_remapped_dictionary_works():
    order = fixtures.demo_order()
    d = dictionary.Dictionary.from_name("tiny")
    remapped = d.copy().remap("longword", 50)
    store = SealedStore(KEY, box_tag=TAG)
    man = engine.transmit(order, "public", store, "layout", remapped,
                          max_box_plain=40)
    pages = engine.receive(man, store, remapped)
    assert any("consolidates" in w for p in pages for w in p)


# --------------------------------------------------------------------------
# commitment
# --------------------------------------------------------------------------
def test_commitment_independent_of_version():
    order = fixtures.demo_order()
    d = dictionary.Dictionary.from_name("tiny")
    commits = set()
    for v in engine.legal_versions(order):
        plan = engine.build_plan(order, "layout", d, max_box_plain=40)
        commits.add(plan.commitment())
    assert len(commits) == 1


def test_commitment_tracks_representation_but_not_spelling():
    order = fixtures.demo_order()
    d = dictionary.Dictionary.from_name("tiny")
    base = engine.build_plan(order, "layout", d, max_box_plain=40).commitment()

    spell = d.copy().respell("longword", "unbelievably-long-text")
    assert engine.build_plan(order, "layout", spell,
                             max_box_plain=40).commitment() == base

    remap = d.copy().remap("longword", 50)  # 200 -> 50 crosses 1/2-byte width
    assert engine.build_plan(order, "layout", remap,
                             max_box_plain=40).commitment() != base


def test_fingerprint_excludes_text():
    d1 = dictionary.Dictionary.from_name("tiny")
    d2 = d1.copy().respell("longword", "totally different spelling")
    assert d1.fingerprint() == d2.fingerprint()


def test_remap_collision_rejected():
    d = dictionary.Dictionary.from_name("tiny")
    with pytest.raises(ACError):
        d.remap("longword", d.code_of("the"))


# --------------------------------------------------------------------------
# experiments
# --------------------------------------------------------------------------
def test_experiment_remap_commitment():
    r = experiments.experiment_remap_commitment(fixtures.demo_order(), capacity=40)
    assert r["commitment_changed"]
    assert r["restore_is_identity"]
    assert r["respell_is_identity"]
    assert r["option_indistinguishable_before"]
    assert r["option_indistinguishable_after"]
    assert r["shapes_before"] != r["shapes_after"]
    assert r["shapes_before"] == r["shapes_restored"]


def test_experiment_replace_box_one_becomes_two():
    r = experiments.experiment_replace_box(fixtures.demo_order())
    assert r["boxes_before"] == 1
    assert r["boxes_after"] == 2
    assert r["before_options_indistinguishable"]
    assert r["after_options_indistinguishable"]
    assert r["content_unchanged"]
    assert r["commitment_before"] != r["commitment_after"]


def test_demonstrate_divergent_compression():
    r = experiments.demonstrate_divergent_compression(fixtures.demo_order())
    assert r["naive_would_differ"]
    assert r["same_boxes_for_all"]
    assert r["no_extra_box_for_either"]
    # each option is recoverable under the shared boxes
    for version, pages in r["pages"].items():
        assert pages


def test_larger_order_multibox_indistinguishable():
    order = fixtures.larger_order()
    d = dictionary.Dictionary.from_name("tiny")
    sizes = None
    for v in engine.legal_versions(order):
        store = SealedStore(KEY, box_tag=TAG)
        man = engine.transmit(order, v, store, "layout", d, max_box_plain=80)
        s = _manifest_sizes(man)
        if sizes is None:
            sizes = s
        assert s == sizes
        assert len(engine.receive(man, store, d)) == len(order["pages"])


# --------------------------------------------------------------------------
# carrier cannot see option from the external record
# --------------------------------------------------------------------------
def test_carrier_view_has_no_option_leak():
    order = fixtures.demo_order()
    d = dictionary.Dictionary.from_name("tiny")
    stores = {}
    mans = {}
    for v in engine.legal_versions(order):
        store = SealedStore(KEY, box_tag=TAG)
        man = engine.transmit(order, v, store, "layout", d, max_box_plain=40)
        stores[v] = store
        mans[v] = man

    # the only carrier-visible data: manifest (same) + blobs (opaque).
    versions = engine.legal_versions(order)
    a, b = versions[0], versions[1]
    assert mans[a] == mans[b]
    for name_a, name_b in zip(stores[a].names(), stores[b].names()):
        blob_a = stores[a].blob(name_a)
        blob_b = stores[b].blob(name_b)
        assert len(blob_a) == len(blob_b)
        # ciphertexts differ (fresh nonces) but carry no plaintext
        for token in ("release", "withhold", "defer", "review", "seal-at-night"):
            assert token.encode() not in blob_a
            assert token.encode() not in blob_b


def test_option_symbols_never_in_plain_manifest_json():
    import json
    order = fixtures.demo_order()
    d = dictionary.Dictionary.from_name("tiny")
    store = SealedStore(KEY, box_tag=TAG)
    man = engine.transmit(order, "protected", store, "layout", d, max_box_plain=40)
    text = json.dumps(man)
    for word in dictionary.OPTIONS.values():
        assert word not in text
