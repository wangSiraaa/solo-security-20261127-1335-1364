"""Domain model: museums, people, collection scopes, referrals, documents.

Design notes
------------
* Person identity is the Ed25519 **key fingerprint**, never the short name.
  A short name may be registered by several museums and may be shared by
  several distinct people; only the fingerprint disambiguates them.
* A *collection scope* is an exact set of catalog items, expressed as
  per-series integer intervals.  Intervals give us precise ``subset`` and
  ``intersection`` operations, which is what makes "the scope may only shrink
  along a referral chain" enforceable instead of hand-wavy.
* A referral edge is a certificate-like object: it names an issuer (with its
  public key), the audience and the audience's public key it vouches for, the
  subject (librarian key), a name mapping, and a scope.  Everything needed to
  verify it offline travels inside it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Optional

from .crypto import b64, canonical_json, sha256_hex, unb64, verify_cached

# purposes -----------------------------------------------------------------

VERIFY_DESCRIPTION = "verify_description"  # 只允许核对说明
CLAIM_EXHIBIT = "claim_exhibit"            # 能签领展品
ALL_PURPOSES = frozenset({VERIFY_DESCRIPTION, CLAIM_EXHIBIT})

# document type -> the single purpose it needs
DOC_PURPOSE = {
    "catalog_correction": VERIFY_DESCRIPTION,
    "exhibit_claim": CLAIM_EXHIBIT,
}


def parse_time(text: str) -> datetime:
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# collection scopes --------------------------------------------------------

_CN = re.compile(r"^(?P<series>[A-Za-z]+)-(?P<digits>\d*)$")


def prefix_interval(series: str, digits: str, width: int = 6) -> tuple[str, int, int]:
    """Turn a catalog prefix such as ``COL-0000`` into an integer interval.

    Call numbers are ``<series>-<seq:0{width}d>``.  ``COL-0000`` therefore
    covers sequence numbers 0..9999 within series COL.
    """
    if len(digits) > width:
        raise ValueError(f"prefix {digits!r} longer than call-number width {width}")
    lo = int(digits) * (10 ** (width - len(digits))) if digits else 0
    hi = lo + (10 ** (width - len(digits)) - 1) if digits else 10 ** width - 1
    return (series, lo, hi)


def parse_prefix(text: str, width: int = 6) -> tuple[str, int, int]:
    m = _CN.match(text.strip())
    if not m:
        raise ValueError(f"bad collection prefix {text!r} (want SERIES-DIGITS)")
    return prefix_interval(m.group("series"), m.group("digits"), width)


def format_call_number(series: str, seq: int, width: int = 6) -> str:
    return f"{series}-{seq:0{width}d}"


def _merge(intervals: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    items = sorted(intervals)
    out: list[list[int]] = []
    for lo, hi in items:
        if lo > hi:
            continue
        if out and lo <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], hi)
        else:
            out.append([lo, hi])
    return [(a, b) for a, b in out]


def _subtract(base: list[tuple[int, int]], cut: list[tuple[int, int]]) -> list[tuple[int, int]]:
    result = list(base)
    for clo, chi in cut:
        nxt: list[tuple[int, int]] = []
        for lo, hi in result:
            if chi < lo or clo > hi:            # no overlap
                nxt.append((lo, hi))
                continue
            if lo < clo:
                nxt.append((lo, clo - 1))
            if hi > chi:
                nxt.append((chi + 1, hi))
        result = nxt
    return _merge(result)


@dataclass(frozen=True)
class CollectionScope:
    """A set of catalog items, keyed by owning museum, as per-series intervals."""

    owner: str
    per_series: dict[str, tuple[tuple[int, int], ...]]

    @classmethod
    def of(
        cls,
        owner: str,
        includes: Iterable[tuple[str, int, int]],
        excludes: Iterable[tuple[str, int, int]] = (),
    ) -> "CollectionScope":
        inc: dict[str, list[tuple[int, int]]] = {}
        for series, lo, hi in includes:
            inc.setdefault(series, []).append((lo, hi))
        exc: dict[str, list[tuple[int, int]]] = {}
        for series, lo, hi in excludes:
            exc.setdefault(series, []).append((lo, hi))
        per: dict[str, tuple[tuple[int, int], ...]] = {}
        for series, ivals in inc.items():
            merged = _merge(ivals)
            if series in exc:
                merged = _subtract(merged, _merge(exc[series]))
            if merged:
                per[series] = tuple(merged)
        return cls(owner=owner, per_series=per)

    def contains(self, owner: str, series: str, seq: int) -> bool:
        if owner != self.owner:
            return False
        for lo, hi in self.per_series.get(series, ()):
            if lo <= seq <= hi:
                return True
        return False

    def is_empty(self) -> bool:
        return not any(self.per_series.values())

    def is_subset_of(self, other: "CollectionScope") -> bool:
        if self.owner != other.owner:
            return False
        for series, ivals in self.per_series.items():
            mine = list(ivals)
            theirs = list(other.per_series.get(series, ()))
            for lo, hi in mine:
                need = lo
                for tlo, thi in theirs:
                    if thi < need:
                        continue
                    if tlo > need:
                        break
                    need = max(need, thi + 1)
                    if need > hi:
                        break
                if need <= hi:
                    return False
        return True

    def intersect(self, other: "CollectionScope") -> "CollectionScope":
        if self.owner != other.owner:
            return CollectionScope(self.owner, {})
        per: dict[str, tuple[tuple[int, int], ...]] = {}
        for series in set(self.per_series) & set(other.per_series):
            a = self.per_series[series]
            b = other.per_series[series]
            out: list[tuple[int, int]] = []
            for alo, ahi in a:
                for blo, bhi in b:
                    lo, hi = max(alo, blo), min(ahi, bhi)
                    if lo <= hi:
                        out.append((lo, hi))
            if out:
                per[series] = tuple(_merge(out))
        return CollectionScope(self.owner, per)

    def to_json(self) -> dict:
        return {
            "owner": self.owner,
            "includes": [
                {"series": s, "lo": lo, "hi": hi}
                for s, ivals in sorted(self.per_series.items())
                for lo, hi in ivals
            ],
        }

    @classmethod
    def from_json(cls, obj: dict) -> "CollectionScope":
        per: dict[str, list[tuple[int, int]]] = {}
        for iv in obj.get("includes", []):
            per.setdefault(iv["series"], []).append((int(iv["lo"]), int(iv["hi"])))
        return cls(owner=obj["owner"], per_series={k: tuple(_merge(v)) for k, v in per.items()})

    def describe(self) -> str:
        if self.is_empty():
            return f"{self.owner}: <empty>"
        parts = []
        for s, ivals in sorted(self.per_series.items()):
            seg = ", ".join(f"{s}-{lo:06d}..{hi:06d}" for lo, hi in ivals)
            parts.append(seg)
        return f"{self.owner}: " + "; ".join(parts)


@dataclass(frozen=True)
class Validity:
    start: str
    end: str

    def contains(self, moment: datetime) -> bool:
        return parse_time(self.start) <= moment <= parse_time(self.end)

    def is_subset_of(self, other: "Validity") -> bool:
        return (
            parse_time(self.start) >= parse_time(other.start)
            and parse_time(self.end) <= parse_time(other.end)
        )

    def to_json(self) -> dict:
        return {"from": self.start, "until": self.end}

    @classmethod
    def from_json(cls, obj: dict) -> "Validity":
        return cls(start=obj["from"], end=obj["until"])


# referrals ----------------------------------------------------------------


@dataclass
class Registration:
    """A registry attestation binding a short name to a person's key."""

    museum: str
    short_name: str
    subject_fp: str
    subject_pubkey_der_b64: str
    issued_at: str
    signature_b64: str = ""

    def body(self) -> dict:
        return {
            "kind": "registration",
            "museum": self.museum,
            "short_name": self.short_name,
            "subject_fp": self.subject_fp,
            "subject_pubkey_der_b64": self.subject_pubkey_der_b64,
            "issued_at": self.issued_at,
        }

    def signed_bytes(self) -> bytes:
        return canonical_json(self.body())

    def verify_under(self, registry_pubkey_der: bytes) -> bool:
        return verify_cached(registry_pubkey_der, self.signed_bytes(), unb64(self.signature_b64))


@dataclass
class Referral:
    """One edge of a step-by-step referral chain."""

    issuer_museum: str
    issuer_key_fp: str
    issuer_pubkey_der_b64: str
    audience_museum: str
    audience_key_fp: str
    audience_pubkey_der_b64: str
    subject_fp: str
    subject_pubkey_der_b64: str
    from_museum: str
    from_name: str
    to_museum: str
    to_name: str
    scope: CollectionScope
    purposes: frozenset[str]
    validity: Validity
    can_refer: bool
    delegation_depth: int
    issued_at: str
    nonce: str
    registration: Registration
    signature_b64: str = ""

    # -- signed body ---------------------------------------------------------
    def body(self) -> dict:
        return {
            "kind": "referral",
            "v": 1,
            "issuer_museum": self.issuer_museum,
            "issuer_key_fp": self.issuer_key_fp,
            "issuer_pubkey_der_b64": self.issuer_pubkey_der_b64,
            "audience_museum": self.audience_museum,
            "audience_key_fp": self.audience_key_fp,
            "audience_pubkey_der_b64": self.audience_pubkey_der_b64,
            "subject_fp": self.subject_fp,
            "subject_pubkey_der_b64": self.subject_pubkey_der_b64,
            "from_museum": self.from_museum,
            "from_name": self.from_name,
            "to_museum": self.to_museum,
            "to_name": self.to_name,
            "scope": self.scope.to_json(),
            "purposes": sorted(self.purposes),
            "validity": self.validity.to_json(),
            "can_refer": self.can_refer,
            "delegation_depth": self.delegation_depth,
            "issued_at": self.issued_at,
            "nonce": self.nonce,
            "registration": self.registration.body()
            | {"signature_b64": self.registration.signature_b64},
        }

    def signed_bytes(self) -> bytes:
        return canonical_json(self.body())

    def signature_ok(self) -> bool:
        pub = unb64(self.issuer_pubkey_der_b64)
        return (
            sha256_hex(pub) == self.issuer_key_fp
            and verify_cached(pub, self.signed_bytes(), unb64(self.signature_b64))
        )

    def registration_ok(self) -> bool:
        pub = unb64(self.issuer_pubkey_der_b64)
        reg = self.registration
        return (
            reg.museum == self.issuer_museum
            and reg.short_name == self.from_name
            and reg.subject_fp == self.subject_fp
            and reg.subject_pubkey_der_b64 == self.subject_pubkey_der_b64
            and reg.verify_under(pub)
        )

    def to_json(self) -> dict:
        return self.body() | {"signature_b64": self.signature_b64}

    @classmethod
    def from_json(cls, obj: dict) -> "Referral":
        reg = obj["registration"]
        return cls(
            issuer_museum=obj["issuer_museum"],
            issuer_key_fp=obj["issuer_key_fp"],
            issuer_pubkey_der_b64=obj["issuer_pubkey_der_b64"],
            audience_museum=obj["audience_museum"],
            audience_key_fp=obj["audience_key_fp"],
            audience_pubkey_der_b64=obj["audience_pubkey_der_b64"],
            subject_fp=obj["subject_fp"],
            subject_pubkey_der_b64=obj["subject_pubkey_der_b64"],
            from_museum=obj["from_museum"],
            from_name=obj["from_name"],
            to_museum=obj["to_museum"],
            to_name=obj["to_name"],
            scope=CollectionScope.from_json(obj["scope"]),
            purposes=frozenset(obj["purposes"]),
            validity=Validity.from_json(obj["validity"]),
            can_refer=obj["can_refer"],
            delegation_depth=obj["delegation_depth"],
            issued_at=obj["issued_at"],
            nonce=obj["nonce"],
            registration=Registration(
                museum=reg["museum"],
                short_name=reg["short_name"],
                subject_fp=reg["subject_fp"],
                subject_pubkey_der_b64=reg["subject_pubkey_der_b64"],
                issued_at=reg["issued_at"],
                signature_b64=reg.get("signature_b64", ""),
            ),
            signature_b64=obj.get("signature_b64", ""),
        )


# collections & documents --------------------------------------------------


@dataclass
class Item:
    owner: str           # museum that owns / catalogs the item
    series: str
    seq: int
    subledger: str       # 分册
    title: str
    width: int = 6

    @property
    def call_number(self) -> str:
        return format_call_number(self.series, self.seq, self.width)

    @property
    def key(self) -> tuple[str, str, int]:
        return (self.owner, self.series, self.seq)


@dataclass
class Document:
    doc_type: str           # catalog_correction | exhibit_claim
    item: Item
    claimer_name: str       # name the signer intends to act under
    statement: str
    signer_fp: str = ""
    signer_pubkey_der_b64: str = ""
    signature_b64: str = ""
    signed_at: str = ""

    def digest_input(self) -> dict:
        """The bytes a librarian signs (no signature / no signer-fp echo)."""
        return {
            "kind": "document",
            "doc_type": self.doc_type,
            "item_owner": self.item.owner,
            "item_call_number": self.item.call_number,
            "claimant_name": self.claimer_name,
            "statement": self.statement,
            "signed_at": self.signed_at,
        }

    def signed_bytes(self) -> bytes:
        return canonical_json(self.digest_input())

    def digest(self) -> str:
        return sha256_hex(self.signed_bytes())

    def verify(self) -> bool:
        if not self.signer_pubkey_der_b64 or not self.signature_b64:
            return False
        pub = unb64(self.signer_pubkey_der_b64)
        if sha256_hex(pub) != self.signer_fp:
            return False
        return verify_cached(pub, self.signed_bytes(), unb64(self.signature_b64))

    def to_json(self) -> dict:
        return {
            "kind": "document",
            "doc_type": self.doc_type,
            "item": {
                "owner": self.item.owner,
                "series": self.item.series,
                "seq": self.item.seq,
                "subledger": self.item.subledger,
                "title": self.item.title,
                "width": self.item.width,
            },
            "claimer_name": self.claimer_name,
            "statement": self.statement,
            "signer_fp": self.signer_fp,
            "signer_pubkey_der_b64": self.signer_pubkey_der_b64,
            "signature_b64": self.signature_b64,
            "signed_at": self.signed_at,
        }

    @classmethod
    def from_json(cls, obj: dict) -> "Document":
        it = obj["item"]
        return cls(
            doc_type=obj["doc_type"],
            item=Item(
                owner=it["owner"], series=it["series"], seq=it["seq"],
                subledger=it["subledger"], title=it["title"], width=it.get("width", 6),
            ),
            claimer_name=obj["claimer_name"],
            statement=obj["statement"],
            signer_fp=obj.get("signer_fp", ""),
            signer_pubkey_der_b64=obj.get("signer_pubkey_der_b64", ""),
            signature_b64=obj.get("signature_b64", ""),
            signed_at=obj.get("signed_at", ""),
        )


@dataclass
class OneTimeAcceptanceMaterial:
    """Station-bound, one-use material issued to carry a *specific* file.

    It is deliberately bound to (station, document digest, purpose, signer
    fingerprint, expiry, nonce).  Re-homing it at another museum cannot make a
    same-named person pass for the original signer: the station and fingerprint
    no longer match.
    """

    station: str
    document_digest: str
    purpose: str
    signer_fp: str
    not_after: str
    nonce: str
    issuer_pubkey_der_b64: str
    signature_b64: str = ""

    def body(self) -> dict:
        return {
            "kind": "one_time_acceptance",
            "station": self.station,
            "document_digest": self.document_digest,
            "purpose": self.purpose,
            "signer_fp": self.signer_fp,
            "not_after": self.not_after,
            "nonce": self.nonce,
            "issuer_pubkey_der_b64": self.issuer_pubkey_der_b64,
        }

    def signed_bytes(self) -> bytes:
        return canonical_json(self.body())

    def verify(self, issuer_pubkey_der: bytes) -> bool:
        return (
            unb64(self.issuer_pubkey_der_b64) == issuer_pubkey_der
            and verify_cached(issuer_pubkey_der, self.signed_bytes(), unb64(self.signature_b64))
        )

    def to_json(self) -> dict:
        return self.body() | {"signature_b64": self.signature_b64}

    @classmethod
    def from_json(cls, obj: dict) -> "OneTimeAcceptanceMaterial":
        return cls(
            station=obj["station"],
            document_digest=obj["document_digest"],
            purpose=obj["purpose"],
            signer_fp=obj["signer_fp"],
            not_after=obj["not_after"],
            nonce=obj["nonce"],
            issuer_pubkey_der_b64=obj["issuer_pubkey_der_b64"],
            signature_b64=obj.get("signature_b64", ""),
        )
