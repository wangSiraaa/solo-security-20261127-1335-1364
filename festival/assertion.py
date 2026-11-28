"""Path B: a trusted *local* proof service answers "did they meet the tier?".

Here the wallet keeps the birth date.  To transact it asks a trusted local proof
service (the same kind of local, trusted program that mints the drill identity)
to answer one question for one screening: "does this holder meet tier T on day
D?".  The service sees the DOB inside the local trust boundary, computes the
exact age on D, and signs an *assertion* that contains only:

    (program, day, tier, binding_pub, nullifier, result)

No birth date, no full-history holder number.  Because the assertion is derived
from the actual DOB against the actual date, it works for dates the desk never
pre-registered -- the property path A lacks.  When the service is unreachable,
the wallet can only fall back on assertions it already holds.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from . import crypto
from .crypto import DOMAIN
from .calendar import age_on


@dataclass(frozen=True)
class EligibilityAssertion:
    assertion_id: str
    program_id: str
    day: date
    tier: int
    binding_pub: crypto.PublicKey
    nullifier: str
    result: str  # "met-on-day"

    def payload(self) -> dict:
        return {
            "assertion_id": self.assertion_id,
            "program_id": self.program_id,
            "day": self.day.isoformat(),
            "tier": self.tier,
            "binding_pub": self.binding_pub.hex,
            "nullifier": self.nullifier,
            "result": self.result,
        }

    @classmethod
    def from_signed(cls, data: dict) -> tuple["EligibilityAssertion", dict]:
        assertion = cls(
            assertion_id=data["assertion_id"],
            program_id=data["program_id"],
            day=date.fromisoformat(data["day"]),
            tier=data["tier"],
            binding_pub=crypto.PublicKey.from_hex(data["binding_pub"]),
            nullifier=data["nullifier"],
            result=data["result"],
        )
        return assertion, data["sig"]


class LocalProofService:
    """Trusted local program.  Holds a private key; the DOB enters only here."""

    def __init__(self, service_id: str = "local-proof-svc"):
        self.service_id = service_id
        self.key = crypto.PrivateKey.generate()
        self.public = self.key.public
        self.online = True
        self._counter = 0

    def go_offline(self) -> None:
        self.online = False

    def bring_online(self) -> None:
        self.online = True

    def attest(
        self,
        dob: date,
        program_id: str,
        day: date,
        tier: int,
        binding_pub: crypto.PublicKey,
        nullifier: str,
    ) -> tuple[EligibilityAssertion, dict]:
        if not self.online:
            raise ConnectionError("local proof service is currently unreachable")
        if age_on(dob, day) < tier:
            raise PermissionError(
                f"holder does not meet tier {tier} on {day.isoformat()}"
            )
        self._counter += 1
        assertion = EligibilityAssertion(
            assertion_id=f"{self.service_id}-asrt-{self._counter:06d}",
            program_id=program_id,
            day=day,
            tier=tier,
            binding_pub=binding_pub,
            nullifier=nullifier,
            result="met-on-day",
        )
        sig = self.key.sign(DOMAIN["eligibility_assertion"], assertion.payload())
        return assertion, sig


def verify_assertion(
    service_pub: crypto.PublicKey,
    assertion: EligibilityAssertion,
    sig: dict,
    program_id: str,
    day: date,
    tier: int,
    binding_pub: crypto.PublicKey,
    nullifier: str,
) -> bool:
    if (assertion.day, assertion.tier) != (day, tier):
        return False
    if assertion.program_id != program_id:
        return False
    if assertion.binding_pub.value != binding_pub.value:
        return False
    if assertion.nullifier != nullifier:
        return False
    if assertion.result != "met-on-day":
        return False
    return crypto.verify(service_pub, DOMAIN["eligibility_assertion"], assertion.payload(), sig)
