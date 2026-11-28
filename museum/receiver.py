"""Receiving museum: trust config, local ledger, filing, archival dossier."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from .authority import (
    Decision,
    TranslationStatement,
    authorize_stepwise,
    authorize_via_desk,
)
from .crypto import Key
from .issue import Museum
from .model import CollectionScope, Document, Item, Referral, Validity


ROUTE_STEP = "step"
ROUTE_DESK = "desk"


@dataclass
class LocalLedger:
    """The receiving museum's own catalog / accession ledger.

    Each physical item has a stable ``item_id``.  A call number
    (owner, series, seq) may be held in several subledgers (分册) and, in
    principle, in more than one copy, so lookups by call number can return
    several items.  Moving one item between subledgers therefore stays a
    separable operation on that item rather than an overwrite of a call number.
    """

    name: str
    _items: dict[str, Item] = field(default_factory=dict)
    _seq: int = 0

    def add(self, item: Item) -> str:
        self._seq += 1
        item_id = f"{self.name}:{self._seq}"
        self._items[item_id] = item
        return item_id

    def find(
        self, owner: str, series: str, seq: int,
        subledger: Optional[str] = None,
    ) -> list[Item]:
        return [
            i for i in self._items.values()
            if i.owner == owner and i.series == series and i.seq == seq
            and (subledger is None or i.subledger == subledger)
        ]

    def get(self, owner: str, series: str, seq: int, subledger: str) -> Optional[Item]:
        hits = self.find(owner, series, seq, subledger)
        return hits[0] if hits else None

    def in_subledger(self, subledger: str) -> list[Item]:
        return [i for i in self._items.values() if i.subledger == subledger]

    def move_subledger(self, owner: str, series: str, seq: int,
                       old_sub: str, new_sub: str) -> Item:
        """Move one item between subledgers at the same call number."""
        item = self.get(owner, series, seq, old_sub)
        if item is None:
            raise KeyError(f"no {series}-{seq:06d} in {old_sub} subledger")
        item.subledger = new_sub
        return item

    def rehome(self, owner: str, series: str, seq: int, subledger: str,
               new_owner: str, item_id: Optional[str] = None) -> Item:
        """Move an item from one museum's ledger to another's (same call no.)."""
        if item_id is None:
            item = self.get(owner, series, seq, subledger)
        else:
            item = self._items[item_id]
        if item is None:
            raise KeyError(f"no {series}-{seq:06d} in {subledger} subledger")
        item.owner = new_owner
        return item

    def all(self) -> list[Item]:
        return list(self._items.values())


@dataclass
class Dossier:
    """Everything needed to re-check a filing offline, retained on file."""

    decision: Decision
    document: Document
    statement: Optional[TranslationStatement] = None
    referrals: list[Referral] = field(default_factory=list)
    trusted_root_fps: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "decision": self.decision.to_json(),
            "document": self.document.to_json(),
            "statement": self.statement.to_json() if self.statement else None,
            "referrals": [r.to_json() for r in self.referrals],
            "trusted_root_fps": self.trusted_root_fps,
        }

    @classmethod
    def from_json(cls, obj: dict) -> "Dossier":
        """Rebuild a dossier from archived material only (no live services)."""
        from .authority import Decision, TranslationStatement

        d = obj["decision"]
        decision = Decision(
            accepted=d["accepted"],
            route=d["route"],
            reason=d["reason"],
            document_digest=d["document_digest"],
            accepted_name=d.get("accepted_name"),
            scope=CollectionScope.from_json(d["scope"]) if d.get("scope") else None,
            purposes=frozenset(d.get("purposes", [])),
            validity=Validity.from_json(d["validity"]) if d.get("validity") else None,
        )
        stmt = (
            TranslationStatement.from_json(obj["statement"])
            if obj.get("statement")
            else None
        )
        return cls(
            decision=decision,
            document=Document.from_json(obj["document"]),
            statement=stmt,
            referrals=[Referral.from_json(r) for r in obj.get("referrals", [])],
            trusted_root_fps=obj.get("trusted_root_fps", []),
        )


@dataclass
class Receiver(Museum):
    """A museum that also *receives* travelling files.

    Because a receiver may itself refer on (a back-reference in the graph), it
    inherits the full registry / referral-issuing capability of :class:`Museum`.
    """

    route: str = ROUTE_STEP
    trusted_root_fps: set[str] = field(default_factory=set)
    desk_pubkey_fp: Optional[str] = None
    desk_pubkey_der: bytes = b""
    ledger: LocalLedger = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.ledger is None:
            self.ledger = LocalLedger(self.name)

    # -- trust config --------------------------------------------------------
    def trust_root(self, museum) -> None:
        self.trusted_root_fps.add(museum.fp)

    def acknowledge_desk(self, desk) -> None:
        self.desk_pubkey_fp = desk.fp
        self.desk_pubkey_der = desk.key.public_der

    # -- filing --------------------------------------------------------------
    def file(
        self,
        document: Document,
        *,
        referrals: Iterable[Referral] = (),
        statement: Optional[TranslationStatement] = None,
    ) -> Dossier:
        """Decide a document and record it; never mutates the ledger on reject."""
        referrals = list(referrals)
        if self.route == ROUTE_STEP:
            decision = authorize_stepwise(document, self, referrals, self.trusted_root_fps)
        elif self.route == ROUTE_DESK:
            if statement is None:
                from .authority import Decision as D

                decision = D(False, "desk", "no desk statement supplied", document.digest())
            else:
                decision = authorize_via_desk(document, self, statement, self.desk_pubkey_der)
        else:
            raise ValueError(f"unknown route {self.route!r}")

        return Dossier(
            decision=decision,
            document=document,
            statement=statement,
            referrals=referrals,
            trusted_root_fps=sorted(self.trusted_root_fps),
        )

    def file_into_catalog(self, dossier: Dossier) -> Dossier:
        """If authorized, actually write the recognised item into the ledger."""
        if not dossier.decision.accepted:
            return dossier
        doc = dossier.document
        item = doc.item
        self.ledger.add(
            Item(
                owner=item.owner, series=item.series, seq=item.seq,
                subledger="accepted", title=item.title, width=item.width,
            )
        )
        return dossier

    def hold_for_evidence(self, dossier: Dossier) -> Dossier:
        """Keep the original file pending evidence; do not change the ledger."""
        return dossier

    # -- successor / offline re-check ----------------------------------------
    def recheck_dossier(self, dossier: Dossier) -> Decision:
        """Re-derive a decision from archived material alone (successor check).

        Uses only the dossier's own stored document, chain/statement and the
        receiver's trust configuration -- no live service required.
        """
        if self.route == ROUTE_STEP:
            return authorize_stepwise(
                dossier.document, self, dossier.referrals, self.trusted_root_fps
            )
        if dossier.statement is None:
            from .authority import Decision as D

            return D(False, "desk", "no desk statement in archive", dossier.document.digest())
        return authorize_via_desk(
            dossier.document, self, dossier.statement, self.desk_pubkey_der
        )
