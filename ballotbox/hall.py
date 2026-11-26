"""A single exhibition hall (展厅) with its own local inbox directory.

Isolation model used by the exercise:

* A hall is constructed with the *path* of its own directory and holds no
  reference to the other hall or to the desk's ledger.  Its clerk methods only
  ever touch ``self.ledger``; ``read_post`` refuses paths outside its directory
  ("经办人只能读本岗回执").
* A hall records ballots keyed by a random ``ballot_id`` and *never* stores a
  member number.  Receipt records carry ``token_id`` + ``ballot_id`` but not the
  choice; the choice lives in a separate ballot record (joined only by
  ``ballot_id``).  So even the hall alone cannot name a member.

Decision table for one delivery (see ``_decide``):

    authority EXCLUSIVE and it is *this* hall  -> FINAL 收妥回执 (counts at once,
                                                  works even while offline)
    authority EXCLUSIVE and it is *another* hall -> WRONG_HALL reject
    authority TRANSFER / not-yet-delegated (None) -> TEMPORARY 暂存回条
                                                  ("待确认"; counts only after a
                                                  successful hand-off before the
                                                  deadline, and never as a final
                                                  promise while the link is down)
    token revoked (loss EFFECTIVE)              -> VOID, no new ballot
    token already has a FINAL receipt           -> ALREADY_FINAL, no new ballot

A token that is already provisionally accepted can be *voided* by the desk when
another hall wins the hand-off, preserving "每人一票".
"""

from __future__ import annotations

import os
import secrets
from typing import Callable, Dict, List, Optional

from . import tokens as tokenlib
from .ledger import Ledger
from .model import (
    AUTH_EXCLUSIVE, AUTH_TRANSFER, BALLOT_COUNTED, BALLOT_PENDING, BALLOT_VOID,
    EVENT_CLOSED, EVENT_OPEN, RECEIPT_FINAL, RECEIPT_TEMPORARY, RECEIPT_VOID,
    Receipt, Rejected, SubmitResult, ForeignPost,
)


class Hall:
    def __init__(self, hall_id: str, directory: str,
                 online: bool = True,
                 clock: Optional[Callable[[], int]] = None,
                 control: Optional[object] = None):
        self.hall_id = hall_id
        self.directory = os.path.abspath(directory)
        self.online = online
        self._clock = clock or (lambda: 0)
        self.control = control  # ControlChannel or None
        self.ledger = Ledger(self.directory, name="hall.jsonl", clock=self._clock)
        # Desk->hall pushes (grants / revocations) are cached here.  This is the
        # *only* thing a hall knows about authority; while offline it cannot ask.
        self._cache: Dict[str, Dict] = {}
        self._closed = False
        # desk->hall pushes queued while offline; flushed by ControlChannel.
        self._pending_pushes: List = []

    # ==================================================================
    # clerk-only local access
    # ==================================================================
    def read_post(self, path: str) -> str:
        """Read a file, but only inside this hall's own directory."""
        real = os.path.abspath(path)
        if os.path.commonpath([real, self.directory]) != self.directory:
            raise ForeignPost(f"{path!r} is outside {self.hall_id}'s post")
        with open(real, "r", encoding="utf-8") as fh:
            return fh.read()

    def _emit(self, rtype: str, data: dict, ts: Optional[int] = None):
        return self.ledger.append(rtype, data, ts=ts)

    # ==================================================================
    # desk->hall pushes
    # ==================================================================
    def apply_grant(self, token_id: str, kind: Optional[str],
                    final_hall: Optional[str]) -> None:
        """Desk tells us a token's final-receipt authority."""
        slot = self._cache.setdefault(token_id, {})
        slot["authority_kind"] = kind
        slot["final_hall"] = final_hall
        slot.setdefault("revoked", False)
        slot.setdefault("already_final", False)

    def apply_revoke(self, token_id: str) -> None:
        """Desk tells us a loss is now EFFECTIVE: stop accepting this token."""
        slot = self._cache.setdefault(token_id, {})
        slot["revoked"] = True

    # ==================================================================
    # link state
    # ==================================================================
    def set_online(self, online: bool) -> None:
        if self.online == online:
            return
        self.online = online
        self._emit("link", {"online": online})

    def _consult_desk(self, token_id: str) -> Dict:
        """Return the authority view.  Live query if online, else last push."""
        if self.online and self.control is not None:
            return self.control.query(hall=self.hall_id,
                                      question="authority", token_id=token_id)
        cached = dict(self._cache.get(token_id, {}))
        cached.setdefault("authority_kind", None)
        cached.setdefault("final_hall", None)
        cached.setdefault("revoked", False)
        cached.setdefault("already_final", False)
        return cached

    # ==================================================================
    # delivery (投递)
    # ==================================================================
    def submit(self, slip: str, choice: str, event,
               receipt_hint: Optional[str] = None) -> SubmitResult:
        if self._closed or event.state == EVENT_CLOSED or self._event_closed():
            self._emit("reject", {"reason": "EVENT_CLOSED"})
            return SubmitResult(ok=False, reason="EVENT_CLOSED")

        # 1) authenticate BEFORE recording anything (covers wrong-event slips
        #    and forged/tampered slips -- nothing is written on failure)
        try:
            tok = tokenlib.verify_slip(slip, event)
        except Exception as exc:  # InvalidSignature / EventMismatch
            self._emit("reject", {"reason": type(exc).__name__,
                                  "detail": str(exc)})
            return SubmitResult(ok=False, reason=type(exc).__name__,
                                detail=str(exc))

        token_id = tok.token_id

        # 2) local idempotency: if this hall already holds a live receipt for the
        #    token, return it unchanged -- never a second ballot.
        seen = self.receipt_for(token_id)
        if seen is not None and seen.kind != RECEIPT_VOID:
            return SubmitResult(ok=True, receipt=seen, reason="idempotent")

        # 3) how may we treat this token?
        view = self._consult_desk(token_id)
        if view.get("revoked"):
            return self._void(tok, "loss effective")
        if view.get("already_final"):
            self._emit("reject", {"reason": "ALREADY_FINAL",
                                  "token_id": token_id})
            return SubmitResult(ok=False, reason="ALREADY_FINAL")

        kind = view.get("authority_kind")
        final_hall = view.get("final_hall")

        if kind == AUTH_EXCLUSIVE and final_hall != self.hall_id:
            self._emit("reject", {"reason": "WRONG_HALL",
                                  "token_id": token_id,
                                  "final_hall": final_hall})
            return SubmitResult(
                ok=False, reason="WRONG_HALL",
                detail=f"final authority held by {final_hall}; this is "
                       f"{self.hall_id}")

        # 4) decide FINAL / TEMPORARY / 待确认
        if kind == AUTH_EXCLUSIVE and final_hall == self.hall_id:
            return self._accept_final(tok, choice, event)
        if kind == AUTH_TRANSFER:
            # 流转证: either hall may receive first (暂存), counts only after a
            # hand-off before the deadline; never a final promise while offline.
            if not self.online:
                self._emit("defer", {"token_id": token_id,
                                     "reason": "transfer-offline"})
                return SubmitResult(
                    ok=False, reason="PENDING_CONFIRM",
                    detail="link down; transfer cannot be promised while "
                           "offline")
            return self._accept_temporary(tok, choice, event)
        # No final authority delegated yet (None): cannot issue even a 暂存回条.
        # Only "待确认" -- this is the "相同投递只能待确认" case.
        self._emit("defer", {"token_id": token_id,
                             "reason": "no-authority-yet"})
        return SubmitResult(
            ok=False, reason="PENDING_CONFIRM",
            detail="hall has not been granted final authority for this card; "
                   "delivery awaits confirmation")

    # -- accept: FINAL (counts immediately, works offline) --------------
    def _accept_final(self, tok, choice: str, event) -> SubmitResult:
        ballot_id = secrets.token_hex(12)
        at = self._clock()
        self._emit("ballot", {"ballot_id": ballot_id, "event_id": event.event_id,
                              "hall": self.hall_id, "choice": choice,
                              "status": BALLOT_COUNTED, "at": at})
        rec = Receipt(receipt_id=secrets.token_hex(8), token_id=tok.token_id,
                      ballot_id=ballot_id, hall=self.hall_id,
                      kind=RECEIPT_FINAL, at=at,
                      note="exclusive final receipt")
        self._emit("receipt", {**rec.as_dict()})
        if self.online and self.control is not None:
            self.control.notify(hall=self.hall_id, message="final",
                                token_id=tok.token_id, ballot_id=ballot_id,
                                receipt_id=rec.receipt_id, at=at)
        return SubmitResult(ok=True, receipt=rec)

    # -- accept: TEMPORARY (待确认, needs hand-off) ---------------------
    def _accept_temporary(self, tok, choice: str, event) -> SubmitResult:
        ballot_id = secrets.token_hex(12)
        at = self._clock()
        self._emit("ballot", {"ballot_id": ballot_id, "event_id": event.event_id,
                              "hall": self.hall_id, "choice": choice,
                              "status": BALLOT_PENDING, "at": at})
        note = ("provisional; awaits hand-off before deadline"
                if self.online else
                "provisional; link down, no final promise while offline")
        rec = Receipt(receipt_id=secrets.token_hex(8), token_id=tok.token_id,
                      ballot_id=ballot_id, hall=self.hall_id,
                      kind=RECEIPT_TEMPORARY, at=at, note=note)
        self._emit("receipt", {**rec.as_dict()})
        if self.online and self.control is not None:
            self.control.notify(hall=self.hall_id, message="temp",
                                token_id=tok.token_id, ballot_id=ballot_id,
                                receipt_id=rec.receipt_id, at=at)
        return SubmitResult(ok=True, receipt=rec)

    def _void(self, tok, why: str) -> SubmitResult:
        at = self._clock()
        self._emit("void", {"token_id": tok.token_id, "reason": why, "at": at})
        return SubmitResult(ok=False, reason="VOIDED", detail=why)

    # ==================================================================
    # desk-driven operations
    # ==================================================================
    def handoff(self, token_id: str, ballot_id: str,
                receipt_id: Optional[str] = None) -> Receipt:
        """Convert a temporary acceptance into a counted final receipt.

        The desk enforces the deadline; the hall just records the conversion.
        """
        rec = self.receipt_for(token_id)
        if rec is None or rec.kind != RECEIPT_TEMPORARY:
            raise Rejected("NOT_TEMPORARY", "no temporary receipt to hand off")
        at = self._clock()
        self._emit("handoff", {"token_id": token_id, "ballot_id": ballot_id,
                              "at": at})
        self._emit("ballot-status", {"ballot_id": ballot_id,
                                     "status": BALLOT_COUNTED, "at": at})
        final = Receipt(receipt_id=receipt_id or secrets.token_hex(8),
                        token_id=token_id, ballot_id=ballot_id,
                        hall=self.hall_id, kind=RECEIPT_FINAL, at=at,
                        note="handed off before deadline")
        # note: a second "receipt" record supersedes the temporary one
        self._emit("receipt", {**final.as_dict()})
        return final

    def apply_void_ballot(self, token_id: str, reason: str) -> bool:
        """Void *our* provisional ballot for ``token_id`` (lost a hand-off)."""
        rec = self.receipt_for(token_id)
        if rec is None or rec.ballot_id is None:
            return False
        at = self._clock()
        self._emit("ballot-status", {"ballot_id": rec.ballot_id,
                                     "status": BALLOT_VOID, "at": at})
        self._emit("void", {"token_id": token_id, "reason": reason, "at": at})
        return True

    def close(self, reason: str = "deadline") -> None:
        if self._closed:
            return
        self._closed = True
        self._emit("close", {"event_state": EVENT_CLOSED, "reason": reason})

    def sync(self) -> int:
        """Called by the clerk on reconnect: push local receipts to the desk,
        then ask it to reconcile (effectuate losses / clear hand-offs).

        Returns the number of receipt records pushed.  Idempotent at the desk.
        """
        if not self.online or self.control is None:
            raise Rejected("OFFLINE", f"{self.hall_id} has no link to the desk")
        pushed = 0
        for rec in self._receipt_records():
            self.control.notify(hall=self.hall_id, message="receipt-sync",
                                token_id=rec["token_id"],
                                ballot_id=rec.get("ballot_id"),
                                receipt_id=rec["receipt_id"],
                                kind=rec["kind"], at=rec["at"], note=rec.get("note", ""))
            pushed += 1
        self.control.query(hall=self.hall_id, question="sync")
        return pushed

    # ==================================================================
    # reading back (clerk's own post only)
    # ==================================================================
    def _event_closed(self) -> bool:
        return self._closed or any(r["type"] == "close" for r in self.ledger)

    def _receipt_records(self) -> List[dict]:
        return [r["data"] for r in self.ledger if r["type"] == "receipt"]

    def receipt_for(self, token_id: str) -> Optional[Receipt]:
        found = None
        for rec in self.ledger:
            d = rec["data"]
            if rec["type"] == "receipt" and d.get("token_id") == token_id:
                found = Receipt(receipt_id=d["receipt_id"],
                                token_id=d["token_id"],
                                ballot_id=d.get("ballot_id"),
                                hall=d["hall"], kind=d["kind"], at=d["at"],
                                note=d.get("note", ""))
            elif rec["type"] == "void" and d.get("token_id") == token_id:
                found = Receipt(receipt_id="", token_id=token_id,
                                ballot_id=None, hall=self.hall_id,
                                kind=RECEIPT_VOID, at=d.get("at", 0),
                                note=d.get("reason", ""))
        return found

    def ballot_status(self, ballot_id: str) -> Optional[str]:
        status = None
        for rec in self.ledger:
            if rec["type"] == "ballot" and rec["data"]["ballot_id"] == ballot_id:
                status = rec["data"]["status"]
            elif rec["type"] == "ballot-status" and rec["data"]["ballot_id"] == ballot_id:
                status = rec["data"]["status"]
        return status

    def lookup_ballot(self, ballot_id: str) -> Optional[dict]:
        """Look up an *anonymous* ballot by its id (no member, no token)."""
        for rec in self.ledger:
            d = rec["data"]
            if rec["type"] == "ballot" and d["ballot_id"] == ballot_id:
                out = dict(d)
                out["status"] = self.ballot_status(ballot_id)
                return out
        return None

    def ballots(self) -> List[dict]:
        status: Dict[str, str] = {}
        rows: List[dict] = []
        for rec in self.ledger:
            d = rec["data"]
            if rec["type"] == "ballot":
                rows.append(dict(d))
                status[d["ballot_id"]] = d["status"]
            elif rec["type"] == "ballot-status":
                status[d["ballot_id"]] = d["status"]
        out = []
        for row in rows:
            row["status"] = status.get(row["ballot_id"], row.get("status"))
            out.append(row)
        return out

    def recount(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for b in self.ballots():
            if b["status"] == BALLOT_COUNTED:
                counts[b["choice"]] = counts.get(b["choice"], 0) + 1
        return counts

    def summary(self) -> Dict[str, object]:
        bs = self.ballots()
        return {
            "hall": self.hall_id,
            "online": self.online,
            "ballots_total": len(bs),
            "ballots_counted": sum(1 for b in bs if b["status"] == BALLOT_COUNTED),
            "ballots_pending": sum(1 for b in bs if b["status"] == BALLOT_PENDING),
            "ballots_void": sum(1 for b in bs if b["status"] == BALLOT_VOID),
            "receipts": len(self._receipt_records()),
            "chain": self.ledger.verify_chain(),
        }


__all__ = ["Hall"]
