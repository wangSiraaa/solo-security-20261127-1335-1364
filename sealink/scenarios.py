"""End-to-end scenario mirroring the task narrative.

Run with:  python3 demo.py
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .engine import HandoffEngine, Ledger, Voyage
from .guarantee import IslandStore
from .model import (
    PUBLIC,
    SEALED,
    Bag,
    Calendar,
    CustodyChain,
    SpaceTime,
    TrackRange,
    build_observation,
)


MIN_WINDOW = TrackRange(0, 2, 0, 2)


def bid(obs, seg, t):
    return f"{obs}:s{seg}:t{t}"


@dataclass
class World:
    manifest: object
    islands: dict
    engine: object


def standard_world(obs_id: str = "OBS", seg: int = 3, t: int = 3) -> World:
    """Two islands A/B that need different pieces for the same window."""
    m = build_observation(obs_id, seg, t)
    by = m.by_id()
    a_cal = Calendar("A", tuple(SpaceTime(0, tt) for tt in range(t)))
    A = IslandStore("A", a_cal, m, held={
        bid(obs_id, 1, 0): by[bid(obs_id, 1, 0)].data,
    })
    b_cal = Calendar("B", (SpaceTime(0, 1),))
    B = IslandStore("B", b_cal, m, held={
        bid(obs_id, 1, 1): by[bid(obs_id, 1, 1)].data,
        bid(obs_id, 2, 0): by[bid(obs_id, 2, 0)].data,
    })
    engine = HandoffEngine(m, {"A": A, "B": B}, None, Ledger())
    return World(manifest=m, islands={"A": A, "B": B}, engine=engine)


def run_demo(verbosity: bool = True) -> dict:
    obs = "OBS"
    w = standard_world(obs)
    m, eng = w.manifest, w.engine
    by = m.by_id()
    A, B = w.islands["A"], w.islands["B"]

    def say(*a):
        if verbosity:
            print(*a)

    say("=" * 72)
    say("SHORE STATION PRE-LOADS THREE VESSELS WITH ITS OWN SEALED BAGS")
    say("=" * 72)

    # Shore station's sealed bags. Each is unsealable only by its designated island.
    # Bytes shipped are bounded by the port per vessel.
    segs_for_A = frozenset(bid(obs, s, t) for s in (1, 2) for t in range(3))
    segs_for_B = frozenset(bid(obs, s, t) for s in range(3) for t in range(3))
    bag_A = Bag("bagA", obs, SEALED, segs_for_A, sealed_to="A")
    bag_B = Bag("bagB", obs, SEALED, segs_for_B, sealed_to="B")

    # V1 = trusted vessel with public capability (may carry unsealed as well).
    # V2 = ordinary carrier (sealed only). V3 = ordinary carrier (sealed only).
    v1 = Voyage("V1", "C1", trusted=True, cap_bytes=10**9)
    v2 = Voyage("V2", "C2", trusted=False, cap_bytes=eng.bag_bytes(bag_B) + 1)
    v3 = Voyage("V3", "C3", trusted=False, cap_bytes=eng.bag_bytes(bag_A) + 1)

    say(f"V1 cap={v1.cap_bytes}; V2 cap={v2.cap_bytes}; V3 cap={v3.cap_bytes}")

    # Ordinary carrier refuses a public bag.
    refused = eng.load(v2, "shore", Bag("pub", obs, PUBLIC, frozenset([bid(obs, 0, 0)])))
    say(f"ordinary carrier V2 accepted a public bag? {refused} (expected False)")
    assert refused is False

    # Ordinary carriers carry only sealed bags.
    assert eng.load(v2, "shore", bag_B)
    assert eng.load(v3, "shore", bag_A)
    # Trusted V1 carries the full public copy and the sealed-for-A bag.
    assert eng.load(v1, "shore", Bag("pubfull", obs, PUBLIC, segs_for_B))

    say("\nPORT WITHDRAWS ONE AGREED CONTACT (V1 x B, cycle 1)")
    eng.port.drop_contact("V1", "B", 1)

    say("\n" + "=" * 72)
    say("ISLAND A MERGE DESK: reassemble window from own calendar + held + received")
    say("=" * 72)
    say(f"A own calendar covers: {sorted({s.as_tuple() for s in A.calendar.steps})}")
    say(f"A previously held overlap: {sorted(A.held)}")
    say(f"A missing before shipments: {sorted(A.missing(MIN_WINDOW))}")

    ra = eng.meet(v1, "A", 1, MIN_WINDOW)
    say(f"V1<->A exchanged {len(ra['delivered'])} pieces; guarantee="
        f"{ra['guarantee'] is not None}")
    if ra["guarantee"]:
        say(f"A window root = {ra['guarantee'].root[:16]}... "
            f"openings={len(ra['guarantee'].openings)}")
    # V3 (sealed for A) also reaches A on a later cycle.
    ra3 = eng.meet(v3, "A", 2, MIN_WINDOW)
    say(f"V3<->A guarantee={ra3['guarantee'] is not None}; "
        f"receipt={ra3['receipt'].receipt_id if ra3['receipt'] else None}")

    say("\nISLAND B MERGE DESK")
    say(f"B own calendar covers: {sorted({s.as_tuple() for s in B.calendar.steps})}")
    say(f"B previously held overlap: {sorted(B.held)}")
    say(f"B missing before shipments: {sorted(B.missing(MIN_WINDOW))}")

    # V1 x B cycle 1 was withdrawn by the port -> allowed miss, no receipt.
    miss = eng.meet(v1, "B", 1, MIN_WINDOW)
    say(f"V1<->B cycle1 contact={miss['contact']} reason={miss['reason']} "
        f"receipt={miss['receipt']}")
    # V2 (sealed for B) arrives and B completes.
    rb = eng.meet(v2, "B", 2, MIN_WINDOW)
    say(f"V2<->B exchanged={len(rb['delivered'])} guarantee="
        f"{rb['guarantee'] is not None}")
    if rb["guarantee"]:
        say(f"B window root = {rb['guarantee'].root[:16]}...")

    say("\n" + "=" * 72)
    say("DIFFERENT GAPS FROM SAME BYTE COUNT (same #bytes, different pieces)")
    say("=" * 72)
    obs2 = "OBS2"
    w2 = standard_world(obs2)
    A2, B2 = w2.islands["A"], w2.islands["B"]
    by2 = w2.manifest.by_id()
    ap = sorted(A2.missing(MIN_WINDOW))[:2]
    bp = sorted(B2.missing(MIN_WINDOW))[:2]
    A2.merge(MIN_WINDOW, {bid(obs2, *p): by2[bid(obs2, *p)].data for p in ap})
    B2.merge(MIN_WINDOW, {bid(obs2, *p): by2[bid(obs2, *p)].data for p in bp})
    say(f"A2 received bytes={sum(len(by2[bid(obs2,*p)].data) for p in ap)}; "
        f"missing={sorted(A2.missing(MIN_WINDOW))}")
    say(f"B2 received bytes={sum(len(by2[bid(obs2,*p)].data) for p in bp)}; "
        f"missing={sorted(B2.missing(MIN_WINDOW))}")

    say("\n" + "=" * 72)
    say("CUSTODY: shipment-driven; receipt-triggered local release")
    say("=" * 72)
    cust = CustodyChain("C", obs, "single")
    eng.transfer_custody(cust, "C1", segs_for_B)
    say(f"after shipment responsibility={ {k: len(v) for k,v in cust.responsibility().items()} }")
    assert not cust.is_released("C1", segs_for_B)
    eng.return_receipt("C1", ra["receipt"], cust, set(segs_for_B))
    say(f"C1 local copy released after returned receipt? "
        f"{cust.is_released('C1', segs_for_B)}")

    # Photocopy of a bag cannot double the promise.
    cust2 = CustodyChain("C2", obs, "dual")
    cust2.ship("D1", set(segs_for_B), bag=bag_B, independent=True)
    cust2.ship("D2", set(segs_for_B), bag=Bag("bagB-copy", obs, SEALED, segs_for_B,
                                              sealed_to="B"))
    say(f"photocopy route counted as committable? "
        f"{'D2' in cust2.committable_routes()} (expected False)")

    say("\n" + "=" * 72)
    say("FINAL STATE")
    say("=" * 72)
    say(f"A independently opens window? {A.can_open(MIN_WINDOW)}")
    say(f"B independently opens window? {B.can_open(MIN_WINDOW)}")
    say(f"islands settled: {sorted(eng.settled_islands())}")
    say(f"ledger events={len(eng.ledger.events)} chain-verifies="
        f"{eng.ledger.verify_chain()}")

    return {
        "settled": sorted(eng.settled_islands()),
        "A_open": A.can_open(MIN_WINDOW),
        "B_open": B.can_open(MIN_WINDOW),
        "events": len(eng.ledger.events),
    }


if __name__ == "__main__":
    result = run_demo()
    print(json.dumps(result, indent=2))
