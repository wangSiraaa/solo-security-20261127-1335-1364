"""Agreed external interfaces + the joint booth.

The coordinator may only use these handles:
  * SubmitInterface (one per point): the point builds protocol material
    (sealed shares, or a masked number) from its PRIVATE vault.  The interface
    returns ONLY protocol material -- never a score.
  * BoothInterface: attempt_publish(scheme_result, roster_decision) and
    query_receipt(point_name).

The Booth never receives a plaintext score.  It receives:
  * scheme1: two signed Partials whose covered sets must match the counted
    roster, or
  * scheme2: a list of masked submissions plus the room to check cancellation.
It combines to a total, checks roster rules, and either signs a public slip
(>= 2 venues, >= 3 independent seats) or refuses.  It always issues per-point
receipts for the points that were actually counted.
"""

from dataclasses import dataclass

from . import crypto_core as cc
from .attest import RosterDecision
from .model import Point, Receipt
from .vault import PointVault


@dataclass
class PublicSlip:
    epoch: int
    total: int
    venues: list
    seats: list
    slots: int            # number of independent seats
    counted_points: list
    selectees: int        # approved participants actually counted
    sig: dict

    def verify(self, booth_pub: int) -> bool:
        msg = _slip_msg(self)
        return cc.schnorr_verify(self.sig, expected_y=booth_pub) and self.sig["msg"] == msg


def _slip_msg(slip) -> bytes:
    return cc.h(b"public-slip", str(slip.epoch).encode(), cc.i2b16(slip.total),
                b",".join(v.encode() for v in slip.venues),
                b",".join(s.encode() for s in slip.seats),
                str(slip.slots).encode(), str(slip.selectees).encode())


class SubmitInterface:
    """Per-point protocol interface.  Wraps a private vault; exposes only
    scheme-shaped material."""

    def __init__(self, point: Point, vault: PointVault):
        self.point = point
        self._vault = vault

    # --- scheme 1: split into two sealed shares ------------------------------
    def make_shares(self, epoch: int, custA_pub: int, custB_pub: int):
        from .scheme1 import make_share_pair
        return make_share_pair(self._vault, self.point, epoch, custA_pub, custB_pub)

    # --- scheme 2: masked number --------------------------------------------
    def make_masked(self, epoch: int, roster_pubs: dict, roster_names: list):
        from .scheme2 import PointMasquer
        masq = PointMasquer(self.point, roster_pubs, epoch, roster_names)
        return masq.build(self._vault)

    def __repr__(self):
        return f"SubmitInterface({self.point.name})"


@dataclass
class PublishOutcome:
    published: bool
    slip: "PublicSlip | None"
    reason: str
    receipts: dict             # point_name -> Receipt
    computed_total: int | None = None   # arithmetic result even if not publishable


class Booth:
    def __init__(self, party: cc.Party):
        self.party = party
        self._receipts: dict[tuple, Receipt] = {}  # (epoch, point) -> receipt

    @property
    def pub(self) -> int:
        return self.party.pub

    # --- receipts ---------------------------------------------------------
    def _issue_receipt(self, point_name, seat_key, epoch, counted, published,
                       reason, digest) -> Receipt:
        r = Receipt(point_name, seat_key, epoch, counted, published, reason, digest, {})
        r.sig = cc.schnorr_sign(self.party, r.message(), b"receipt-dom")
        self._receipts[(epoch, point_name)] = r
        return r

    def query_receipt(self, epoch: int, point_name: str) -> Receipt | None:
        return self._receipts.get((epoch, point_name))

    # --- publication ------------------------------------------------------
    def attempt_publish_scheme1(self, epoch, partials, roster: RosterDecision,
                                points: dict[str, Point],
                                custodian_pubs: dict | None = None) -> PublishOutcome:
        """Scheme 1: require BOTH custodians' partials, over the SAME counted
        set, matching the roster.  If one desk is offline it will be missing
        or cover a different set -> refuse (no 'half' publication).

        Each partial must verify under its custodian's PINNED key; a forged or
        attacker-minted partial is rejected outright.
        """
        receipts = {}
        if len(partials) != 2:
            return self._refuse(epoch, roster, points,
                                "incomplete: fewer than two custodian partials (one desk offline?)")
        A, B = partials
        if A.epoch != epoch or B.epoch != epoch:
            return self._refuse(epoch, roster, points, "partial epoch mismatch")
        # Signature check under pinned custodian keys.
        for part in (A, B):
            pub = (custodian_pubs or {}).get(part.label)
            if pub is None or not part.verify_pinned(pub):
                return self._refuse(epoch, roster, points,
                                    f"partial from {part.label} fails pinned-key verification")
        covered_A = set(A.covered)
        covered_B = set(B.covered)
        expected = set(roster.counted_points)
        if covered_A != expected or covered_B != expected:
            return self._refuse(
                epoch, roster, points,
                f"custodian batches differ from counted roster "
                f"(A covers {len(covered_A)}, B covers {len(covered_B)}, "
                f"roster {len(expected)}); cannot silently drop a point")

        total = cc.fadd(A.total, B.total)
        return self._finalize(epoch, total, roster, points, receipts)

    def attempt_publish_scheme2(self, epoch, submissions, masqers,
                                roster: RosterDecision, points: dict[str, Point]) -> PublishOutcome:
        """Scheme 2: require that the submitted set is the FULL agreed roster
        and that masks cancel.  If someone left early after half submitted,
        the set != roster and masks won't cancel -> refuse; a new round is
        needed, NOT a silent roster trim."""
        from .scheme2 import combine_masked, masks_cancel
        receipts = {}
        if not submissions:
            return self._refuse(epoch, roster, points, "no masked submissions")
        if not masks_cancel(submissions, masqers):
            names = sorted(s.point_name for s in submissions)
            roster_names = sorted(masqers[next(iter(masqers))].roster_names)
            missing = sorted(set(roster_names) - set(names))
            return self._refuse(
                epoch, roster, points,
                f"masks do not cancel: submitted {len(names)} of {len(roster_names)} "
                f"roster points (missing {missing}); start a NEW round, "
                f"do not trim the roster")
        total = combine_masked(submissions)
        return self._finalize(epoch, total, roster, points, receipts)

    def _refuse(self, epoch, roster, points, reason, computed_total=None) -> PublishOutcome:
        # A refused round still issues honest per-point receipts: a point that
        # was accepted into the batch learns counted=True, published=False.
        receipts = {}
        for pname, p in points.items():
            counted = pname in roster.counted_points
            dig = cc.h(b"contrib", pname.encode(), str(epoch).encode()) if counted \
                else cc.h(b"contrib-refused", pname.encode(), str(epoch).encode())
            why = roster.rejected.get(pname, reason) if not counted else reason
            receipts[pname] = self._issue_receipt(
                pname, p.seat_key, epoch, counted, False, why, dig)
        return PublishOutcome(False, None, reason, receipts, computed_total)

    def _finalize(self, epoch, total, roster, points, receipts) -> PublishOutcome:
        # No collection => no publication, even if arithmetic looked fine.
        if not roster.ok:
            # The arithmetic may be perfectly correct; we still must not sign a
            # slip that overstates venues/seats.  Report the computed total for
            # audit, but publish nothing.
            return self._refuse(epoch, roster, points,
                                "roster rule failed: " + "; ".join(roster.reasons),
                                computed_total=total)
        slip = PublicSlip(epoch, total, roster.venues, roster.counted_seats,
                          len(roster.counted_seats), roster.counted_points,
                          roster.selectees, {})
        slip.sig = cc.schnorr_sign(self.party, _slip_msg(slip), b"public-slip-dom")
        for pname, p in points.items():
            counted = pname in roster.counted_points
            if counted:
                dig = cc.h(b"contrib", pname.encode(), str(epoch).encode())
                receipts[pname] = self._issue_receipt(
                    pname, p.seat_key, epoch, True, True, "counted; slip published", dig)
            else:
                dig = cc.h(b"contrib-refused", pname.encode(), str(epoch).encode())
                receipts[pname] = self._issue_receipt(
                    pname, p.seat_key, epoch, False, True,
                    roster.rejected.get(pname, "not counted"), dig)
        return PublishOutcome(True, slip, "published", receipts, total)
