"""Scheme 2: point-coordinated canceling masks.

The points jointly agree (via pairwise DH) on per-epoch masks that cancel
ONLY when every point in the agreed roster for that epoch contributes.  Each
point sends the central tally desk a *masked* number:

    masked_i = local_sum_i + mask_i      (mod MOD)

where mask_i = sum over peers j of  sign(i,j) * m_{ij},  m_{ij} the DH-derived
per-epoch mask shared by i and j, and sign flips so each pair contributes
+m and -m.  Summing over the FULL roster therefore cancels the masks to zero
and yields the true total; any missing point leaves a non-zero residue.

Consequences the drill enforces
-------------------------------
* The central desk never needs to trust the two custodian desks -- it only
  sees masked integers and their sum.
* If someone is absent after half the points already submitted, the central
  desk MUST NOT silently delete that point from the roster and recombine:
  the masks were bound to the full roster, so dropping a point leaves a
  residue.  The only sound fix is a NEW round (new epoch) with the new roster,
  and the remaining points must re-submit fresh masks for that epoch.
"""

from dataclasses import dataclass

from . import crypto_core as cc
from .vault import PointVault
from .model import Point


@dataclass
class MaskedSubmission:
    point_name: str
    seat_key: str
    epoch: int
    masked_value: int
    contribution_digest: bytes
    sig: dict  # point signs its masked submission (authenticity, not secrecy)

    def verify(self, point_pub: int) -> bool:
        msg = _masked_msg(self.point_name, self.seat_key, self.epoch,
                          self.masked_value, self.contribution_digest)
        return cc.schnorr_verify(self.sig, expected_y=point_pub) and self.sig["msg"] == msg


def _masked_msg(point_name, seat_key, epoch, masked_value, dig) -> bytes:
    return cc.h(b"masked", point_name.encode(), seat_key.encode(),
                str(epoch).encode(), cc.i2b16(masked_value), dig)


class PointMasquer:
    """A point's view of the round: its own party, the roster's public keys,
    and the DH pairwise masks for a given epoch.  Masks depend on BOTH the
    epoch and the exact roster, so a different round (or a trimmed roster) can
    never accidentally recombine with stale masks.
    """

    def __init__(self, point: Point, roster_pubs: dict[str, int], epoch: int, roster_names: list[str]):
        self.point = point
        self.epoch = epoch
        self.roster_pubs = dict(roster_pubs)
        self.roster_names = list(roster_names)
        self._shared: dict[str, bytes] = {}
        for name, y in self.roster_pubs.items():
            if name == point.name:
                continue
            self._shared[name] = cc.dh_shared(point.party.x, y)

    def _pair_mask(self, peer: str) -> int:
        # Symmetric in (self, peer): both endpoints must derive the SAME value
        # for the pair, so canonicalise the label order.  Otherwise P1's
        # "+m(P1,P2)" and P2's "-m(P2,P1)" would use different m and never
        # cancel.
        a, b = sorted((self.point.name, peer))
        return cc.prf_field(self._shared[peer], b"scheme2-mask",
                            str(self.epoch).encode(), a.encode(), b.encode())

    def mask_i(self) -> int:
        """This point's net mask (sum of +m / -m over peers)."""
        total = 0
        for peer in self._shared:
            m = self._pair_mask(peer)
            sign = +1 if self.point.name < peer else -1
            total = cc.fadd(total, m if sign == 1 else cc.fsub(0, m))
        return total

    def build(self, vault: PointVault) -> MaskedSubmission:
        local = vault._local_sum()
        masked = cc.fadd(local, self.mask_i())
        dig = cc.h(b"contrib", self.point.name.encode(), str(self.epoch).encode())
        msg = _masked_msg(self.point.name, self.point.seat_key, self.epoch, masked, dig)
        sig = cc.schnorr_sign(self.point.party, msg, b"masked-dom")
        return MaskedSubmission(self.point.name, self.point.seat_key,
                                self.epoch, masked, dig, sig)


def combine_masked(subs: list[MaskedSubmission]) -> int:
    """Central desk's recombination: just add the masked integers."""
    return cc.fsum(s.masked_value for s in subs)


def masks_cancel(subs: list[MaskedSubmission], masqers: dict[str, PointMasquer]) -> bool:
    """True iff the submitted set is exactly the full roster and the masks
    cancel -- i.e. this is a complete, sound round."""
    names = sorted(s.point_name for s in subs)
    expected = sorted(masqers[next(iter(masqers))].roster_names)
    if names != expected:
        return False
    net = 0
    for s in subs:
        net = cc.fadd(net, masqers[s.point_name].mask_i())
    return net == 0
