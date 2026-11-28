"""Path A: pre-issued eligibility tokens.

The issuing desk, knowing only a *finite* set of screening dates and age tiers,
pre-signs one token per (date, tier) in advance.  A wallet that can prove it meets
the tier on the date may ask the desk for the corresponding token.  Verification
later is a single signature check: the door learns "this wallet cleared tier T on
date D" and nothing about the birthday.

Tokens are bound to the requesting wallet's *program-scoped* binding key at issue
time, so a token handed around cannot be used by a different purchaser, and the
binding key cannot be reused to correlate the holder in another programme.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from . import crypto
from .crypto import DOMAIN


@dataclass(frozen=True)
class PreIssuedToken:
    token_id: str
    program_id: str
    day: date
    tier: int
    binding_pub: crypto.PublicKey
    sig: dict

    def payload(self) -> dict:
        return {
            "token_id": self.token_id,
            "program_id": self.program_id,
            "day": self.day.isoformat(),
            "tier": self.tier,
            "binding_pub": self.binding_pub.hex,
        }

    def to_signed(self) -> dict:
        out = self.payload()
        out["sig"] = self.sig
        return out

    @classmethod
    def from_signed(cls, data: dict) -> "PreIssuedToken":
        return cls(
            token_id=data["token_id"],
            program_id=data["program_id"],
            day=date.fromisoformat(data["day"]),
            tier=data["tier"],
            binding_pub=crypto.PublicKey.from_hex(data["binding_pub"]),
            sig=data["sig"],
        )


class PreIssueAuthority:
    """Holds the private key that mints tokens for a fixed (dates x tiers) set."""

    def __init__(self, authority_id: str, program_id: str, dates: list[date], tiers: list[int]):
        self.authority_id = authority_id
        self.program_id = program_id
        self.key = crypto.PrivateKey.generate()
        self.public = self.key.public
        # The whole point: the mintable grid is finite and fixed up front.
        self.authorized = {(d, t) for d in dates for t in tiers}
        self._counter = 0

    def can_cover(self, day: date, tier: int) -> bool:
        return (day, tier) in self.authorized

    def issue(self, day: date, tier: int, binding_pub: crypto.PublicKey) -> PreIssuedToken:
        if not self.can_cover(day, tier):
            raise LookupError(
                f"no pre-issued token for {day.isoformat()} tier {tier}; "
                "the minting grid is fixed and does not include this screening"
            )
        self._counter += 1
        token_id = f"{self.authority_id}-tok-{self._counter:06d}"
        payload = {
            "token_id": token_id,
            "program_id": self.program_id,
            "day": day.isoformat(),
            "tier": tier,
            "binding_pub": binding_pub.hex,
        }
        sig = self.key.sign(DOMAIN["preissued_token"], payload)
        return PreIssuedToken(token_id, self.program_id, day, tier, binding_pub, sig)


def verify_token(
    authority_pub: crypto.PublicKey,
    token: PreIssuedToken,
    program_id: str,
    day: date,
    tier: int,
    binding_pub: crypto.PublicKey,
) -> bool:
    if token.program_id != program_id:
        return False
    if token.day != day or token.tier != tier:
        return False
    if token.binding_pub.value != binding_pub.value:
        return False
    return crypto.verify(authority_pub, DOMAIN["preissued_token"], token.payload(), token.sig)
