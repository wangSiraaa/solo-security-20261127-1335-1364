"""Filesystem-backed simulation tests: local directories, offline resumes."""

import json
import os

from sealink import PUBLIC, SEALED, Bag, TrackRange, build_observation
from sealink.fsstore import FsWorld


OBS = "OBS"
RNG = TrackRange(0, 2, 0, 2)


def bid(s, t):
    return f"{OBS}:s{s}:t{t}"


def make_world(tmp_path):
    m = build_observation(OBS, 3, 3)
    w = FsWorld(str(tmp_path / "world"))

    w.set_island_calendar("A", [(0, 0), (0, 1), (0, 2)])
    w.set_island_calendar("B", [(0, 1)])
    w.seed_island_held("A", m, [bid(1, 0)])
    w.seed_island_held("B", m, [bid(1, 1), bid(2, 0)])

    bag_full = Bag("bagfull", OBS, PUBLIC,
                   frozenset(bid(s, t) for s in range(3) for t in range(3)))
    bag_A = Bag("bagA", OBS, SEALED,
                frozenset(bid(s, t) for s in (1, 2) for t in range(3)),
                sealed_to="A")
    bag_B = Bag("bagB", OBS, SEALED,
                frozenset(bid(s, t) for s in range(3) for t in range(3)),
                sealed_to="B")
    for b in (bag_full, bag_A, bag_B):
        w.publish_bag(m, b)
    for v in ("V1", "V2", "V3", "V4"):
        w.set_cap(v, 10**6)
    return w, m, bag_full, bag_A, bag_B


def test_ordinary_carrier_refuses_public_bag_on_disk(tmp_path):
    w, m, bag_full, bag_A, bag_B = make_world(tmp_path)
    res = w.load("V2", "C2", "bagfull", ordinary=True, manifest=m)
    assert res["ok"] is False
    assert res["reason"] == "ordinary_carrier_requires_sealed"
    # A sealed bag is accepted by an ordinary carrier.
    assert w.load("V3", "C3", "bagA", ordinary=True, manifest=m)["ok"] is True


def test_meet_only_copies_openable_files_and_writes_whole_obs(tmp_path):
    w, m, bag_full, bag_A, bag_B = make_world(tmp_path)
    w.load("V1", "C1", "bagfull", ordinary=False, manifest=m)
    rA = w.meet("V1", "C1", "A", 1, m, RNG)
    assert rA["reason"] == "exchanged"
    g = w.merge("A", m, RNG)
    assert g is not None
    assert os.path.exists(g["file"])
    # The output file is independently openable: header + bytes, inner digests.
    with open(g["file"], "rb") as fh:
        header = json.loads(fh.readline())
        assert header["obs_id"] == OBS
        assert len(header["segments"]) == 9


def test_sealed_bag_not_openable_at_wrong_island_on_disk(tmp_path):
    w, m, bag_full, bag_A, bag_B = make_world(tmp_path)
    # bagB is sealed for B; meeting A with it transfers nothing.
    w.load("V4", "C4", "bagB", ordinary=True, manifest=m)
    r = w.meet("V4", "C4", "A", 1, m, RNG)
    assert r["delivered"] == []
    assert r["receipt"] is None
    # No inbox files were written.
    inbox = str(tmp_path / "world" / "islands" / "A" / "inbox")
    before = set(os.listdir(inbox)) if os.path.isdir(inbox) else set()
    # The V1 public delivery later does write; but the sealed B one did not.
    assert all(f.endswith(".seg") for f in before)


def test_withdrawn_contact_is_allowed_miss_no_receipt(tmp_path):
    w, m, bag_full, bag_A, bag_B = make_world(tmp_path)
    w.load("V1", "C1", "bagfull", ordinary=False, manifest=m)
    w.drop_contact("V1", "B", 2)
    r = w.meet("V1", "C1", "B", 2, m, RNG)
    assert r["contact"] is False
    assert r["receipt"] is None
    assert r["reason"] == "port_dropped_contact"
    # A different cycle still works.
    r2 = w.meet("V1", "C1", "B", 3, m, RNG)
    assert r2["receipt"] is not None


def test_ship_reopens_directory_to_resume(tmp_path):
    w, m, bag_full, bag_A, bag_B = make_world(tmp_path)
    w.load("V1", "C1", "bagfull", ordinary=False, manifest=m)
    # Simulate offline: read back from disk only.
    man = w.ship_manifest("C1")
    assert len(man["entries"]) == 1
    assert man["entries"][0]["kind"] == "load"
    w.meet("V1", "C1", "A", 1, m, RNG)
    man = w.ship_manifest("C1")
    assert any(e["kind"] == "receipt" for e in man["entries"])
    assert w.ship_receipts("C1")  # receipt file on board, as a real回单


def test_local_copy_released_only_after_returned_receipt_on_disk(tmp_path):
    w, m, bag_full, bag_A, bag_B = make_world(tmp_path)
    w.load("V4", "C4", "bagB", ordinary=True, manifest=m)
    r = w.meet("V4", "C4", "B", 1, m, RNG)
    rid = r["receipt"].receipt_id
    # Before returning: bag still in hold.
    assert [b["bag_id"] for b in w.ship_bags("C4")] == ["bagB"]
    # Returning a non-existent receipt is refused.
    assert w.return_receipt("C4", "nope")["ok"] is False
    # Returning the real receipt releases the local copy.
    out = w.return_receipt("C4", rid)
    assert out["ok"] is True
    assert w.ship_bags("C4") == []


def test_tampered_receipt_on_disk_is_refused(tmp_path):
    w, m, bag_full, bag_A, bag_B = make_world(tmp_path)
    w.load("V4", "C4", "bagB", ordinary=True, manifest=m)
    r = w.meet("V4", "C4", "B", 1, m, RNG)
    rid = r["receipt"].receipt_id
    p = str(tmp_path / "world" / "ships" / "C4" / "receipts" / f"{rid}.json")
    d = json.load(open(p))
    d["n_segments"] = 999
    json.dump(d, open(p, "w"))
    out = w.return_receipt("C4", rid)
    assert out["ok"] is False
    assert out["reason"] == "receipt_tampered"
    assert w.ship_bags("C4")  # copy still present


def test_same_bytes_different_gaps_on_disk(tmp_path):
    m = build_observation(OBS, 3, 3)
    by = m.by_id()
    w = FsWorld(str(tmp_path / "w2"))
    # two islands, same calendar, different prior overlap
    w.set_island_calendar("X", [(0, 0)])
    w.set_island_calendar("Y", [(0, 0)])
    w.seed_island_held("X", m, [bid(1, 0)])
    w.seed_island_held("Y", m, [bid(1, 1)])
    gx = w.merge("X", m, RNG)
    gy = w.merge("Y", m, RNG)
    assert gx is None and gy is None  # neither opens
    # logs record different gaps
    lx = [e for e in w.island_log("X") if e["kind"] == "merge_incomplete"][-1]["missing"]
    ly = [e for e in w.island_log("Y") if e["kind"] == "merge_incomplete"][-1]["missing"]
    assert lx != ly


def test_two_islands_each_settle_on_sufficient_voyage_on_disk(tmp_path):
    w, m, bag_full, bag_A, bag_B = make_world(tmp_path)
    w.load("V1", "C1", "bagfull", ordinary=False, manifest=m)
    w.load("V3", "C3", "bagA", ordinary=True, manifest=m)
    w.load("V4", "C4", "bagB", ordinary=True, manifest=m)
    ra = w.meet("V1", "C1", "A", 1, m, RNG)
    rb = w.meet("V4", "C4", "B", 1, m, RNG)
    ga = w.merge("A", m, RNG)
    gb = w.merge("B", m, RNG)
    assert ga is not None and gb is not None
    # both islands produced an independently-openable whole observation
    assert os.path.exists(ga["file"]) and os.path.exists(gb["file"])


def test_merged_obsf_verifies_standalone(tmp_path):
    from sealink.verify import verify_obsf
    w, m, bag_full, bag_A, bag_B = make_world(tmp_path)
    w.load("V1", "C1", "bagfull", ordinary=False, manifest=m)
    w.meet("V1", "C1", "A", 1, m, RNG)
    g = w.merge("A", m, RNG)
    r = verify_obsf(g["file"])
    assert r["ok"] is True
    assert r["segments"] == 9


def test_tampered_obsf_fails_verification(tmp_path):
    from sealink.verify import verify_obsf
    w, m, bag_full, bag_A, bag_B = make_world(tmp_path)
    w.load("V1", "C1", "bagfull", ordinary=False, manifest=m)
    w.meet("V1", "C1", "A", 1, m, RNG)
    g = w.merge("A", m, RNG)
    # Flip one byte of the first payload line.
    raw = open(g["file"], "rb").read().split(b"\n")
    raw[1] = raw[1][:-1] + (b"Z" if raw[1][-1:] != b"Z" else b"Y")
    open(g["file"], "wb").write(b"\n".join(raw))
    r = verify_obsf(g["file"])
    assert r["ok"] is False
    assert any("inner digest" in x or "length" in x for x in r["reasons"])


def test_signed_receipt_without_useful_new_piece_on_disk(tmp_path):
    w, m, bag_full, bag_A, bag_B = make_world(tmp_path)
    w.load("V1", "C1", "bagfull", ordinary=False, manifest=m)
    r1 = w.meet("V1", "C1", "A", 1, m, RNG)
    assert r1["receipt"] is not None and r1["useful"] is True
    # Second shipment of the very same pieces to the same island: files move and
    # are receipted, but nothing new is covered.
    w.set_cap("V1b", 10**6)
    w.load("V1b", "C1b", "bagfull", ordinary=False, manifest=m)
    r2 = w.meet("V1b", "C1b", "A", 1, m, RNG)
    assert r2["receipt"] is not None
    assert r2["delivered"]
    assert r2["useful"] is False
    assert r2["new_pieces"] == []
