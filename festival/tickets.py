"""Tickets, stubs and the desk-side sale logic.

A *ticket bundle* is one sale to one holder.  It produces one :class:`Stub` per
screening the sale covers.  Every stub in a bundle:

* carries the shared ``ticket_id`` (so the two stubs are known to belong
  together),
* carries the holder's *program-scoped* handle and binding key (so it is pinned
  to that wallet and cannot be transferred, and so the holder is not identifiable
  outside this programme),
* carries a per-stub random secret commitment (the on-site presenter must reveal
  the matching secret).

The desk refuses to mix eligibility proofs belonging to different holders into
one bundle -- that is the "don't stitch two viewers together" rule.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import date

from . import crypto
from .crypto import DOMAIN


class MixedHolderError(Exception):
    """A sale tried to combine eligibility belonging to different holders."""


class DeskRejection(Exception):
    pass


@dataclass(frozen=True)
class Stub:
    ticket_id: str
    program_id: str
    index: int
    day: date
    tier: int
    holder_handle: str          # program-scoped nullifier
    binding_pub: crypto.PublicKey
    secret_commitment: str      # sha256(stub_secret), hex
    sig: dict

    def payload(self) -> dict:
        return {
            "ticket_id": self.ticket_id,
            "program_id": self.program_id,
            "index": self.index,
            "day": self.day.isoformat(),
            "tier": self.tier,
            "holder_handle": self.holder_handle,
            "binding_pub": self.binding_pub.hex,
            "secret_commitment": self.secret_commitment,
        }

    def to_signed(self) -> dict:
        out = self.payload()
        out["sig"] = self.sig
        return out

    @classmethod
    def from_signed(cls, data: dict) -> "Stub":
        return cls(
            ticket_id=data["ticket_id"],
            program_id=data["program_id"],
            index=data["index"],
            day=date.fromisoformat(data["day"]),
            tier=data["tier"],
            holder_handle=data["holder_handle"],
            binding_pub=crypto.PublicKey.from_hex(data["binding_pub"]),
            secret_commitment=data["secret_commitment"],
            sig=data["sig"],
        )


def binding_message(nonce: str, ticket_id: str, index: int) -> dict:
    """The exact message a holder signs at the door to prove possession."""
    return {"nonce": nonce, "ticket_id": ticket_id, "index": index}


@dataclass
class SaleResult:
    """What the desk returns for a completed sale."""

    ticket_id: str
    program_id: str
    program_fingerprint: str
    stubs: dict[int, "Stub"]
    stub_secrets: dict[int, bytes]
    holder_handle: str
    binding_pub: crypto.PublicKey
    receipt: dict


class IssuedTicket:
    """What the wallet keeps after a successful sale (holds the real key).

    Also stores, per stub, the eligibility material the desk accepted, so the
    holder can present it (plus the stub) to a door that still wants to verify the
    *root* credential -- e.g. path A's pre-issued token or path B's assertion.
    """

    def __init__(self, sale: SaleResult, binding_key: crypto.PrivateKey,
                 eligibility: dict[int, dict] | None = None):
        self.ticket_id = sale.ticket_id
        self.program_id = sale.program_id
        self.program_fingerprint = sale.program_fingerprint
        self.holder_handle = sale.holder_handle
        self.binding_key = binding_key
        self.stubs = dict(sale.stubs)
        self.stub_secrets = dict(sale.stub_secrets)
        self.receipt = sale.receipt
        self.eligibility = dict(eligibility or {})

    @property
    def binding_pub(self) -> crypto.PublicKey:
        return self.binding_key.public

    def present(self, index: int, nonce: str) -> dict:
        """Build the material handed to the door for one stub."""
        stub = self.stubs[index]
        sig = self.binding_key.sign(
            DOMAIN["stub_binding"], binding_message(nonce, self.ticket_id, index)
        )
        out = {
            "stub": stub.to_signed(),
            "secret": self.stub_secrets[index].hex(),
            "nonce": nonce,
            "binding_sig": sig,
        }
        if index in self.eligibility:
            out["eligibility"] = serialize_eligibility(self.eligibility[index])
        return out


def serialize_eligibility(material: dict) -> dict:
    """Turn a wallet-side eligibility material dict into a transferable dict."""
    if material["kind"] == "preissue":
        return {"kind": "preissue", "token": material["token"].to_signed()}
    if material["kind"] == "assertion":
        assertion = material["assertion"]
        body = assertion.payload()
        body["sig"] = material["assertion_sig"]
        return {"kind": "assertion", "assertion": body}
    raise ValueError(f"unknown eligibility kind {material['kind']!r}")


class TicketDesk:
    def __init__(self, desk_id: str, program, trusted: dict[str, crypto.PublicKey]):
        self.desk_id = desk_id
        self.program = program
        self.key = crypto.PrivateKey.generate()
        self.public = self.key.public
        self.trusted = dict(trusted)  # {"preissue": pub, "assertion": pub}
        self.ledger: list[dict] = []
        self._seq = 0

    # -- eligibility checks --------------------------------------------- #
    def _check_material(self, index: int, material: dict, binding_pub: crypto.PublicKey, nullifier: str) -> dict:
        from .assertion import verify_assertion
        from .preissue import verify_token

        s = self.program.screening(index)
        kind = material["kind"]
        if kind not in self.trusted:
            raise DeskRejection(f"desk does not accept eligibility kind {kind!r}")
        pub = self.trusted[kind]
        if kind == "preissue":
            tok = material["token"]
            ok = verify_token(pub, tok, self.program.program_id, s.date, s.age_tier, binding_pub)
            ref = tok.token_id
        else:
            ok = verify_assertion(
                pub, material["assertion"], material["assertion_sig"],
                self.program.program_id, s.date, s.age_tier, binding_pub, nullifier,
            )
            ref = material["assertion"].assertion_id
        if not ok:
            raise DeskRejection(f"eligibility for screening {index} did not verify")
        return {"kind": kind, "ref": ref}

    # -- sale ------------------------------------------------------------ #
    def sell(self, proofs: dict[int, dict]) -> SaleResult:
        """Sell stubs for the given screening indices.

        ``proofs`` maps screening index -> eligibility material from one wallet.
        For a full double-screening bundle, pass both indices.
        """
        if not proofs:
            raise DeskRejection("no screenings requested")
        for index in proofs:
            if not (0 <= index < len(self.program)):
                raise DeskRejection(f"bad screening index {index}")

        # Single-holder rule.  Every proof must name the same holder handle and
        # the same binding key; otherwise we are stitching two viewers together.
        holders = {(m["binding_pub"].value, m["nullifier"]) for m in proofs.values()}
        if len(holders) != 1:
            raise MixedHolderError(
                "eligibility proofs belong to different holders; a bundle is one holder"
            )
        (binding_val, holder_handle) = holders.pop()
        binding_pub = crypto.PublicKey(binding_val)

        refs = {i: self._check_material(i, m, binding_pub, holder_handle) for i, m in proofs.items()}

        self._seq += 1
        ticket_id = f"{self.desk_id}-T{self._seq:06d}"
        stubs: dict[int, Stub] = {}
        stub_secrets: dict[int, bytes] = {}
        record_stubs = []
        for index in sorted(proofs):
            s = self.program.screening(index)
            secret = secrets.token_bytes(32)
            commitment = crypto.sha256(secret).hex()
            signed_payload = {
                "ticket_id": ticket_id,
                "program_id": self.program.program_id,
                "index": index,
                "day": s.date.isoformat(),
                "tier": s.age_tier,
                "holder_handle": holder_handle,
                "binding_pub": binding_pub.hex,
                "secret_commitment": commitment,
            }
            stub = Stub(
                ticket_id=ticket_id,
                program_id=self.program.program_id,
                index=index,
                day=s.date,
                tier=s.age_tier,
                holder_handle=holder_handle,
                binding_pub=binding_pub,
                secret_commitment=commitment,
                sig=self.key.sign(DOMAIN["stub"], signed_payload),
            )
            stubs[index] = stub
            stub_secrets[index] = secret
            record_stubs.append(
                {"index": index, "day": s.date.isoformat(), "tier": s.age_tier,
                 "eligibility": refs[index]}
            )

        receipt = {
            "desk_id": self.desk_id,
            "ticket_id": ticket_id,
            "program_id": self.program.program_id,
            "program_fingerprint": self.program.fingerprint(),
            "indices": sorted(proofs),
            "stub_count": len(proofs),
        }
        receipt["sig"] = self.key.sign(DOMAIN["desk_reconcile"], receipt)

        self.ledger.append(
            {
                "ticket_id": ticket_id,
                "desk_id": self.desk_id,
                "program_id": self.program.program_id,
                "program_fingerprint": self.program.fingerprint(),
                "holder": {"nullifier": holder_handle, "binding_pub": binding_pub.hex},
                "stubs": record_stubs,
            }
        )
        return SaleResult(
            ticket_id=ticket_id,
            program_id=self.program.program_id,
            program_fingerprint=self.program.fingerprint(),
            stubs=stubs,
            stub_secrets=stub_secrets,
            holder_handle=holder_handle,
            binding_pub=binding_pub,
            receipt=receipt,
        )

    # -- reconciliation ("核自己的账") ----------------------------------- #
    def reconcile(self) -> dict:
        by_index: dict[int, int] = {i: 0 for i in range(len(self.program))}
        seats = 0
        problems = []
        for rec in self.ledger:
            if rec["program_id"] != self.program.program_id:
                problems.append(f"foreign program in ledger: {rec['ticket_id']}")
            if not rec["stubs"]:
                problems.append(f"empty ticket: {rec['ticket_id']}")
            if len({(rec["holder"]["nullifier"], rec["holder"]["binding_pub"])}) != 1:
                problems.append(f"ticket mixes holders: {rec['ticket_id']}")
            for st in rec["stubs"]:
                by_index[st["index"]] = by_index.get(st["index"], 0) + 1
                seats += 1
                s = self.program.screening(st["index"])
                if st["day"] != s.date.isoformat() or st["tier"] != s.age_tier:
                    problems.append(f"stub no longer matches programme: {rec['ticket_id']}#{st['index']}")
        summary = {
            "desk_id": self.desk_id,
            "program_id": self.program.program_id,
            "program_fingerprint": self.program.fingerprint(),
            "tickets": len(self.ledger),
            "seats": seats,
            "by_index": {str(k): v for k, v in sorted(by_index.items())},
            "ok": not problems,
            "problems": problems,
        }
        summary["sig"] = self.key.sign(DOMAIN["desk_reconcile"], summary)
        return summary
