"""Verification side: referral-graph search, authorization, translation desk.

Two offline routes to let a receiving museum recognise a travelling librarian:

Route A -- step-by-step ("逐级引荐交给收件馆"):
    The receiver holds the referral edges plus its own trust roots and searches
    the graph itself from a trusted root to itself.  Name conversion, purpose,
    excluded ranges, scope and validity must all hold **on one and the same
    path**.

Route B -- trusted translation desk ("身份翻译处检查完整路径"):
    Both parties acknowledge a desk.  The desk verifies the whole path and
    signs a one-time statement limited to {station, document digest, purpose}.
    The receiver need not keep the origin chains, but if the desk is offline no
    new item can be re-credentialed.

Only one route is configured per receiver (``Receiver.route``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Optional

from .crypto import Key, b64, canonical_json, sha256_hex, unb64, verify_cached
from .model import (
    DOC_PURPOSE,
    CLAIM_EXHIBIT,
    VERIFY_DESCRIPTION,
    CollectionScope,
    Document,
    Referral,
    Validity,
    iso,
    parse_time,
)


# ---------------------------------------------------------------------------
# path composition


@dataclass
class Path:
    """A root -> receiver referral path with its composed authority."""

    edges: list[Referral]
    accepted_name: str
    subject_fp: str
    scope: CollectionScope
    purposes: frozenset[str]
    validity: Validity | None

    @property
    def origin(self) -> str:
        return self.edges[0].issuer_museum

    def describe(self) -> str:
        hops = " -> ".join(f"{e.issuer_museum}[{e.from_name}]" for e in self.edges)
        hops += f" -> {self.edges[-1].audience_museum}[{self.accepted_name}]"
        return f"{hops} | scope {self.scope.describe()} | purposes {sorted(self.purposes)}"


def _chain_ok(prev: Referral, nxt: Referral) -> bool:
    return (
        prev.audience_museum == nxt.issuer_museum
        and prev.audience_key_fp == nxt.issuer_key_fp
        and prev.to_museum == nxt.from_museum
        and prev.to_name == nxt.from_name
        and prev.subject_fp == nxt.subject_fp
        and prev.subject_pubkey_der_b64 == nxt.subject_pubkey_der_b64
    )


def _compose(edges: list[Referral], receiver) -> Optional[Path]:
    """Intersect scopes/purposes/validity; return None if empty/inconsistent."""
    scope = edges[0].scope
    purposes = edges[0].purposes
    start = parse_time(edges[0].validity.start)
    end = parse_time(edges[0].validity.end)
    for e in edges[1:]:
        scope = scope.intersect(e.scope)
        purposes = purposes & e.purposes
        start = max(start, parse_time(e.validity.start))
        end = min(end, parse_time(e.validity.end))
    if scope.is_empty() or not purposes or start > end:
        return None
    return Path(
        edges=list(edges),
        accepted_name=edges[-1].to_name,
        subject_fp=edges[-1].subject_fp,
        scope=scope,
        purposes=frozenset(purposes),
        validity=Validity(iso(start), iso(end)),
    )


class ReferralGraph:
    """Index of referral edges for offline path search."""

    def __init__(self, referrals: Iterable[Referral]):
        self.referrals = list(referrals)

    def find_paths(
        self,
        receiver,
        trusted_root_fps: set[str],
        *,
        subject_fp: str | None = None,
        max_depth: int = 8,
    ) -> list[Path]:
        """Enumerate every simple valid path root -> receiver.

        A path is *structurally* valid when every edge is genuine, the first
        issuer is a trusted root, key fingerprints chain, the name mapping
        chains, the subject is constant, and the receiver's key ends the path.
        Authority (scope/purpose/validity) is then the intersection along the
        path -- never a union, so back-references and overlapping name mappings
        can only shrink what is recognised.  We deliberately enumerate *all*
        paths, so one dead path never hides a referral that holds elsewhere.
        """
        # Mutable recursion over every edge.  A path only continues from an edge
        # to the next issuer whose name AND key fingerprint equal this edge's
        # audience name AND fingerprint (checked in _chain_ok), so same-named
        # museums are never conflated.
        results: list[Path] = []

        def genuine(edge: Referral) -> bool:
            return edge.signature_ok() and edge.registration_ok()

        def walk(chain: list[Referral], seen_keys: set[str], hop_budget: int):
            last = chain[-1]
            if (
                last.audience_museum == receiver.name
                and last.audience_key_fp == receiver.fp
                and last.audience_pubkey_der_b64 == receiver.pub_b64
            ):
                composed = _compose(chain, receiver)
                if composed is not None:
                    results.append(composed)
            # ``hop_budget`` is fixed by the trusted root edge and never grows,
            # so an intermediate cannot amplify how far delegation may chain.
            if len(chain) >= hop_budget:
                return
            for nxt in self.referrals:
                if nxt.issuer_key_fp in seen_keys:
                    continue
                if not genuine(nxt):
                    continue
                # The current edge must permit further delegation.
                if not last.can_refer:
                    continue
                if not _chain_ok(last, nxt):
                    continue
                seen_keys.add(nxt.issuer_key_fp)
                chain.append(nxt)
                walk(chain, seen_keys, hop_budget)
                chain.pop()
                seen_keys.discard(nxt.issuer_key_fp)

        roots = [
            r
            for r in self.referrals
            if r.issuer_key_fp in trusted_root_fps
            and r.issuer_key_fp != receiver.fp
            and genuine(r)
            and (subject_fp is None or r.subject_fp == subject_fp)
        ]
        for root in roots:
            hop_budget = min(max_depth, max(1, root.delegation_depth))
            walk([root], {root.issuer_key_fp}, hop_budget)
        return results


# ---------------------------------------------------------------------------
# per-document authorization


@dataclass
class Decision:
    accepted: bool
    route: str
    reason: str
    document_digest: str
    accepted_name: str | None = None
    scope: CollectionScope | None = None
    purposes: frozenset[str] = frozenset()
    validity: Validity | None = None
    path: Path | None = None
    candidates: list[Path] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "accepted": self.accepted,
            "route": self.route,
            "reason": self.reason,
            "document_digest": self.document_digest,
            "accepted_name": self.accepted_name,
            "scope": self.scope.to_json() if self.scope else None,
            "purposes": sorted(self.purposes),
            "validity": self.validity.to_json() if self.validity else None,
            "path": [e.to_json() for e in self.path.edges] if self.path else [],
        }


def _path_fully_covers(path: Path, doc: Document, need_purpose: str) -> tuple[bool, str]:
    if path.subject_fp != doc.signer_fp:
        return False, "subject fingerprint != signer fingerprint"
    if need_purpose not in path.purposes:
        return False, f"path purposes {sorted(path.purposes)} lack {need_purpose}"
    if path.validity is None or not path.validity.contains(parse_time(doc.signed_at)):
        return False, "document signed outside referral validity"
    if not path.scope.contains(doc.item.owner, doc.item.series, doc.item.seq):
        return False, f"scope {path.scope.describe()} does not cover item {doc.item.call_number}"
    return True, "ok"


def authorize_stepwise(
    document: Document,
    receiver,
    referrals: Iterable[Referral],
    trusted_root_fps: set[str],
) -> Decision:
    """Route A: search the graph and require ONE path to satisfy everything."""
    digest = document.digest()
    need = DOC_PURPOSE.get(document.doc_type)
    if need is None:
        return Decision(False, "step", f"unknown document type {document.doc_type!r}", digest)

    if not document.verify():
        return Decision(False, "step", "document signature invalid", digest)

    graph = ReferralGraph(referrals)
    paths = graph.find_paths(receiver, trusted_root_fps, subject_fp=document.signer_fp)
    if not paths:
        return Decision(False, "step", "no structurally valid referral path to receiver", digest)

    # A name is only borrowed from a path that also supplies authority: we look
    # for a single path that simultaneously provides name, purpose, scope and
    # validity.  We never splice a name from one path and a purpose from another.
    covering = []
    reasons = []
    for p in paths:
        ok, why = _path_fully_covers(p, document, need)
        if ok:
            covering.append(p)
        else:
            reasons.append(why)

    if not covering:
        return Decision(
            False, "step",
            "no single path both converts the name and grants the needed authority: "
            + "; ".join(sorted(set(reasons))),
            digest, candidates=paths,
        )

    # Deterministic choice: widest effective scope, then shortest path.
    covering.sort(key=lambda p: (-sum(len(v) for v in p.scope.per_series.values()), len(p.edges)))
    best = covering[0]
    return Decision(
        True, "step", "authorized by a single referral path", digest,
        accepted_name=best.accepted_name, scope=best.scope,
        purposes=best.purposes, validity=best.validity, path=best,
        candidates=paths,
    )


# ---------------------------------------------------------------------------
# translation desk


@dataclass
class TranslationStatement:
    """Desk-signed, one-use identity statement: station + digest + purpose."""

    station: str
    document_digest: str
    purpose: str
    subject_fp: str
    subject_pubkey_der_b64: str
    accepted_name: str
    scope: CollectionScope
    validity: Validity
    not_after: str
    nonce: str
    desk_pubkey_der_b64: str
    signature_b64: str = ""

    def body(self) -> dict:
        return {
            "kind": "translation_statement",
            "station": self.station,
            "document_digest": self.document_digest,
            "purpose": self.purpose,
            "subject_fp": self.subject_fp,
            "subject_pubkey_der_b64": self.subject_pubkey_der_b64,
            "accepted_name": self.accepted_name,
            "scope": self.scope.to_json(),
            "validity": self.validity.to_json(),
            "not_after": self.not_after,
            "nonce": self.nonce,
            "desk_pubkey_der_b64": self.desk_pubkey_der_b64,
        }

    def signed_bytes(self) -> bytes:
        return canonical_json(self.body())

    def verify(self, desk_pubkey_der: bytes) -> bool:
        pub = unb64(self.desk_pubkey_der_b64)
        if pub != desk_pubkey_der:
            return False
        return verify_cached(pub, canonical_json(self.body()), unb64(self.signature_b64))

    def to_json(self) -> dict:
        return self.body() | {"signature_b64": self.signature_b64}

    @classmethod
    def from_json(cls, obj: dict) -> "TranslationStatement":
        return cls(
            station=obj["station"],
            document_digest=obj["document_digest"],
            purpose=obj["purpose"],
            subject_fp=obj["subject_fp"],
            subject_pubkey_der_b64=obj["subject_pubkey_der_b64"],
            accepted_name=obj["accepted_name"],
            scope=CollectionScope.from_json(obj["scope"]),
            validity=Validity.from_json(obj["validity"]),
            not_after=obj["not_after"],
            nonce=obj["nonce"],
            desk_pubkey_der_b64=obj["desk_pubkey_der_b64"],
            signature_b64=obj.get("signature_b64", ""),
        )


@dataclass
class TranslationDesk:
    name: str
    key: Key = field(default_factory=Key.generate)

    @property
    def fp(self) -> str:
        return self.key.fp

    @property
    def pub_b64(self) -> str:
        return b64(self.key.public_der)

    def issue(
        self,
        *,
        document: Document,
        receiver,
        referral_path: Path,
        not_after: str,
        nonce: str | None = None,
    ) -> TranslationStatement:
        """Verify the full path, then sign a station+digest+purpose statement."""
        # The desk independently re-checks that the path really reaches this
        # receiver and that a single path supplies both name and authority.
        need = DOC_PURPOSE[document.doc_type]
        ok, why = _path_fully_covers(referral_path, document, need)
        if not ok:
            raise ValueError(f"desk refuses: {why}")
        last = referral_path.edges[-1]
        if not (
            last.audience_museum == receiver.name
            and last.audience_key_fp == receiver.fp
            and last.audience_pubkey_der_b64 == receiver.pub_b64
        ):
            raise ValueError("desk refuses: path does not terminate at the receiver")
        import secrets
        stmt = TranslationStatement(
            station=receiver.name,
            document_digest=document.digest(),
            purpose=need,
            subject_fp=document.signer_fp,
            subject_pubkey_der_b64=document.signer_pubkey_der_b64,
            accepted_name=referral_path.accepted_name,
            scope=referral_path.scope,
            validity=referral_path.validity,
            not_after=not_after,
            nonce=nonce or secrets.token_hex(16),
            desk_pubkey_der_b64=self.pub_b64,
        )
        stmt.signature_b64 = b64(self.key.sign(stmt.signed_bytes()))
        return stmt


def authorize_via_desk(
    document: Document,
    receiver,
    statement: TranslationStatement,
    desk_pubkey_der: bytes,
) -> Decision:
    """Route B: receiver checks only the desk's one-use statement."""
    digest = document.digest()
    need = DOC_PURPOSE.get(document.doc_type)
    if need is None:
        return Decision(False, "desk", f"unknown document type {document.doc_type!r}", digest)
    if not document.verify():
        return Decision(False, "desk", "document signature invalid", digest)
    if not statement.verify(desk_pubkey_der):
        return Decision(False, "desk", "desk statement signature invalid", digest)
    if statement.station != receiver.name:
        return Decision(False, "desk", "statement bound to another station", digest)
    if statement.document_digest != digest:
        return Decision(False, "desk", "statement bound to another document digest", digest)
    if statement.purpose != need:
        return Decision(False, "desk", f"statement purpose {statement.purpose} != {need}", digest)
    if statement.subject_fp != document.signer_fp:
        return Decision(False, "desk", "statement subject != signer", digest)
    if not statement.scope.contains(doc_owner := document.item.owner, document.item.series, document.item.seq):
        return Decision(False, "desk", f"statement scope {statement.scope.describe()} lacks item", digest)
    if not statement.validity.contains(parse_time(document.signed_at)):
        return Decision(False, "desk", "document signed outside statement validity", digest)
    if parse_time(document.signed_at) > parse_time(statement.not_after):
        return Decision(False, "desk", "statement expired before document time", digest)
    return Decision(
        True, "desk", "authorized by translation desk statement", digest,
        accepted_name=statement.accepted_name, scope=statement.scope,
        purposes=frozenset({statement.purpose}), validity=statement.validity,
    )
