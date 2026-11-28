"""The door.

An usher opens a *fresh* inspection terminal after the ticket desk is shut.  The
terminal holds only:

* the one screening that is valid right now (index, day, tier),
* its own local admission log from earlier in the day,
* the public keys of the desk and of whatever path minted eligibility,

and it is handed the buyer's presentation (stub + revealed secret + a fresh
nonce signed by the holder's program-scoped key).  It can therefore do a complete
offline verification without any link back to the desk:

1. desk signature over the stub,
2. stub matches *this* screening (program, day, tier),
3. revealed secret matches the stub's commitment,
4. holder's signature over the door's nonce proves possession (non-transferability),
5. the stub has not already been admitted (permanent replay guard).

A stub can take *at most one seat, ever*.  Showing it again -- including after the
holder has left -- is refused and allocates no extra seat.  Departures produce
exit receipts but never free the seat for reissue.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from . import crypto
from .assertion import EligibilityAssertion
from .crypto import DOMAIN
from .preissue import PreIssuedToken
from .tickets import Stub, binding_message


@dataclass
class TicketView:
    """The single screening this terminal is authorised to inspect."""

    program_id: str
    program_fingerprint: str
    index: int
    day: date
    tier: int


@dataclass
class ScanRecord:
    seq: int
    kind: str                 # "entry" | "exit"
    ticket_id: str | None
    index: int | None
    accepted: bool
    reason: str
    seat: int | None = None
    sig: dict | None = None


class DoorTerminal:
    def __init__(self, terminal_id: str, view: TicketView,
                 desk_pub: crypto.PublicKey,
                 trusted: dict[str, crypto.PublicKey] | None = None):
        self.terminal_id = terminal_id
        self.view = view
        self.desk_pub = desk_pub
        # Public keys for whichever eligibility paths this door accepts.  It only
        # ever *verifies*; it cannot mint tokens or assertions.
        self.trusted = dict(trusted or {})
        self.key = crypto.PrivateKey.generate()
        self.public = self.key.public
        self.log: list[ScanRecord] = []
        self._seq = 0
        self._next_seat = 1
        # Permanent replay guard: a stub admitted once can never take a new seat.
        self._admitted: dict[tuple[str, int], int] = {}  # (ticket_id, index) -> seat
        # Currently inside (entered, not yet exited), keyed the same way.
        self._inside: set[tuple[str, int]] = set()
        self._seat_owner: dict[int, tuple[str, int]] = {}

    def seed_history(self, entries: list[dict]) -> None:
        """Load earlier admission records for this screening (offline copy)."""
        for e in entries:
            if e.get("kind") == "entry" and e.get("accepted") and e.get("ticket_id") is not None:
                key = (e["ticket_id"], e["index"])
                self._admitted[key] = e["seat"]
                self._seat_owner[e["seat"]] = key
                self._next_seat = max(self._next_seat, e["seat"] + 1)

    # -- core verification ---------------------------------------------- #
    def _verify_stub(self, presentation: dict, nonce: str) -> tuple[bool, str, Stub | None]:
        raw = presentation.get("stub")
        if not raw:
            return False, "no stub supplied", None
        try:
            stub = Stub.from_signed(raw)
        except (KeyError, ValueError):
            return False, "malformed stub", None

        if not crypto.verify(self.desk_pub, DOMAIN["stub"], stub.payload(), stub.sig):
            return False, "stub signature invalid", None
        if stub.program_id != self.view.program_id:
            return False, "stub is for another programme", None
        if stub.day != self.view.day or stub.tier != self.view.tier:
            return False, "stub does not match the screening on this date", None

        secret = presentation.get("secret", "")
        try:
            revealed = bytes.fromhex(secret)
        except ValueError:
            return False, "malformed secret", None
        if crypto.sha256(revealed).hex() != stub.secret_commitment:
            return False, "revealed secret mismatch", None

        bmsg = binding_message(nonce, stub.ticket_id, stub.index)
        if not crypto.verify(stub.binding_pub, DOMAIN["stub_binding"], bmsg, presentation.get("binding_sig")):
            return False, "holder binding signature invalid (not this holder's stub)", None

        # Root eligibility, verified offline from the buyer's own material.
        ok, reason = self._verify_eligibility(presentation.get("eligibility"), stub)
        if not ok:
            return False, reason, None

        return True, "verified", stub

    def _verify_eligibility(self, material: dict | None, stub: Stub) -> tuple[bool, str]:
        """Verify the root credential (pre-issued token or service assertion)."""
        if not self.trusted:
            # A door configured without eligibility keys trusts the desk stub
            # alone (kept for backwards-compatible deployments).
            return True, "desk stub only"
        if not material:
            return False, "no eligibility material presented"
        from .assertion import verify_assertion
        from .preissue import verify_token

        kind = material.get("kind")
        if kind not in self.trusted:
            return False, f"door does not accept eligibility kind {kind!r}"
        pub = self.trusted[kind]
        if kind == "preissue":
            try:
                tok = PreIssuedToken.from_signed(material["token"])
            except (KeyError, ValueError):
                return False, "malformed pre-issued token"
            if not verify_token(pub, tok, self.view.program_id, self.view.day,
                                self.view.tier, stub.binding_pub):
                return False, "pre-issued token invalid for this screening"
            return True, "token ok"
        try:
            assertion, sig = EligibilityAssertion.from_signed(material["assertion"])
        except (KeyError, ValueError):
            return False, "malformed eligibility assertion"
        if not verify_assertion(pub, assertion, sig, self.view.program_id, self.view.day,
                                self.view.tier, stub.binding_pub, stub.holder_handle):
            return False, "eligibility assertion invalid for this screening"
        return True, "assertion ok"

    # -- entry / exit --------------------------------------------------- #
    def admit(self, presentation: dict, nonce: str, seat: int | None = None) -> dict:
        self._seq += 1
        ok, reason, stub = self._verify_stub(presentation, nonce)
        if not ok:
            self.log.append(ScanRecord(self._seq, "entry", None, None, False, reason))
            return {"accepted": False, "reason": reason}

        key = (stub.ticket_id, stub.index)
        if key in self._admitted:
            self.log.append(ScanRecord(self._seq, "entry", stub.ticket_id, stub.index, False,
                                       "stub already admitted; no extra seat"))
            return {
                "accepted": False,
                "reason": "stub already admitted; no extra seat",
                "already_seat": self._admitted[key],
                "ticket_id": stub.ticket_id,
                "index": stub.index,
            }

        if seat is None:
            seat = self._next_seat
        if seat in self._seat_owner:
            self.log.append(ScanRecord(self._seq, "entry", stub.ticket_id, stub.index, False,
                                       "requested seat already occupied"))
            return {"accepted": False, "reason": "requested seat already occupied", "seat": seat}

        self._admitted[key] = seat
        self._inside.add(key)
        self._seat_owner[seat] = key
        self._next_seat = max(self._next_seat, seat + 1)
        receipt = {
            "terminal_id": self.terminal_id,
            "program_id": self.view.program_id,
            "program_fingerprint": self.view.program_fingerprint,
            "index": stub.index,
            "day": stub.day.isoformat(),
            "tier": stub.tier,
            "ticket_id": stub.ticket_id,
            "holder_handle": stub.holder_handle,
            "seat": seat,
            "seq": self._seq,
        }
        receipt["sig"] = self.key.sign(DOMAIN["entry_receipt"], receipt)
        self.log.append(ScanRecord(self._seq, "entry", stub.ticket_id, stub.index, True,
                                   "admitted", seat, receipt["sig"]))
        return {"accepted": True, "seat": seat, "receipt": receipt}

    def depart(self, presentation: dict, nonce: str) -> dict:
        self._seq += 1
        ok, reason, stub = self._verify_stub(presentation, nonce)
        if not ok:
            self.log.append(ScanRecord(self._seq, "exit", None, None, False, reason))
            return {"accepted": False, "reason": reason}
        key = (stub.ticket_id, stub.index)
        if key not in self._inside:
            self.log.append(ScanRecord(self._seq, "exit", stub.ticket_id, stub.index, False,
                                       "exit without a matching entry"))
            return {"accepted": False, "reason": "exit without a matching entry"}
        self._inside.discard(key)
        seat = self._admitted[key]  # seat remains permanently allocated
        receipt = {
            "terminal_id": self.terminal_id,
            "program_id": self.view.program_id,
            "index": stub.index,
            "ticket_id": stub.ticket_id,
            "holder_handle": stub.holder_handle,
            "seat": seat,
            "seq": self._seq,
        }
        receipt["sig"] = self.key.sign(DOMAIN["exit_receipt"], receipt)
        self.log.append(ScanRecord(self._seq, "exit", stub.ticket_id, stub.index, True,
                                   "departed", seat, receipt["sig"]))
        return {"accepted": True, "seat": seat, "receipt": receipt}

    # -- reconciliation of the screening --------------------------------- #
    def admitted_count(self) -> int:
        """Number of distinct seats issued (permanent)."""
        return len(self._admitted)

    def inside_count(self) -> int:
        return len(self._inside)

    def history(self) -> list[dict]:
        return [
            {"seq": r.seq, "kind": r.kind, "ticket_id": r.ticket_id, "index": r.index,
             "accepted": r.accepted, "reason": r.reason, "seat": r.seat}
            for r in self.log
        ]

    def receipt_valid(self, receipt: dict, domain: str) -> bool:
        body = {k: v for k, v in receipt.items() if k != "sig"}
        return crypto.verify(self.public, DOMAIN[domain], body, receipt.get("sig"))
