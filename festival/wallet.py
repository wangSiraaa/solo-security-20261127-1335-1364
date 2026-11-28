"""The holder's wallet.

Holds the drill credential (and hence the birth date) on the wallet side, plus
whichever eligibility material that holder is entitled to under the chosen path.
The wallet uses a *program-scoped* binding key, so stubs and eligibility can be
pinned to this wallet without exposing a festival-wide handle.  It is the only
place (besides the trusted local proof service) the DOB is ever used.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from . import crypto
from .assertion import EligibilityAssertion, LocalProofService
from .calendar import Program
from .credential import ExerciseCredential
from .preissue import PreIssueAuthority, PreIssuedToken


class NotEligible(Exception):
    pass


class EligibilityUnavailable(Exception):
    """We cannot produce a usable proof for this screening with what we hold."""


@dataclass
class PurchasedTicket:
    ticket_id: str
    program_id: str
    program_fingerprint: str
    binding_key: crypto.PrivateKey = field(repr=False)
    stub_secrets: list[bytes] = field(default_factory=list)
    presentation: list[dict] = field(default_factory=list, repr=False)

    @property
    def binding_pub(self) -> crypto.PublicKey:
        return self.binding_key.public


class Wallet:
    def __init__(self, credential: ExerciseCredential, mode: str):
        assert mode in ("preissue", "assertion")
        self.cred = credential
        self.mode = mode
        self.tokens: dict[tuple[date, int], PreIssuedToken] = {}
        self.assertions: dict[tuple[str, date, int], tuple[EligibilityAssertion, dict]] = {}
        self.tickets: dict[str, PurchasedTicket] = {}

    # ---- per-program public face --------------------------------------- #
    def nullifier(self, program_id: str) -> str:
        return self.cred.nullifier(program_id)

    def binding_key(self, program_id: str) -> crypto.PrivateKey:
        return self.cred.program_key(program_id)

    # ---- path A: pre-issued tokens ------------------------------------- #
    def obtain_preissued(self, authority: PreIssueAuthority, program_id: str, day: date, tier: int) -> PreIssuedToken:
        if self.mode != "preissue":
            raise EligibilityUnavailable("wallet not configured for pre-issued tokens")
        if not self.cred.meets(tier, day):
            raise NotEligible("holder does not meet the tier on that day")
        if not authority.can_cover(day, tier):
            raise EligibilityUnavailable("the pre-issue grid has no token for this date/tier")
        binding = self.binding_key(program_id).public
        token = authority.issue(day, tier, binding)
        self.tokens[(day, tier)] = token
        return token

    def has_preissued(self, day: date, tier: int) -> bool:
        return (day, tier) in self.tokens

    # ---- path B: local proof service ----------------------------------- #
    def prefetch_assertion(self, service: LocalProofService, program_id: str, day: date, tier: int):
        assertion, sig = service.attest(
            self.cred.dob, program_id, day, tier,
            self.binding_key(program_id).public, self.nullifier(program_id),
        )
        self.assertions[(program_id, day, tier)] = (assertion, sig)
        return assertion, sig

    def obtain_assertion(self, service: LocalProofService, program_id: str, day: date, tier: int):
        if self.mode != "assertion":
            raise EligibilityUnavailable("wallet not configured for proof-service assertions")
        if not self.cred.meets(tier, day):
            raise NotEligible("holder does not meet the tier on that day")
        try:
            return self.prefetch_assertion(service, program_id, day, tier)
        except ConnectionError as exc:
            held = self.assertions.get((program_id, day, tier))
            if held is not None:
                return held
            raise EligibilityUnavailable(str(exc)) from exc

    # ---- unified "prove eligibility for this screening" ---------------- #
    def eligibility_for(self, program: Program, index: int, *, authority=None, service=None) -> dict:
        """Return door-verifiable eligibility material for one screening."""
        s = program.screening(index)
        # Modes differ only in *how* eligibility is proved; the age itself is
        # always checked wallet-side first, so an ineligible holder is refused
        # identically on either path.
        if not self.cred.meets(s.age_tier, s.date):
            raise NotEligible(
                f"holder is {self.cred.age_on(s.date)} on {s.date.isoformat()}, "
                f"below tier {s.age_tier}"
            )
        if self.mode == "preissue":
            if not self.has_preissued(s.date, s.age_tier):
                raise EligibilityUnavailable("no pre-fetched token for this screening")
            return {
                "kind": "preissue",
                "token": self.tokens[(s.date, s.age_tier)],
                "nullifier": self.nullifier(program.program_id),
                "binding_pub": self.binding_key(program.program_id).public,
            }
        else:
            if service is None:
                raise EligibilityUnavailable("local proof service unavailable")
            try:
                assertion, sig = self.obtain_assertion(service, program.program_id, s.date, s.age_tier)
            except NotEligible:
                raise
            except EligibilityUnavailable:
                raise
            return {
                "kind": "assertion",
                "assertion": assertion,
                "assertion_sig": sig,
                "nullifier": self.nullifier(program.program_id),
                "binding_pub": self.binding_key(program.program_id).public,
            }

    # ---- signing stubs (non-transferability) --------------------------- #
    def sign_stub_binding(self, program_id: str, stub_payload: dict) -> dict:
        return self.binding_key(program_id).sign("festival/stub-binding/v1", stub_payload)
