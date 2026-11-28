"""On-disk demonstration: shore station, three vessels, two islands.

Everything travels as small files; actors read the directories back after being
"offline". The demo mirrors the task's narrative beats.
"""

from __future__ import annotations

from .fsstore import FsWorld
from .model import (
    PUBLIC,
    SEALED,
    Bag,
    TrackRange,
    build_observation,
)


OBS = "OBS"
WINDOW = TrackRange(0, 2, 0, 2)


def _bid(s, t):
    return f"{OBS}:s{s}:t{t}"


def run_fs_demo(root: str) -> dict:
    m = build_observation(OBS, 3, 3)
    w = FsWorld(root)

    print(f"\nworld at {root}")
    print("\n[shore] publishes bags as directories of small .seg files")
    bag_full = Bag("bagfull", OBS, PUBLIC,
                   frozenset(_bid(s, t) for s in range(3) for t in range(3)))
    bag_A = Bag("bagA", OBS, SEALED,
                frozenset(_bid(s, t) for s in (1, 2) for t in range(3)),
                sealed_to="A")
    bag_B = Bag("bagB", OBS, SEALED,
                frozenset(_bid(s, t) for s in range(3) for t in range(3)),
                sealed_to="B")
    for b in (bag_full, bag_A, bag_B):
        w.publish_bag(m, b)

    print("\n[islands] own track calendars + previously ingested overlaps")
    w.set_island_calendar("A", [(0, 0), (0, 1), (0, 2)])
    w.set_island_calendar("B", [(0, 1)])
    w.seed_island_held("A", m, [_bid(1, 0)])
    w.seed_island_held("B", m, [_bid(1, 1), _bid(2, 0)])
    print("  A calendar=seg0 all; held=OBS:s1:t0")
    print("  B calendar=seg0 t1; held=OBS:s1:t1, OBS:s2:t0")

    print("\n[port] caps bytes per vessel and withdraws one contact")
    w.set_cap("V1", 10**6)
    w.set_cap("V2", 10**6)
    w.set_cap("V3", 10**6)
    w.set_cap("V4", 10**6)

    print("\n[shore -> vessels] ordinary carriers only take sealed bags")
    print("  load public 'bagfull' onto ordinary V2:",
          w.load("V2", "C2", "bagfull", ordinary=True, manifest=m))
    print("  load sealed-for-A 'bagA' onto ordinary V3:",
          w.load("V3", "C3", "bagA", ordinary=True, manifest=m)["ok"])
    print("  load sealed-for-B 'bagB' onto ordinary V4:",
          w.load("V4", "C4", "bagB", ordinary=True, manifest=m)["ok"])
    print("  load public 'bagfull' onto trusted V1:",
          w.load("V1", "C1", "bagfull", ordinary=False, manifest=m)["ok"])

    print("\n[vessels go offline; later reopen directory to resume]")
    print("  V4 manifest entries:", [e["kind"] for e in w.ship_manifest("C4")["entries"]])

    print("\n[port] withdraws V1 x B cycle 2")
    w.drop_contact("V1", "B", 2)

    print("\n[meet] V1 at island A")
    rA = w.meet("V1", "C1", "A", 1, m, WINDOW)
    print(f"  delivered {len(rA['delivered'])} pieces, reason={rA['reason']}, "
          f"receipt={rA['receipt'].receipt_id if rA['receipt'] else None}")
    gA = w.merge("A", m, WINDOW)
    print(f"  island A independent merge: {gA['file'].split('/')[-1]} "
          f"root={gA['root'][:12]}...")

    print("\n[meet] V4 at island B (V1's contact to B is withdrawn -> allowed miss)")
    miss = w.meet("V1", "C1", "B", 2, m, WINDOW)
    print(f"  withdrawn contact: reason={miss['reason']}, receipt={miss['receipt']}")
    rB = w.meet("V4", "C4", "B", 1, m, WINDOW)
    print(f"  V4->B delivered {len(rB['delivered'])} pieces, "
          f"new={len(rB['new_pieces'])} useful={rB['useful']} "
          f"receipt={rB['receipt'].receipt_id}")
    gB = w.merge("B", m, WINDOW)
    print(f"  island B independent merge: {gB['file'].split('/')[-1]} "
          f"root={gB['root'][:12]}...")

    print("\n[receipt] V4 brings its signed receipt back; only now is its copy released")
    print("  before:", [b["bag_id"] for b in w.ship_bags("C4")])
    out = w.return_receipt("C4", rB["receipt"].receipt_id)
    print("  release:", out)
    print("  after:", [b["bag_id"] for b in w.ship_bags("C4")])

    print("\n[state] both islands produced independently-openable whole observations")
    print("  A merged:", gA is not None, " B merged:", gB is not None)

    return {"A": gA is not None, "B": gB is not None,
            "released": out.get("ok")}
