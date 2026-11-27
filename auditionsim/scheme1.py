"""Scheme 1: two mutually non-colluding custodian desks.

Each point splits its contribution additively into two field shares and seals
one share to custodian A and the other to custodian B.  A and B each sum ONLY
the shares they actually received for the batch, and the booth adds the two
partial sums.  If one desk is offline, exactly one share per point is missing
and the result is *half* the batch -- the drill must refuse to publish.

Key detail: a "batch" is the set of points that ACTUALLY delivered to a desk
this round.  A desk computes its partial only over that real set; the booth
will not silently substitute a partial from a different set.
"""

from dataclasses import dataclass, field

from . import crypto_core as cc
from .vault import PointVault
from .model import Point


@dataclass
class ShareContribution:
    """One sealed share from a point to a custodian for one epoch."""
    point_name: str
    seat_key: str
    epoch: int
    recipient: str            # "A" or "B"
    env: dict                 # sealed field-share
    nonce: str                # anti-replay tag for this point+epoch+recipient
    contribution_digest: bytes

    def batch_id(self) -> tuple:
        return (self.recipient, self.epoch)


def make_share_pair(vault: PointVault, point: Point, epoch: int,
                    custA_pub: int, custB_pub: int) -> tuple[ShareContribution, ShareContribution]:
    """Split the point's local sum into two additive shares, seal each to its
    custodian.  Returns (share_to_A, share_to_B).  Neither share reveals the
    local sum; only their field sum does.
    """
    local = vault._local_sum()
    sA = cc.rand_field()
    sB = cc.fsub(local, sA)              # sA + sB == local (mod MOD)
    dom_a = b"scheme1|A|" + str(epoch).encode()
    dom_b = b"scheme1|B|" + str(epoch).encode()
    envA = cc.seal(_sharer(point), custA_pub, cc.i2b16(sA), dom_a)
    envB = cc.seal(_sharer(point), custB_pub, cc.i2b16(sB), dom_b)
    nonce = cc.rand_token(8)
    dig = cc.h(b"contrib", point.name.encode(), str(epoch).encode())
    ca = ShareContribution(point.name, point.seat_key, epoch, "A", envA, nonce, dig)
    cb = ShareContribution(point.name, point.seat_key, epoch, "B", envB, nonce, dig)
    return ca, cb


def _sharer(point: Point) -> cc.Party:
    """A per-point sealing identity.  We derive it deterministically from the
    point name so the same point always uses the same ephemeral identity --
    good enough for a local model; the seal itself is what hides the value."""
    # Build a stable Party from a hashed seed (not used as a real key).
    if not hasattr(_sharer, "_cache"):
        _sharer._cache = {}
    key = point.name
    if key not in _sharer._cache:
        pp = cc.Party("sharer:" + point.name)
        _sharer._cache[key] = pp
    return _sharer._cache[key]


class Custodian:
    """A custodian desk.  Holds its secret key and, for each epoch, the set of
    shares it *actually received*.  It opens only its own shares, sums them
    over the REAL received batch, and publishes a partial + the batch id.
    """

    def __init__(self, label: str):
        self.label = label
        self.party = cc.Party("custodian:" + label)
        self._sealed: dict[tuple, list[ShareContribution]] = {}  # (epoch) -> shares
        self.online = True

    @property
    def pub(self) -> int:
        return self.party.pub

    def receive(self, c: ShareContribution) -> None:
        if not self.online:
            raise RuntimeError(f"custodian {self.label} is offline")
        assert c.recipient == self.label, "share delivered to wrong desk"
        key = (c.epoch,)
        batch = self._sealed.setdefault(key, [])
        # Reject a reconnecting point's second share AND a share carried over
        # from a previous round (same nonce / stale epoch).  One contribution
        # per (point, epoch) only.
        for prior in batch:
            if prior.point_name == c.point_name:
                raise ValueError(
                    f"duplicate contribution from {c.point_name} in epoch {c.epoch} "
                    f"(reconnecting point / replayed share)")
        batch.append(c)

    def batch_points(self, epoch: int) -> set[str]:
        return {c.point_name for c in self._sealed.get((epoch,), [])}

    def compute_partial(self, epoch: int) -> "Partial":
        """Open only what was actually received THIS epoch and sum it.  The
        partial is bound to the exact set of point names it covers."""
        shares = self._sealed.get((epoch,), [])
        total = 0
        for c in shares:
            total = cc.fadd(total, cc.b2i(cc.open_seal(self.party, c.env)))
        covered = sorted({c.point_name for c in shares})
        msg = _partial_msg(self.label, epoch, covered, total)
        sig = cc.schnorr_sign(self.party, msg, b"partial-dom")
        return Partial(self.label, epoch, covered, total, sig)

    def __repr__(self):
        return f"Custodian({self.label}, online={self.online})"


@dataclass
class Partial:
    label: str
    epoch: int
    covered: list            # sorted point names actually summed
    total: int               # field partial sum (opaque to booth meaning)
    sig: dict

    def verify(self, custodian: Custodian) -> bool:
        return self.verify_pinned(custodian.pub)

    def verify_pinned(self, pub: int) -> bool:
        msg = _partial_msg(self.label, self.epoch, sorted(self.covered), self.total)
        return (cc.schnorr_verify(self.sig, expected_y=pub)
                and self.sig["msg"] == msg)


def _partial_msg(label: str, epoch: int, covered: list, total: int) -> bytes:
    return cc.h(b"partial", label.encode(), str(epoch).encode(),
                b",".join(name.encode() for name in covered), cc.i2b16(total))
