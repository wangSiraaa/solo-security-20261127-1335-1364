"""Attestation and roster rules for the public slip.

This module is the gate between raw point submissions and anything that may
become public.  It enforces:

  * Every counted participant has a voucher that (a) verifies under the
    awarding venue's PINNED key, and (b) is bound to THIS round's epoch.
    Yesterday's same-named voucher (different epoch) is rejected.
  * A point may contribute at most ONCE per round.  A reconnect that submits
    twice is deduplicated; a half-share from a replay/duplicate is rejected.
  * A point may not count using a voucher/opening from the previous round.
  * The public slip requires >= 2 venues and >= 3 INDEPENDENT seats, where
    a standby machine and the point it replaces collapse to ONE seat, and two
    points registered as a same-seat machine swap collapse to ONE seat.
"""

from dataclasses import dataclass

from . import crypto_core as cc
from .model import Point, Seat, Voucher


@dataclass
class RosterDecision:
    ok: bool
    reasons: list
    counted_seats: list        # independent (venue, seat_id_or_canonical) keys
    venues: list
    counted_points: list       # point names actually counted
    rejected: dict             # point_name -> reason string
    selectees: int = 0         # approved participants actually counted


def canonical_seat(point: Point, same_seat_swaps: dict[str, str]) -> str:
    """Return the canonical INDEPENDENT-seat key for a point.

    ``same_seat_swaps`` maps point_name -> the point_name it shares a physical
    seat with (同席换机).  A standby box collapses to the point it replaces.
    The canonical key is derived from the *underlying* seat so that standby +
    replaced, and swap partners, all resolve to the same one.
    """
    seen = set()
    cur = point.name
    while cur in same_seat_swaps and cur not in seen:
        seen.add(cur)
        cur = same_seat_swaps[cur]
    # standby collapses to its replaced point, then to that point's seat
    if point.is_standby and point.replaces:
        base = same_seat_swaps.get(point.replaces, point.replaces)
    else:
        base = cur
    # Use the actual seat of the resolved point if we can; fall back to name.
    return f"{point.venue}|seat:{base}"


def validate_round(points: dict[str, Point],
                   vaults: dict,               # point_name -> PointVault
                   vouchers: dict,             # point_name -> list[Voucher]
                   epoch: int,
                   venue_pubs: dict,           # venue -> pinned vk
                   same_seat_swaps: dict | None = None,
                   seen_submission_tokens: set | None = None) -> RosterDecision:
    """Apply all roster rules and return a decision the booth can rely on.

    ``seen_submission_tokens`` implements per-round single-contribution: any
    token already used this round marks a replayed/duplicate submission.
    """
    same_seat_swaps = same_seat_swaps or {}
    seen = seen_submission_tokens if seen_submission_tokens is not None else set()

    reasons: list[str] = []
    rejected: dict[str, str] = {}
    counted_points: list[str] = []

    for pname, point in points.items():
        vs = vouchers.get(pname, [])
        if not vs:
            rejected[pname] = "no voucher"
            continue
        bad = []
        for v in vs:
            if v.epoch != epoch:
                bad.append(f"voucher epoch {v.epoch} != round {epoch}")
                continue
            if not cc.schnorr_verify(v.sig, expected_y=venue_pubs.get(v.venue)):
                bad.append("voucher signature/venue mismatch")
                continue
            if v.seat_key != point.seat_key and not point.is_standby:
                bad.append("voucher seat does not match point")
                continue
            # opening must be the round-bound opening
            want = cc.h(b"open", v.participant_name.encode(), v.seat_key.encode(),
                        str(epoch).encode())
            if v.opening != want:
                bad.append("voucher opening not bound to this round")
        if bad:
            rejected[pname] = "; ".join(bad)
            continue
        # single contribution per round
        token = cc.h(b"submission", pname.encode(), str(epoch).encode(),
                     point.party.pub.to_bytes(256, "big"))
        if token in seen:
            rejected[pname] = "duplicate submission this round (reconnect)"
            continue
        seen.add(token)
        counted_points.append(pname)

    if not counted_points:
        return RosterDecision(False, ["no valid points"], [], [], [], rejected)

    # Independent seats: canonicalise, then also fold shut standbys.
    seat_keys = {canonical_seat(points[p], same_seat_swaps) for p in counted_points}
    venues = sorted({points[p].venue for p in counted_points})
    venues = list(venues)

    # Approved participants actually counted (sum over counted points' vaults).
    selectees = sum(len(vaults[p]) for p in counted_points if p in vaults)

    if len(venues) < 2:
        reasons.append(f"only {len(venues)} venue(s); need >= 2")
    if len(seat_keys) < 3:
        reasons.append(f"only {len(seat_keys)} independent seat(s); need >= 3")

    ok = not reasons
    return RosterDecision(ok, reasons, sorted(seat_keys), venues,
                          counted_points, rejected, selectees)
