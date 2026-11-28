"""Requirement-by-requirement tests for the sealink exercise.

Each test names the clause of the task it pins down.
"""

import copy

import pytest

from sealink import (
    PUBLIC,
    SEALED,
    Bag,
    Calendar,
    CustodyChain,
    HandoffEngine,
    Ledger,
    PortOrder,
    SpaceTime,
    TrackRange,
    Voyage,
    build_observation,
)
from sealink.guarantee import IslandStore, bytes_received
from sealink.scenarios import standard_world


RNG = TrackRange(0, 2, 0, 2)


def bid(obs, seg, t):
    return f"{obs}:s{seg}:t{t}"


# --------------------------------------------------------------------------- #
# "现场只认最终可独立打开的整条观测" / "重开目录续运"
# --------------------------------------------------------------------------- #


def test_island_recognises_only_a_fully_openable_whole_observation():
    w = standard_world()
    A = w.islands["A"]
    obs = w.manifest.obs_id
    by = w.manifest.by_id()

    # Deliver only part of what A is missing: no guarantee.
    part = {bid(obs, 1, 1): by[bid(obs, 1, 1)].data}
    assert A.merge(RNG, part) is None
    assert not A.can_open(RNG)

    # Deliver the rest: A now independently opens the whole window.
    rest = {
        bid(obs, s, t): by[bid(obs, s, t)].data
        for s, t in sorted(A.missing(RNG))
    }
    g = A.merge(RNG, rest)
    assert g is not None
    assert A.can_open(RNG)
    # The opening is continuously sequenced across the window.
    assert g.openings == tuple(
        bid(obs, s, t) for s in range(3) for t in range(3)
    )


def test_duplicate_piece_does_not_clobber_or_inflate():
    w = standard_world()
    A = w.islands["A"]
    obs = w.manifest.obs_id
    by = w.manifest.by_id()
    sid = bid(obs, 1, 1)
    data = by[sid].data
    # Send the same piece twice; store keeps one, no clobber, no double count.
    A.merge(RNG, {sid: data})
    A.merge(RNG, {sid: data})
    assert A.clobbered == 0
    assert set(A.received) & {sid}
    # A junk piece with a mismatched digest is rejected, not stored.
    junk = {sid: data + b"tamper"}
    A.merge(RNG, junk)
    assert A.verified_rejections >= 1
    assert A.received[sid] == data


def test_inner_digest_in_tampered_delivery_is_rejected():
    w = standard_world()
    B = w.islands["B"]
    obs = w.manifest.obs_id
    by = w.manifest.by_id()
    gid = bid(obs, 2, 1)
    bad = b"corrupted payload"
    B.merge(RNG, {gid: bad})
    assert B.verified_rejections == 1
    assert gid not in B.received


# --------------------------------------------------------------------------- #
# "普通承运者仅收封件" / sealed only by designated island
# --------------------------------------------------------------------------- #


def test_ordinary_carrier_rejects_public_bag_and_accepts_sealed():
    w = standard_world()
    eng = w.engine
    voy = Voyage("V-ord", "Ordinary", trusted=False, cap_bytes=10**9)
    pub = Bag("pub1", w.manifest.obs_id, PUBLIC, frozenset([bid(w.manifest.obs_id, 0, 0)]))
    sec = Bag("sec1", w.manifest.obs_id, SEALED,
              frozenset([bid(w.manifest.obs_id, 1, 0)]), sealed_to="A")
    assert eng.load(voy, "shore", pub) is False
    assert eng.load(voy, "shore", sec) is True
    # The refusal is recorded, and it was a public-bag reason.
    last = [e for e in eng.ledger.events if e["kind"] == "load_refused"][-1]
    assert last["reason"] == "ordinary_carrier_requires_sealed"


def test_sealed_bag_only_unsealable_by_designated_island():
    w = standard_world()
    eng = w.engine
    obs = w.manifest.obs_id
    voy = Voyage("V-seal", "Ord", trusted=False, cap_bytes=10**9)
    bag = Bag("bA", obs, SEALED,
              frozenset(bid(obs, s, t) for s in (1, 2) for t in range(3)),
              sealed_to="A")
    assert eng.load(voy, "shore", bag)
    # Meeting B: carrier cannot unseal the bag designated for A.
    res_b = eng.meet(voy, "B", 1, RNG)
    assert res_b["delivered"] == []
    assert res_b["reason"] == "no_usable_bag_for_station"
    assert res_b["receipt"] is None
    # Meeting A: it can be unsealed and, here, completes A's window.
    res_a = eng.meet(voy, "A", 1, RNG)
    assert res_a["delivered"]
    assert res_a["guarantee"] is not None


# --------------------------------------------------------------------------- #
# "港口控制字节和会合窗口" / "撤去一笔约定接触" / "允许航次临时错过"
# --------------------------------------------------------------------------- #


def test_port_withdraws_a_contact_and_voyage_skips_without_receipt():
    w = standard_world()
    eng = w.engine
    obs = w.manifest.obs_id
    voy = Voyage("V1", "C1", trusted=True, cap_bytes=10**9)
    bag = Bag("b", obs, PUBLIC,
              frozenset(bid(obs, s, t) for s in range(3) for t in range(3)))
    assert eng.load(voy, "A", bag)
    eng.port.drop_contact("V1", "A", 1)
    res = eng.meet(voy, "A", 1, RNG)
    assert res["contact"] is False
    assert res["receipt"] is None
    assert res["reason"] == "port_dropped_contact"

    # The voyage may subsequently still meet on another window/cycle.
    res2 = eng.meet(voy, "A", 2, RNG)
    assert res2["contact"] is True
    assert res2["receipt"] is not None


def test_voyage_byte_cap_is_enforced_and_miss_is_allowed():
    w = standard_world()
    eng = w.engine
    obs = w.manifest.obs_id
    by = w.manifest.by_id()
    only = frozenset([bid(obs, 0, 0)])
    cost = by[next(iter(only))].length
    voy = Voyage("Vcap", "Ccap", trusted=False, cap_bytes=cost)
    b1 = Bag("b1", obs, SEALED, only, sealed_to="A")
    b2 = Bag("b2", obs, SEALED, frozenset([bid(obs, 0, 1)]), sealed_to="A")
    assert eng.load(voy, "s", b1) is True
    assert eng.load(voy, "s", b2) is False  # would exceed the cap
    # Missing a window is allowed and yields no receipt; no error is raised.
    eng.port.drop_contact("Vcap", "A", 1)
    r = eng.meet(voy, "A", 1, RNG)
    assert r["receipt"] is None


# --------------------------------------------------------------------------- #
# "实交后带回签收再释放本地副本" / single custody chain
# --------------------------------------------------------------------------- #


def test_local_copy_released_only_after_returned_receipt():
    w = standard_world()
    eng = w.engine
    obs = w.manifest.obs_id
    ids = {bid(obs, 1, 1)}
    voy = Voyage("Vr", "Cr", trusted=True, cap_bytes=10**9)
    bag = Bag("br", obs, PUBLIC, frozenset(ids))
    assert eng.load(voy, "A", bag)
    assert eng.local_ids["Cr"] == ids
    assert eng.released["Cr"] is False

    res = eng.meet(voy, "A", 1, RNG)
    receipt = res["receipt"]
    assert receipt is not None

    cust = CustodyChain("C1", obs, "single")
    eng.transfer_custody(cust, "Cr", ids)
    # Physical handoff alone does NOT release; only the returned signed receipt.
    assert not cust.is_released("Cr", ids)
    ok = eng.return_receipt("Cr", receipt, cust, ids)
    assert ok
    assert cust.is_released("Cr", ids)
    assert eng.released["Cr"] is True


def test_tampered_receipt_is_rejected_and_copy_stays():
    w = standard_world()
    eng = w.engine
    obs = w.manifest.obs_id
    ids = {bid(obs, 1, 1)}
    voy = Voyage("Vt", "Ct", trusted=True, cap_bytes=10**9)
    bag = Bag("bt", obs, PUBLIC, frozenset(ids))
    eng.load(voy, "A", bag)
    res = eng.meet(voy, "A", 1, RNG)
    receipt = res["receipt"]
    # Forge: change byte count but keep old mac.
    forged = type(receipt)(**{**receipt.__dict__, "n_segments": 999})
    cust = CustodyChain("C2", obs, "single")
    assert eng.return_receipt("Ct", forged, cust, ids) is False
    assert eng.released["Ct"] is False


def test_single_chain_cannot_hold_matter_on_two_carriers():
    obs = "OBS"
    cust = CustodyChain("C3", obs, "single")
    cust.ship("s1", {"a"})
    with pytest.raises(ValueError):
        cust.ship("s2", {"a"})
    # Only after s1 returns a receipt and releases its local copy may the same
    # id move onto s2 (custody follows actual shipment).
    assert not cust.is_released("s1", {"a"})
    cust.return_receipt("s1", None, {"a"})
    assert cust.is_released("s1", {"a"})
    cust.ship("s2", {"a"})
    assert cust.primary == "s2"
    assert "a" not in cust.responsibility().get("s1", set())


def test_dual_route_requires_independence_and_copy_never_doubles():
    obs = "OBS"
    cust = CustodyChain("C4", obs, "dual")
    b_primary = Bag("p", obs, PUBLIC, frozenset(["a", "b"]))
    cust.ship("s1", {"a", "b"}, bag=b_primary, independent=True)
    assert cust.primary == "s1"

    # A photocopy of the same bag on a second route: allowed physically, but
    # never counted as a second committable promise.
    b_copy = Bag("p-copy", obs, PUBLIC, frozenset(["a", "b"]))
    cust.ship("s2", {"a", "b"}, bag=b_copy)
    assert "s2" in cust.duplicate_routes
    assert "s2" not in cust.committable_routes()

    # A genuinely independent bag (different content split) can be a second route.
    b_indep = Bag("i", obs, PUBLIC, frozenset(["a", "c"]))
    cust.ship("s3", {"a", "c"}, bag=b_indep, independent=True)
    assert "s3" in cust.committable_routes()
    assert set(cust.committable_routes()) == {"s1", "s3"}


# --------------------------------------------------------------------------- #
# "两个岛 ... 独立拼出 ... 收到同样字节数的两岛将有不同缺口"
# --------------------------------------------------------------------------- #


def test_same_byte_count_but_different_gaps():
    w = standard_world()
    A, B = w.islands["A"], w.islands["B"]
    obs = w.manifest.obs_id
    by = w.manifest.by_id()

    # Give A and B exactly the same number of *bytes*, but of different pieces.
    a_ids = sorted(A.missing(RNG))
    b_ids = sorted(B.missing(RNG))
    a_pick = a_ids[:2]
    b_pick = b_ids[:2]
    a_bytes = sum(len(by[bid(obs, s, t)].data) for s, t in a_pick)
    b_bytes = sum(len(by[bid(obs, s, t)].data) for s, t in b_pick)
    # pick positions whose payloads are equal length by construction
    assert a_bytes == b_bytes

    A.merge(RNG, {bid(obs, s, t): by[bid(obs, s, t)].data for s, t in a_pick})
    B.merge(RNG, {bid(obs, s, t): by[bid(obs, s, t)].data for s, t in b_pick})

    assert bytes_received(A) == bytes_received(B)
    assert A.missing(RNG) != B.missing(RNG)


def test_two_islands_settle_on_a_sufficient_voyage():
    w = standard_world()
    eng = w.engine
    obs = w.manifest.obs_id
    by = w.manifest.by_id()
    voy = Voyage("Vbig", "Cbig", trusted=True, cap_bytes=10**9)

    bag_a = Bag("ba", obs, PUBLIC,
                frozenset(bid(obs, s, t) for s in (1, 2) for t in range(3)))
    bag_b = Bag("bb", obs, PUBLIC,
                frozenset(bid(obs, s, t) for s in (0, 1, 2) for t in range(3)))
    assert eng.load(voy, "shore", bag_a)
    assert eng.load(voy, "shore", bag_b)

    ra = eng.meet(voy, "A", 1, RNG)
    rb = eng.meet(voy, "B", 2, RNG)
    assert ra["guarantee"] is not None
    assert rb["guarantee"] is not None
    # At least one clear settlement at each of the two islands.
    assert eng.settled_islands() == {"A", "B"}


# --------------------------------------------------------------------------- #
# "只变某袋与既有资料的实际重叠 ... 恢复原袋、仅给一条船新增会合 ...
#  保管责任和最终点收哪份资料应跟实际装运改变"
# --------------------------------------------------------------------------- #


def test_overlap_change_alters_the_gap():
    obs = "OBS"
    m = build_observation(obs, 3, 3)
    by = m.by_id()
    # Same calendar, two different "previously ingested" overlap sets.
    cal = Calendar("X", (SpaceTime(0, 0),))
    rng = TrackRange(0, 2, 0, 2)
    held1 = {by[bid(obs, 1, 0)].sid: by[bid(obs, 1, 0)].data}
    held2 = {by[bid(obs, 1, 1)].sid: by[bid(obs, 1, 1)].data}
    X1 = IslandStore("X1", cal, m, held=held1)
    X2 = IslandStore("X2", cal, m, held=held2)
    assert X1.missing(rng) != X2.missing(rng)
    # A bag adds the same pieces to both, but the remaining gaps still differ:
    add = {by[bid(obs, 1, 2)].sid: by[bid(obs, 1, 2)].data}
    X1.merge(rng, add)
    X2.merge(rng, add)
    assert X1.missing(rng) != X2.missing(rng)


def test_custody_and_final_settlement_follow_actual_shipment():
    w = standard_world()
    eng = w.engine
    obs = w.manifest.obs_id
    by = w.manifest.by_id()
    ids_full = frozenset(bid(obs, s, t) for s in range(3) for t in range(3))

    # Ship to A only.
    voy1 = Voyage("V1", "C1", trusted=True, cap_bytes=10**9)
    assert eng.load(voy1, "shore", Bag("full", obs, PUBLIC, ids_full))
    ra = eng.meet(voy1, "A", 1, RNG)
    assert ra["guarantee"] is not None
    assert eng.settled_islands() == {"A"}

    cust = CustodyChain("C", obs, "single")
    eng.transfer_custody(cust, "C1", set(ids_full))
    assert cust.primary == "C1"
    assert eng.local_ids["C1"] == set(ids_full)

    # Now a second route is granted to reach B; responsiveness changes with
    # actual shipment, and local copy on C1 is only released after its receipt
    # comes back.
    eng.return_receipt("C1", ra["receipt"], cust, set(ids_full))
    assert cust.is_released("C1", set(ids_full))

    voy2 = Voyage("V2", "C2", trusted=True, cap_bytes=10**9)
    eng.load(voy2, "shore", Bag("full2", obs, PUBLIC, ids_full))
    eng.transfer_custody(cust, "C2", set(ids_full))
    rb = eng.meet(voy2, "B", 1, RNG)
    assert rb["guarantee"] is not None
    assert eng.settled_islands() == {"A", "B"}
    # Custody responsibility now rests with C2, not C1.
    assert cust.responsibility().get("C2") == set(ids_full)


# --------------------------------------------------------------------------- #
# Ledger integrity
# --------------------------------------------------------------------------- #


def test_ledger_chain_verifies_and_detects_tamper():
    w = standard_world()
    eng = w.engine
    obs = w.manifest.obs_id
    voy = Voyage("VL", "CL", trusted=True, cap_bytes=10**9)
    eng.load(voy, "shore", Bag("bl", obs, PUBLIC,
                               frozenset([bid(obs, 1, 1)])))
    assert eng.ledger.verify_chain()
    # Tamper with an event field.
    eng.ledger.events[0]["kind"] = "forged"
    assert eng.ledger.verify_chain() is False


def test_signing_a_bag_is_not_the_same_as_a_useful_new_piece():
    """A bag can be receipted yet add no new openable piece.

    Here island A already holds every delivered segment (a duplicate delivery),
    so the carrier legitimately signs a receipt for the physical exchange, but
    the merge desk still reports no guarantee -- the island gained nothing new.
    """
    w = standard_world()
    eng = w.engine
    A = w.islands["A"]
    obs = w.manifest.obs_id
    by = w.manifest.by_id()

    # First genuinely useful delivery for A completes its window.
    voy = Voyage("V1", "C1", trusted=True, cap_bytes=10**9)
    full = Bag("full", obs, PUBLIC,
               frozenset(bid(obs, s, t) for s in range(3) for t in range(3)))
    assert eng.load(voy, "shore", full)
    r1 = eng.meet(voy, "A", 1, RNG)
    assert r1["receipt"] is not None
    assert r1["guarantee"] is not None

    # Second delivery of the same pieces: exchanged, receipted, but no new piece.
    voy2 = Voyage("V2", "C2", trusted=True, cap_bytes=10**9)
    assert eng.load(voy2, "shore", Bag("full2", obs, PUBLIC, full.segment_ids))
    r2 = eng.meet(voy2, "A", 2, RNG)
    assert r2["receipt"] is not None          # signed for the exchange
    assert r2["delivered"]                     # files did physically move
    assert r2["useful"] is False               # but nothing new became covered
    assert r2["new_pieces"] == []
