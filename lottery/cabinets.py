"""Mock witness cabinets implementing 2-of-2 custody of sealed originals.

Each cabinet stores one XOR share of an original reveal payload.  A single
cabinet can never reconstruct the original.  The pair only releases the sealed
original when *both* cabinets are online and each hands over a receipt that
matches the other: same round, same ticket, valid receipt digest.  The combined
shares must also hash to the digest registered at deposit time.

This is a local simulation, but it models exactly the guarantee the public draw
relies on: "各交一张匹配的回执才能对外取回封存原值".
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from .crypto import join_shares, share_digest, sha256_hex, split_secret


class CabinetError(Exception):
    """Base class for custody errors."""


class CabinetOffline(CabinetError):
    def __init__(self, cabinet_id: str):
        super().__init__(f"witness cabinet {cabinet_id!r} is offline")
        self.cabinet_id = cabinet_id


class CabinetMissing(CabinetError):
    def __init__(self, cabinet_id: str, round_id: str, ticket_id: str):
        super().__init__(
            f"cabinet {cabinet_id!r} holds no share for {round_id!r}/{ticket_id!r}"
        )
        self.cabinet_id = cabinet_id


class ReceiptMismatch(CabinetError):
    """Raised when the two cabinets' receipts do not match."""


@dataclass
class CabinetReceipt:
    cabinet_id: str
    round_id: str
    ticket_id: str
    receipt_id: str
    receipt_hash: str

    def to_dict(self) -> dict:
        return {
            "cabinet_id": self.cabinet_id,
            "round_id": self.round_id,
            "ticket_id": self.ticket_id,
            "receipt_id": self.receipt_id,
            "receipt_hash": self.receipt_hash,
        }


@dataclass
class _Held:
    share: bytes
    receipt: CabinetReceipt


class WitnessCabinet:
    """A single cabinet.  ``compliant`` models an honest drill participant."""

    def __init__(self, cabinet_id: str):
        self.cabinet_id = cabinet_id
        self.online = True
        self._held: dict[tuple[str, str], _Held] = {}

    # -- operator controls used by drills --------------------------------
    def set_online(self, online: bool) -> None:
        self.online = online

    def has(self, round_id: str, ticket_id: str) -> bool:
        return (round_id, ticket_id) in self._held

    # -- custody operations ----------------------------------------------
    def receive(self, round_id: str, ticket_id: str, share: bytes, receipt_id: str) -> CabinetReceipt:
        rh = share_digest(self.cabinet_id, round_id, ticket_id, share)
        rec = CabinetReceipt(
            cabinet_id=self.cabinet_id,
            round_id=round_id,
            ticket_id=ticket_id,
            receipt_id=receipt_id,
            receipt_hash=rh,
        )
        self._held[(round_id, ticket_id)] = _Held(share=share, receipt=rec)
        return rec

    def handover(self, round_id: str, ticket_id: str) -> tuple[bytes, CabinetReceipt]:
        if not self.online:
            raise CabinetOffline(self.cabinet_id)
        try:
            held = self._held[(round_id, ticket_id)]
        except KeyError:
            raise CabinetMissing(self.cabinet_id, round_id, ticket_id) from None
        return held.share, held.receipt


@dataclass
class RetrieveResult:
    original: bytes
    receipts: list[CabinetReceipt]


class CabinetPair:
    """Two cabinets that jointly guard sealed originals."""

    def __init__(self, ids: tuple[str, str] = ("cabinet-A", "cabinet-B")):
        self.a = WitnessCabinet(ids[0])
        self.b = WitnessCabinet(ids[1])
        # expected original digest registered at deposit time, keyed by round/ticket
        self._expected: dict[tuple[str, str], str] = {}

    @property
    def cabinets(self) -> tuple[WitnessCabinet, WitnessCabinet]:
        return (self.a, self.b)

    def cabinet(self, cabinet_id: str) -> WitnessCabinet:
        for c in self.cabinets:
            if c.cabinet_id == cabinet_id:
                return c
        raise KeyError(cabinet_id)

    def set_online(self, cabinet_id: str, online: bool) -> None:
        self.cabinet(cabinet_id).set_online(online)

    def deposit(self, round_id: str, ticket_id: str, original: bytes) -> list[CabinetReceipt]:
        """Split ``original`` and hand one share to each cabinet.

        Returns the two matching receipts.  A single share reveals nothing.
        """
        share_a, share_b = split_secret(original)
        rid = secrets.token_hex(8)
        rec_a = self.a.receive(round_id, ticket_id, share_a, rid)
        rec_b = self.b.receive(round_id, ticket_id, share_b, rid)
        self._expected[(round_id, ticket_id)] = sha256_hex(original)
        return [rec_a, rec_b]

    def retrieve(self, round_id: str, ticket_id: str) -> RetrieveResult:
        """Return the sealed original only in exchange for two matching receipts.

        Any offline or missing cabinet aborts the handover: the caller cannot
        obtain the original from a single share.
        """
        share_a, rec_a = self.a.handover(round_id, ticket_id)
        share_b, rec_b = self.b.handover(round_id, ticket_id)

        if rec_a.round_id != rec_b.round_id or rec_a.ticket_id != rec_b.ticket_id:
            raise ReceiptMismatch("receipts reference different custody slots")
        if rec_a.receipt_id != rec_b.receipt_id:
            raise ReceiptMismatch("receipts do not share a custody transaction id")

        original = join_shares(share_a, share_b)
        expected = self._expected.get((round_id, ticket_id))
        if expected is not None and sha256_hex(original) != expected:
            raise ReceiptMismatch("recombined shares do not match the sealed original")
        return RetrieveResult(original=original, receipts=[rec_a, rec_b])

    def has_deposit(self, round_id: str, ticket_id: str) -> bool:
        return self.a.has(round_id, ticket_id) and self.b.has(round_id, ticket_id)
