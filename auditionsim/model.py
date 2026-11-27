"""Domain model: venues, seats, points, participants, vouchers, receipts.

Attestation rules the drill enforces
------------------------------------
* A venue (单位) is an organisation; a seat (席位) is a stable identity inside
  it.  A seat key is ``venue#seat_id``.
* A participant belongs to exactly one seat.  A Voucher is the venue's signed
  attestation that this participant is enrolled *this round* (this epoch).
* A submission is attributed to a point, which sits at a seat.  The same
  venue+seat presenting twice (a reconnecting point) must not count twice; a
  standby machine and the point it replaces, or two points registered as a
  "same-seat machine swap" (同席换机), collapse to ONE independent seat.
"""

from dataclasses import dataclass, field
from typing import Optional

from . import crypto_core as cc


@dataclass(frozen=True)
class Seat:
    venue: str
    seat_id: str

    @property
    def key(self) -> str:
        return f"{self.venue}#{self.seat_id}"

    @property
    def venue_of_key(self) -> str:
        return self.key.split("#", 1)[0]


@dataclass
class Participant:
    seat: Seat
    name: str   # fictional label, for drill readability only
    score: int  # fictional integer score for this round


@dataclass
class Voucher:
    """Signed participation credential: participant is enrolled this epoch."""
    participant_name: str
    seat_key: str
    epoch: int
    opening: bytes   # binds the enrolment without revealing the score
    sig: dict        # Schnorr signature by the awarding venue

    @property
    def venue(self) -> str:
        return self.seat_key.split("#", 1)[0]

    def message(self) -> bytes:
        return cc.h(b"voucher-msg", self.participant_name.encode(),
                    self.seat_key.encode(), str(self.epoch).encode(), self.opening)

    def digest(self) -> bytes:
        """Round-stable identity of the voucher (epoch + seat + participant)."""
        return cc.h(b"voucher-id", self.participant_name.encode(),
                    self.seat_key.encode(), str(self.epoch).encode())

    @staticmethod
    def create(participant: Participant, epoch: int, venue_party: cc.Party,
               opening: Optional[bytes] = None) -> "Voucher":
        if opening is None:
            opening = cc.h(b"open", participant.name.encode(),
                           participant.seat.key.encode(), str(epoch).encode())
        v = Voucher(participant.name, participant.seat.key, epoch, opening, {})
        dom = b"voucher-dom|" + v.venue.encode() + b"|" + str(epoch).encode()
        v.sig = cc.schnorr_sign(venue_party, v.message(), dom)
        return v


@dataclass
class Point:
    """An audition point: one submitting machine at a venue seat."""
    name: str
    venue: str
    seat: Seat
    is_standby: bool = False
    replaces: Optional[str] = None
    party: cc.Party = None

    def __post_init__(self):
        if self.party is None:
            self.party = cc.Party("point:" + self.name)

    @property
    def seat_key(self) -> str:
        return self.seat.key


@dataclass
class Receipt:
    """Per-point receipt for one round.  Carries no score -- only two honest
    facts plus a reason:
      * ``counted``   -- this point's contribution was accepted into this
                         round's batch (valid voucher, not a duplicate).
      * ``published`` -- a public slip was issued for this round.
    A point can query its own receipt and learn exactly whether it was
    counted and whether the round actually published."""
    point_name: str
    seat_key: str
    epoch: int
    counted: bool
    published: bool
    reason: str
    contribution_digest: bytes
    sig: dict

    def message(self) -> bytes:
        return cc.h(b"receipt-msg", self.point_name.encode(), self.seat_key.encode(),
                    str(self.epoch).encode(),
                    b"1" if self.counted else b"0",
                    b"1" if self.published else b"0",
                    self.reason.encode(), self.contribution_digest)

    def digest(self) -> str:
        return cc.short_hash(b"receipt", self.point_name.encode(),
                             self.seat_key.encode(), str(self.epoch).encode(),
                             b"1" if self.counted else b"0",
                             b"1" if self.published else b"0", self.reason.encode())

    def verify(self, booth_pub: int) -> bool:
        return (cc.schnorr_verify(self.sig, expected_y=booth_pub)
                and self.sig["msg"] == self.message())
