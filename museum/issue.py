"""Issuance side: registries, librarian signing, referral & material minting."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .crypto import Key, b64, canonical_json, sha256_hex
from .model import (
    ALL_PURPOSES,
    CollectionScope,
    Document,
    OneTimeAcceptanceMaterial,
    Referral,
    Registration,
    Validity,
    iso,
)


def now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Museum:
    """A museum holds one local registry key and an in-memory registry."""

    name: str
    key: Key = field(default_factory=Key.generate)
    _registrations: dict[tuple[str, str], Registration] = field(default_factory=dict)

    @property
    def fp(self) -> str:
        return self.key.fp

    @property
    def pub_b64(self) -> str:
        return b64(self.key.public_der)

    # -- registry ------------------------------------------------------------
    def register(self, short_name: str, person_key: Key, issued_at: str | None = None) -> Registration:
        reg = Registration(
            museum=self.name,
            short_name=short_name,
            subject_fp=person_key.fp,
            subject_pubkey_der_b64=b64(person_key.public_der),
            issued_at=issued_at or iso(now()),
        )
        reg.signature_b64 = b64(self.key.sign(reg.signed_bytes()))
        self._registrations[(short_name, person_key.fp)] = reg
        return reg

    def registration_for(self, short_name: str, person_fp: str) -> Registration | None:
        return self._registrations.get((short_name, person_fp))

    # -- referral minting ----------------------------------------------------
    def issue_referral(
        self,
        *,
        audience: "Museum",
        subject_key: Key,
        from_name: str,
        to_name: str,
        scope: CollectionScope,
        purposes,
        validity: Validity,
        can_refer: bool = True,
        delegation_depth: int = 2,
        issued_at: str | None = None,
        nonce: str | None = None,
    ) -> Referral:
        """Issue a referral *under this museum's own name* for a subject.

        ``from_name`` is the subject's name in this (issuer / ``from_museum``)
        space; ``to_name`` is the name the audience (``to_museum``) will know
        the subject by.  The next hop must continue from ``to_museum`` /
        ``to_name``.  The subject must already be registered under
        ``from_name`` here; the embedded registration makes the name->key
        binding checkable offline by the audience.
        """
        reg = self.registration_for(from_name, subject_key.fp)
        if reg is None:
            raise ValueError(
                f"{from_name!r}/{subject_key.fp[:8]} is not registered at {self.name}"
            )
        purposes = frozenset(purposes)
        unknown = purposes - ALL_PURPOSES
        if unknown:
            raise ValueError(f"unknown purposes: {unknown}")
        ref = Referral(
            issuer_museum=self.name,
            issuer_key_fp=self.fp,
            issuer_pubkey_der_b64=self.pub_b64,
            audience_museum=audience.name,
            audience_key_fp=audience.fp,
            audience_pubkey_der_b64=audience.pub_b64,
            subject_fp=subject_key.fp,
            subject_pubkey_der_b64=b64(subject_key.public_der),
            from_museum=self.name,
            from_name=from_name,
            to_museum=audience.name,
            to_name=to_name,
            scope=scope,
            purposes=purposes,
            validity=validity,
            can_refer=can_refer,
            delegation_depth=delegation_depth,
            issued_at=issued_at or iso(now()),
            nonce=nonce or secrets.token_hex(16),
            registration=reg,
        )
        ref.signature_b64 = b64(self.key.sign(ref.signed_bytes()))
        return ref

    def issue_acceptance_material(
        self,
        *,
        station: str,
        document: Document,
        purpose: str,
        not_after: str,
        nonce: str | None = None,
    ) -> OneTimeAcceptanceMaterial:
        mat = OneTimeAcceptanceMaterial(
            station=station,
            document_digest=document.digest(),
            purpose=purpose,
            signer_fp=document.signer_fp,
            not_after=not_after,
            nonce=nonce or secrets.token_hex(16),
            issuer_pubkey_der_b64=self.pub_b64,
        )
        mat.signature_b64 = b64(self.key.sign(mat.signed_bytes()))
        return mat


@dataclass
class Librarian:
    """A person with a local drill signing key and a short display name."""

    short_name: str
    key: Key = field(default_factory=Key.generate)
    _used_material: set[str] = field(default_factory=set)

    @property
    def fp(self) -> str:
        return self.key.fp

    @property
    def pub_b64(self) -> str:
        return b64(self.key.public_der)

    def sign_document(
        self,
        *,
        doc_type: str,
        item,
        statement: str,
        signed_at: str | None = None,
        claimer_name: str | None = None,
    ) -> Document:
        doc = Document(
            doc_type=doc_type,
            item=item,
            claimer_name=claimer_name or self.short_name,
            statement=statement,
            signer_fp=self.fp,
            signer_pubkey_der_b64=self.pub_b64,
            signed_at=signed_at or iso(now()),
        )
        doc.signature_b64 = b64(self.key.sign(doc.signed_bytes()))
        return doc

    # -- using one-time acceptance material ---------------------------------
    def consume_material(self, mat: OneTimeAcceptanceMaterial, *, station: str,
                         document_digest: str, purpose: str, at: datetime) -> bool:
        """Try to spend station material.  Returns True only if it is valid for
        *this* librarian, *this* station, *this* file and *this* purpose, and
        has not been spent before."""
        from .model import parse_time

        if mat.nonce in self._used_material:
            return False
        if mat.station != station:
            return False
        if mat.document_digest != document_digest or mat.purpose != purpose:
            return False
        if mat.signer_fp != self.fp:
            return False
        if at > parse_time(mat.not_after):
            return False
        self._used_material.add(mat.nonce)
        return True
