"""End-to-end narrated demo of the read-aloud licensing desk and two players.

Run:  python3 -m licensing.demo

The desk is the local issue counter. player-A is the traveller on the train;
player-B is the waiting borrower. They share no state except the desk.

Policy shown: an offline, chapter-limited, LIMITED-COUNT capability consumed on
a non-rollback player counter; the player settles by presenting a sealed,
device-signed return proof when it reconnects.

Scenes (matching the exercise):
  1. Normal chapter claim + pre-fetch.
  2. Disconnect in the tunnel; pause and resume from the middle (free in-window).
  3. Per-segment disconnect: only issued segments resume; later ones MISSING.
  4. Offline return: honest PENDING ("to be checked") and LIMITED receipts.
  5. Waiting borrower B consumes what was returned.
  6. Receipt swap: an instance with issued valid segments has ONLY its return
     receipt retargeted to another chapter -> refused, no early reclaim.
  7. Restore the valid receipt -> the reclaim stands; then change the waiting
     borrower to a front-preview-only reader -> reclaim stands but the whole
     chapter cannot be lent to them.
  8. Transfer ("转机"): carry remaining credits to another machine, only after
     sealing the source.
  9. Double-spend guard: never grant a credit while an old device could open.

Every tick is simulated; every signature is a toy HMAC. No real keys.
"""
from __future__ import annotations

from .clock import SimClock
from .desk import Desk
from .model import LeaseScope, ReturnProof
from .player import Player
from .reader import ReaderCapability


def banner(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def flush(*players) -> None:
    for p in players:
        for line in p.lines():
            print("   " + line)


def show_pool(desk: Desk, label: str) -> None:
    st = desk.pool_status()
    cells = ", ".join(
        f"{ch}:{v['available']}/{v['total']} free" for ch, v in st.items()
    )
    print(f"   >> pool [{label}]: {cells}")


def show_receipt(res) -> None:
    r = res.receipt
    if r is None:
        print(f"   receipt: (none) -- {res.message}")
        return
    print(
        f"   receipt {r.receipt_id} [{r.status.value}]: "
        f"consumed {r.plays_consumed}/{r.plays_total}, "
        f"returned {r.credits_returned} -- {r.note}"
    )


def main() -> None:
    clock = SimClock(0)
    desk = Desk(clock, {"ch1": 8, "ch2": 8})
    A = Player("player-A", clock)   # traveller on the train
    B = Player("player-B", clock)   # waiting borrower
    A.online = B.online = True
    reader_a = ReaderCapability(A, desk)
    reader_b = ReaderCapability(B, desk)

    # --------------------------------------------------------------- Scene 1
    banner("SCENE 1  Normal claim: A takes a limited-count lease for ch1")
    show_pool(desk, "start")
    res = desk.issue_lease("ch1", "player-A", "traveller", plays=4,
                           scope=LeaseScope.FULL, ttl=3600)
    print(f"   desk: {res.message}")
    lease_a = res.lease
    A.bind(lease_a)
    show_pool(desk, "after issue (4 reserved to A)")
    print("   -- A pre-fetches the cacheable content while still connected --")
    A.prefetch(lease_a.lease_id)
    print("   -- then plays through the reader's play() interface --")
    reader_a.play(lease_a.lease_id, "ch1.s1")
    flush(A)

    # --------------------------------------------------------------- Scene 2
    banner("SCENE 2  Disconnect in the tunnel; pause/resume mid-segment")
    A.online = False
    print("   (A goes offline; the tram enters the tunnel)")
    clock.advance(30)
    reader_a.pause(lease_a.lease_id)
    clock.advance(120)                         # within RESUME_WINDOW (300 s)
    reader_a.play(lease_a.lease_id)            # resume -> free, delivers line
    flush(A)
    print("   -- preview-only ch1.s0 cannot be cached or played offline --")
    reader_a.play(lease_a.lease_id, "ch1.s0")
    flush(A)

    # --------------------------------------------------------------- Scene 3
    banner("SCENE 3  Per-segment disconnect: only issued segments resume")
    res2 = desk.issue_lease("ch2", "player-A", "traveller", plays=6,
                            scope=LeaseScope.FULL, ttl=3600)
    print(f"   desk: {res2.message}")
    lease_a2 = res2.lease
    A.bind(lease_a2)
    A.online = True
    got = A.receive_until(lease_a2.lease_id, 2)      # only s1, s2 arrive
    print(f"   briefly online: A received only {got}; the rest were never issued")
    A.online = False
    print("   (disconnect again)")
    reader_a.play(lease_a2.lease_id, "ch2.s1")
    reader_a.play(lease_a2.lease_id, "ch2.s2")
    reader_a.play(lease_a2.lease_id, "ch2.s3")       # not issued -> refused
    miss = A.narrate_missing(lease_a2.lease_id)
    flush(A)
    print(f"   honest gaps on A's screen: {miss}")
    show_pool(desk, "after A's partial ch2 lease")

    # --------------------------------------------------------------- Scene 4
    banner("SCENE 4  Offline return: PENDING (to check) then LIMITED")
    A.seal(lease_a2.lease_id)
    print("   -- first, no counter shown: desk issues an honest PENDING receipt --")
    pending = reader_a.return_(lease_a2.lease_id, show_counter=False)
    print(f"   desk: {pending.message}")
    show_receipt(pending)
    show_pool(desk, "after PENDING (nothing reclaimed)")

    print("   -- now A shows its own sealed counter: LIMITED provisional --")
    limited = reader_a.return_(lease_a2.lease_id, show_counter=True)
    print(f"   desk: {limited.message}")
    show_receipt(limited)
    show_pool(desk, "after LIMITED (provisional release)")

    # --------------------------------------------------------------- Scene 5
    banner("SCENE 5  Waiting borrower B consumes what was returned (ch1)")
    A.online = True
    settled_a = reader_a.return_(lease_a.lease_id)
    print(f"   desk: {settled_a.message}")
    show_receipt(settled_a)
    show_pool(desk, "after A settles ch1 lease")
    flush(A)

    res_b = desk.issue_lease("ch1", "player-B", "waiting-borrower",
                             plays=3, scope=LeaseScope.FULL, ttl=3600)
    print(f"   desk: {res_b.message}")
    B.bind(res_b.lease)
    B.prefetch(res_b.lease.lease_id)
    reader_b.play(res_b.lease.lease_id, "ch1.s1")
    reader_b.play(res_b.lease.lease_id, "ch1.s2")
    flush(B)
    show_pool(desk, "after B consumes 2")

    # --------------------------------------------------------------- Scene 6
    banner("SCENE 6  Receipt swap: one instance, ONLY the receipt is retargeted")
    print("   B's ch1 lease still holds issued, valid segments (s3..s5 cached).")
    B.seal(res_b.lease.lease_id)
    real = B.return_proof(res_b.lease.lease_id)
    before = desk.pool_status()["ch1"]["available"]
    swapped = ReturnProof(**{**real.__dict__, "chapter_id": "ch2"})
    print("   -- retarget the return receipt from ch1 to ch2 and present it --")
    bad = desk.settle_return(swapped)
    print(f"   desk: {bad.message}")
    after = desk.pool_status()["ch1"]["available"]
    print(f"   ch1 available: {before} -> {after}  (early reclaim refused)")
    show_pool(desk, "after refused swap (ch1 NOT reclaimed early)")

    # --------------------------------------------------------------- Scene 7
    banner("SCENE 7  Restore valid receipt -> reclaim stands; then front-only borrower")
    good = desk.settle_return(real)
    print(f"   desk: {good.message}")
    show_receipt(good)
    show_pool(desk, "after valid ch1 return (reclaim stands)")
    print("   -- now the waiting borrower is only allowed to preview the front --")
    prev = desk.issue_lease("ch1", "player-B", "front-preview-borrower",
                            plays=0, scope=LeaseScope.FRONT_PREVIEW, ttl=3600)
    print(f"   desk: {prev.message}  (scope={prev.lease.scope.value}, plays={prev.lease.plays_total})")
    B.bind(prev.lease)
    B.online = True
    print("   -- they may sample the live front taster: --")
    reader_b.play(prev.lease.lease_id, "ch1.s0")
    print("   -- but they may NOT take any normal segment of the whole chapter: --")
    reader_b.play(prev.lease.lease_id, "ch1.s1")
    B.receive(prev.lease.lease_id, "ch1.s1")
    flush(B)
    print("   => reclaim succeeded, yet the whole chapter cannot be lent to them.")

    # --------------------------------------------------------------- Scene 8
    banner("SCENE 8  Transfer ('转机'): carry remaining credits to another machine")
    res_c = desk.issue_lease("ch2", "player-A", "traveller", plays=4,
                             scope=LeaseScope.FULL, ttl=3600)
    C = Player("player-C", clock)
    C.online = True
    A.bind(res_c.lease)
    A.online = True
    A.receive_until(res_c.lease.lease_id, 5)
    reader_a.play(res_c.lease.lease_id, "ch2.s1")
    print(f"   A opened 1 of 4 on {res_c.lease.lease_id}; wants to hand the rest to C")
    moved = reader_a.transfer(res_c.lease.lease_id,
                              to_device_id="player-C",
                              new_borrower_id="transfer-recipient", ttl=3600)
    print(f"   desk: {moved.message}")
    show_receipt(moved)
    C.bind(moved.lease)
    C.prefetch(moved.lease.lease_id)
    reader_c = ReaderCapability(C, desk)
    reader_c.play(moved.lease.lease_id, "ch2.s2")
    flush(C)
    show_pool(desk, "after transfer (consumed stays spent, remainder carried)")

    # --------------------------------------------------------------- Scene 9
    banner("SCENE 9  Double-spend guard: no credit while an old device can open")
    avail = desk.pool_status()["ch2"]["available"]
    D = Player("player-D", clock)
    D.online = True
    grab = desk.issue_lease("ch2", "player-D", "greedy", plays=avail,
                            scope=LeaseScope.FULL, ttl=3600)
    print(f"   desk: issued {avail} to D -> {grab.message}")
    over = desk.issue_lease("ch2", "player-D", "greedy", plays=1,
                            scope=LeaseScope.FULL, ttl=3600)
    print(f"   desk: ask for 1 more -> {over.message}")
    show_pool(desk, "after double-spend attempt")

    banner("DESK AUDIT LOG")
    for line in desk.audit():
        print("   " + line)

    print("\nDone. All counters simulated; all signatures are toy HMACs.")


if __name__ == "__main__":
    main()
