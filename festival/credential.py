"""The drill credential ("exercise ID") and its per-program public faces.

A trusted local program mints an :class:`ExerciseCredential` into the wallet.
The credential holds the birth date and the drill identity.  Everything the
wallet ever shows to a desk or a door is *program-scoped*:

* ``nullifier(program_id)`` -- a pseudonym derived from the drill ID and the
  programme id.  Same holder -> same handle across every desk of one programme
  (so the admission records line up), but a *different* handle in every other
  programme, so the same person cannot be correlated across programmes.
* ``program_key(program_id)`` -- a program-scoped signing key.  Stubs and
  eligibility are bound to it, so non-transferability holds without ever
  exposing a festival-wide holder key.

Neither value is derived from the DOB.  The DOB is used only *inside* the wallet
and inside the trusted local proof service (path B).
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import date

from . import crypto
from .calendar import age_on


@dataclass(frozen=True)
class ExerciseCredential:
    exercise_id: str          # the drill identity, e.g. "EX-0001"
    dob: date                 # NEVER leaves the wallet / local closure
    holder_key: crypto.PrivateKey = field(repr=False)

    # -- wallet-only operations ------------------------------------------ #
    def age_on(self, ref: date) -> int:
        return age_on(self.dob, ref)

    def meets(self, tier: int, ref: date) -> bool:
        return self.age_on(ref) >= tier

    # -- the rotating public handle -------------------------------------- #
    def nullifier(self, program_id: str) -> str:
        """Per-program pseudonym: same holder -> same handle within a programme.

        Derived only from the drill ID + programme id, never from the DOB, so it
        reveals nothing about the birthday and nothing across programmes.
        """
        return hashlib.sha256(
            b"festival/nullifier/v1\x00"
            + program_id.encode()
            + b"\x00"
            + self.exercise_id.encode()
        ).hexdigest()

    # -- the program-scoped signing key ---------------------------------- #
    def program_key(self, program_id: str) -> crypto.PrivateKey:
        material = (
            self.exercise_id.encode() + b"|" + str(self.holder_key.secret).encode()
        )
        return crypto.PrivateKey.derive(f"program-key/{program_id}", material)


class LocalCredentialIssuer:
    """The trusted local program that mints drill identities, offline.

    A "new viewer" receives a credential here.  The issuer picks the birth date
    (here, supplied by the exercise author) and a fresh holder key; it never calls
    any network service.
    """

    def __init__(self, issuer_id: str = "local-credential-issuer"):
        self.issuer_id = issuer_id
        self._n = 0

    def mint(self, dob: date) -> ExerciseCredential:
        self._n += 1
        exercise_id = f"EX-{self._n:04d}-{secrets.token_hex(3)}"
        return ExerciseCredential(
            exercise_id=exercise_id,
            dob=dob,
            holder_key=crypto.PrivateKey.generate(),
        )
