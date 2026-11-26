"""The membership desk (会员台).

Owns: eligible members, the *member -> token* linkage, the final-receipt
authority grants, and loss (挂失) state.  It **never** stores a member's choice:
hall notifications carry ``ballot_id`` (an opaque random handle) but not the
choice, and the desk ledger has no choice column.  So "会员台不能从自己留存的
记录查到谁选了什么" holds structurally.

Answering a hall's ``authority`` question is the desk's job while the link is
up.  When a hall is offline it cannot ask, and the desk cannot push: the hall
falls back to the authority it was granted *before* the outage (see hall.py).

Loss semantics
--------------

* authority EXCLUSIVE to hall H and H online      -> loss EFFECTIVE at once;
                                                     H is told to revoke.
* authority EXCLUSIVE to hall H and H offline      -> loss PENDING ("挂失待其
                                                     交接"); the desk must not
                                                      claim it is already in
                                                      force.  It becomes
                                                      EFFECTIVE once H syncs --
                                                      unless H already issued a
                                                      FINAL receipt in the
                                                      meantime, in which case the
                                                      cast vote stands (每人一票)
                                                      and the loss is CONSUMED.
* authority TRANSFER / not delegated               -> loss EFFECTIVE at once;
                                                     provisional ballots in both
                                                     halls are voided.

"先挂失" and "先收妥" therefore produce different, observable outcomes.
"""

from __future__ import annotations

import secrets
from typing import Dict, List, Optional

from . import tokens as tokenlib
from .control import ControlChannel
from .hall import Hall
from .ledger import Ledger
from .model import (
    AUTH_EXCLUSIVE, AUTH_TRANSFER, EVENT_CLOSED, EVENT_OPEN,
    LOSS_EFFECTIVE, LOSS_NONE, LOSS_PENDING, RECEIPT_FINAL, RECEIPT_TEMPORARY,
    AlreadyIssued, Event, LossResult, NotAllowed, Rejected, Token, TokenRecord,
)


class Desk:
    def __init__(self, directory: str, clock=None):
        import os
        self.directory = os.path.abspath(directory)
        os.makedirs(self.directory, exist_ok=True)
        self._clock = clock or (lambda: 0)
        self.ledger = Ledger(self.directory, name="desk.jsonl", clock=self._clock)
        self.members: set = set()
        self.tokens: Dict[str, TokenRecord] = {}
        self._by_member: Dict[tuple, str] = {}
        self.events: Dict[str, Event] = {}
        self.halls: Dict[str, Hall] = {}
        self.control = ControlChannel(self)
        self._seen_receipts: set = set()

    # ==================================================================
    # setup
    # ==================================================================
    def add_member(self, member_id: str) -> None:
        if member_id in self.members:
            return
        self.members.add(member_id)
        self.ledger.append("member", {"member_id": member_id})

    def is_eligible(self, member_id: str) -> bool:
        return member_id in self.members

    def register_event(self, event_id: str, choices, deadline=None,
                       key: Optional[bytes] = None) -> Event:
        ev = Event(event_id=event_id, choices=tuple(choices),
                   key=key or secrets.token_bytes(32), deadline=deadline)
        self.events[event_id] = ev
        self.ledger.append("event", {"event_id": event_id,
                                     "choices": list(ev.choices),
                                     "deadline": deadline})
        return ev

    def register_hall(self, hall: Hall) -> None:
        hall.control = self.control
        self.halls[hall.hall_id] = hall
        self.ledger.append("hall", {"hall_id": hall.hall_id})

    def _hall(self, hall_id: str) -> Hall:
        if hall_id not in self.halls:
            raise Rejected("UNKNOWN_HALL", hall_id)
        return self.halls[hall_id]

    # ==================================================================
    # card issuance (领证)
    # ==================================================================
    def issue(self, member_id: str, event_id: str) -> Token:
        if not self.is_eligible(member_id):
            raise Rejected("NOT_ELIGIBLE", member_id)
        ev = self.events.get(event_id)
        if ev is None:
            raise Rejected("UNKNOWN_EVENT", event_id)
        if ev.state == EVENT_CLOSED:
            raise Rejected("EVENT_CLOSED", event_id)
        key = (member_id, event_id)
        if key in self._by_member:
            raise AlreadyIssued(member_id)
        tok = tokenlib.issue_token(ev)
        rec = TokenRecord(token_id=tok.token_id, member_id=member_id,
                          event_id=event_id)
        self.tokens[tok.token_id] = rec
        self._by_member[key] = tok.token_id
        # desk ledger stores member<->token but NO choice.
        self.ledger.append("issue", {"member_id": member_id,
                                     "token_id": tok.token_id,
                                     "event_id": event_id})
        return tok

    # ==================================================================
    # authority grants (最终接收权 / 跨厅流转资格)
    # ==================================================================
    def grant_final_authority(self, token_id: str, hall_id: str) -> None:
        """Give one hall exclusive final-receipt authority for a card."""
        rec = self._rec(token_id)
        if hall_id not in self.halls:
            raise Rejected("UNKNOWN_HALL", hall_id)
        if rec.finalized:
            raise NotAllowed("card already finalized")
        rec.authority = (AUTH_EXCLUSIVE, hall_id)
        rec.granted_at = self._clock()
        self.ledger.append("grant", {"token_id": token_id,
                                     "kind": AUTH_EXCLUSIVE,
                                     "final_hall": hall_id})
        for hid in self.halls:
            self.control.push(hid, "grant", token_id=token_id,
                              kind=AUTH_EXCLUSIVE, final_hall=hall_id)

    def keep_transfer(self, token_id: str) -> None:
        """Keep cross-hall transfer eligibility (no single final authority)."""
        rec = self._rec(token_id)
        if rec.finalized:
            raise NotAllowed("card already finalized")
        rec.authority = AUTH_TRANSFER
        rec.granted_at = self._clock()
        self.ledger.append("grant", {"token_id": token_id,
                                     "kind": AUTH_TRANSFER,
                                     "final_hall": None})
        for hid in self.halls:
            self.control.push(hid, "grant", token_id=token_id,
                              kind=AUTH_TRANSFER, final_hall=None)

    # ==================================================================
    # loss / void (挂失)
    # ==================================================================
    def report_loss(self, token_id: str) -> LossResult:
        rec = self._rec(token_id)
        now = self._clock()

        # Already counted: the ballot stands, but the card cannot be reused.
        if rec.finalized:
            if rec.loss_requested_at is None:
                rec.loss_requested_at = now
                rec.loss_note = "loss reported after ballot counted; vote stands"
                self.ledger.append("loss-after-final",
                                   {"token_id": token_id, "at": now,
                                    "ballot_id": rec.consumed_ballot})
            return LossResult(token_id=token_id, effective=False, pending=False,
                              state="CONSUMED", effective_at=rec.finalized_at,
                              reason="ballot already counted; vote stands")

        if rec.loss_state == LOSS_EFFECTIVE:
            return LossResult(token_id=token_id, effective=True, pending=False,
                              state=LOSS_EFFECTIVE,
                              effective_at=rec.loss_effective_at,
                              reason="already effective")

        rec.loss_requested_at = now
        kind = rec.authority[0] if isinstance(rec.authority, tuple) else rec.authority
        final_hall = rec.authority[1] if isinstance(rec.authority, tuple) else None

        if kind == AUTH_EXCLUSIVE and self.halls.get(final_hall) is not None \
                and not self.halls[final_hall].online:
            # Cannot reach the authority hall: only a pending loss is honest.
            rec.loss_state = LOSS_PENDING
            rec.loss_note = f"pending; awaits hand-off from {final_hall}"
            self.ledger.append("loss-pending",
                               {"token_id": token_id, "at": now,
                                "authority_hall": final_hall})
            return LossResult(token_id=token_id, effective=False, pending=True,
                              state=LOSS_PENDING, effective_at=None,
                              reason=f"authority hall {final_hall} offline; "
                                     f"loss pending its hand-off")

        # Reachable (online authority hall, or transfer/none): effect now.
        self._make_loss_effective(rec, now, note="immediate")
        return LossResult(token_id=token_id, effective=True, pending=False,
                          state=LOSS_EFFECTIVE, effective_at=now,
                          reason="loss effective")

    def _make_loss_effective(self, rec: TokenRecord, now: int, note: str) -> None:
        rec.loss_state = LOSS_EFFECTIVE
        rec.loss_effective_at = now
        rec.loss_note = note
        self.ledger.append("loss-effective",
                           {"token_id": rec.token_id, "at": now, "note": note})
        # Only the hall that could still accept this card needs the revoke
        # push; transfer/undelegated cards are revoked everywhere.
        kind = rec.authority[0] if isinstance(rec.authority, tuple) \
            else rec.authority
        final_hall = rec.authority[1] if isinstance(rec.authority, tuple) \
            else None
        targets = [final_hall] if (kind == AUTH_EXCLUSIVE
                                   and final_hall in self.halls) else list(self.halls)
        for hid in targets:
            self.control.push(hid, "revoke", token_id=rec.token_id)
        self._void_provisionals(rec.token_id, "loss effective")

    def loss_conclusion(self, token_id: str) -> Dict:
        rec = self._rec(token_id)
        # A loss reported *after* a counted ballot is classified as CONSUMED even
        # though the stored loss_state stays NONE (nothing was ever effective).
        state = rec.loss_state
        if rec.finalized and rec.loss_requested_at is not None \
                and state == LOSS_NONE:
            state = "CONSUMED"
        return {
            "token_id": token_id,
            "loss_state": state,
            "loss_requested_at": rec.loss_requested_at,
            "loss_effective_at": rec.loss_effective_at,
            "finalized": rec.finalized,
            "finalized_hall": rec.finalized_hall,
            "finalized_at": rec.finalized_at,
            "ballot_id": rec.consumed_ballot,
            "note": rec.loss_note,
        }

    # ==================================================================
    # desk <-> hall plumbing (called by ControlChannel)
    # ==================================================================
    def _answer(self, hall_id: str, question: str, **kw) -> Dict:
        if question == "authority":
            rec = self.tokens.get(kw["token_id"])
            if rec is None:
                return {"known": False, "authority_kind": None,
                        "final_hall": None, "revoked": False,
                        "already_final": False}
            kind = rec.authority[0] if isinstance(rec.authority, tuple) \
                else rec.authority
            final_hall = rec.authority[1] if isinstance(rec.authority, tuple) \
                else None
            return {
                "known": True,
                "authority_kind": kind,
                "final_hall": final_hall,
                "revoked": rec.loss_state == LOSS_EFFECTIVE,
                "already_final": rec.finalized,
            }
        if question == "sync":
            return self._reconcile(hall_id)
        raise Rejected("UNKNOWN_QUESTION", question)

    def _receive(self, hall_id: str, message: str, **kw) -> None:
        if message == "final":
            self._on_final(hall_id, **kw)
        elif message == "temp":
            self._on_temp(hall_id, **kw)
        elif message == "receipt-sync":
            self._on_receipt_sync(hall_id, **kw)
        else:
            raise Rejected("UNKNOWN_MESSAGE", message)

    def _on_final(self, hall_id, token_id, ballot_id, receipt_id, at,
                  **_ignored) -> None:
        if receipt_id in self._seen_receipts:
            return
        self._seen_receipts.add(receipt_id)
        rec = self.tokens.get(token_id)
        if rec is None:
            return
        self.ledger.append("final", {"token_id": token_id,
                                     "ballot_id": ballot_id,
                                     "hall": hall_id,
                                     "receipt_id": receipt_id, "at": at})
        # a FINAL receipt supersedes any provisional acceptances
        self._void_provisionals(token_id, "final receipt received", skip_hall=hall_id)
        if not rec.finalized:
            rec.finalized = True
            rec.finalized_hall = hall_id
            rec.finalized_at = at
            rec.consumed_ballot = ballot_id
        if rec.loss_state == LOSS_PENDING:
            # vote was cast before the loss became effective -> it stands
            rec.loss_note = "loss requested but ballot already counted"

    def _on_temp(self, hall_id, token_id, ballot_id, receipt_id, at,
                 **_ignored) -> None:
        if receipt_id in self._seen_receipts:
            return
        self._seen_receipts.add(receipt_id)
        rec = self.tokens.get(token_id)
        if rec is None:
            return
        self.ledger.append("temp", {"token_id": token_id,
                                    "ballot_id": ballot_id,
                                    "hall": hall_id,
                                    "receipt_id": receipt_id, "at": at})
        # Replay safety: if this temporary acceptance is the one that later
        # became the counted ballot, it is already final -- do not void it.
        if rec.finalized and rec.consumed_ballot == ballot_id:
            return
        rec.provisionals[hall_id] = {"ballot_id": ballot_id,
                                     "receipt_id": receipt_id, "at": at}
        if rec.finalized or rec.loss_state == LOSS_EFFECTIVE:
            # arrived too late -> tell that hall to void it
            self._hall(hall_id).apply_void_ballot(token_id,
                                                  "received after close/loss")
            rec.provisionals.pop(hall_id, None)

    def _on_receipt_sync(self, hall_id, token_id, ballot_id, receipt_id, kind,
                         at, note="", **_ignored) -> None:
        # replays of a hall's own post; idempotent because receipt ids dedup.
        if kind == RECEIPT_FINAL:
            self._on_final(hall_id, token_id=token_id, ballot_id=ballot_id,
                           receipt_id=receipt_id, at=at)
        elif kind == RECEIPT_TEMPORARY:
            self._on_temp(hall_id, token_id=token_id, ballot_id=ballot_id,
                          receipt_id=receipt_id, at=at)
        # VOID notices from a hall are recorded via its local ledger; nothing
        # new to apply at the desk.

    # ==================================================================
    # reconciliation on reconnect
    # ==================================================================
    def set_hall_online(self, hall_id: str, online: bool) -> None:
        hall = self._hall(hall_id)
        hall.set_online(online)
        if online:
            self.control.flush(hall_id)  # deliver queued grants/revokes

    def _reconcile(self, hall_id: str) -> Dict:
        # 1) decide pending losses: now that the authority hall is reachable,
        #    either the vote was counted (receipt-sync already recorded) or the
        #    loss can take effect.
        changed = []
        for rec in self.tokens.values():
            if rec.loss_state != LOSS_PENDING:
                continue
            kind = rec.authority[0] if isinstance(rec.authority, tuple) \
                else rec.authority
            final_hall = rec.authority[1] if isinstance(rec.authority, tuple) \
                else None
            auth_hall = self.halls.get(final_hall)
            if kind == AUTH_EXCLUSIVE and auth_hall is not None \
                    and not auth_hall.online:
                continue
            if rec.finalized:
                # The vote was cast before the hand-off; it stands, and the
                # card is now dead.  Record *when* the loss took effect (the
                # moment the authority hall handed off).
                now = self._clock()
                rec.loss_state = LOSS_EFFECTIVE
                rec.loss_effective_at = now
                rec.loss_note = "ballot counted before hand-off; vote stands"
                self.ledger.append("loss-effective",
                                   {"token_id": rec.token_id,
                                    "at": now,
                                    "note": "late loss; ballot counted"})
                changed.append((rec.token_id, "counted"))
            else:
                self._make_loss_effective(rec, self._clock(),
                                          note="after reconnect")
                changed.append((rec.token_id, "effective"))
            self.control.push(hall_id, "revoke", token_id=rec.token_id)
        return {"hall": hall_id, "changed": changed}

    def _void_provisionals(self, token_id: str, reason: str,
                           skip_hall: Optional[str] = None) -> None:
        rec = self.tokens.get(token_id)
        if rec is None:
            return
        for hid, info in list(rec.provisionals.items()):
            if hid == skip_hall:
                continue
            hall = self.halls.get(hid)
            if hall is not None:
                hall.apply_void_ballot(token_id, reason)
            self.ledger.append("void-provisional",
                               {"token_id": token_id, "hall": hid,
                                "ballot_id": info.get("ballot_id"),
                                "reason": reason})
            rec.provisionals.pop(hid, None)

    # ==================================================================
    # hand-off (交接): commit one provisional to a final counted ballot
    # ==================================================================
    def commit_handoff(self, token_id: str, hall_id: str) -> dict:
        rec = self._rec(token_id)
        ev = self.events[rec.event_id]
        if ev.state == EVENT_CLOSED:
            raise Rejected("EVENT_CLOSED",
                           "hand-off after deadline cannot count")
        if rec.finalized:
            raise Rejected("ALREADY_FINAL", "card already counted")
        if rec.loss_state == LOSS_EFFECTIVE:
            raise Rejected("VOIDED", "card voided")
        info = rec.provisionals.get(hall_id)
        if info is None:
            raise Rejected("NO_PROVISIONAL", f"{hall_id} holds no temp receipt")
        hall = self._hall(hall_id)
        receipt = hall.handoff(token_id, info["ballot_id"])
        # mark the receipt seen so a later hall.sync() replay is a no-op
        self._seen_receipts.add(receipt.receipt_id)
        rec.provisionals.pop(hall_id, None)
        self.ledger.append("handoff", {"token_id": token_id,
                                       "ballot_id": info["ballot_id"],
                                       "hall": hall_id, "at": self._clock()})
        self._void_provisionals(token_id, "lost hand-off", skip_hall=hall_id)
        rec.finalized = True
        rec.finalized_hall = hall_id
        rec.finalized_at = self._clock()
        rec.consumed_ballot = info["ballot_id"]
        self.ledger.append("final", {"token_id": token_id,
                                     "ballot_id": info["ballot_id"],
                                     "hall": hall_id,
                                     "receipt_id": receipt.receipt_id,
                                     "at": receipt.at})
        return receipt.as_dict()

    # ==================================================================
    # closing & inspection helpers
    # ==================================================================
    def close_event(self, event_id: str) -> None:
        ev = self.events[event_id]
        ev.state = EVENT_CLOSED
        for hall in self.halls.values():
            hall.close()
        self.ledger.append("close", {"event_id": event_id,
                                     "at": self._clock()})

    def _rec(self, token_id: str) -> TokenRecord:
        rec = self.tokens.get(token_id)
        if rec is None:
            raise Rejected("UNKNOWN_TOKEN", token_id)
        return rec

    def token_state(self, token_id: str) -> Dict:
        rec = self._rec(token_id)
        kind = rec.authority[0] if isinstance(rec.authority, tuple) \
            else rec.authority
        final_hall = rec.authority[1] if isinstance(rec.authority, tuple) \
            else None
        return {
            "token_id": token_id,
            "member_id": rec.member_id,  # this table is member<->token only
            "event_id": rec.event_id,
            "authority_kind": kind,
            "final_hall": final_hall,
            "loss_state": rec.loss_state,
            "finalized": rec.finalized,
            "finalized_hall": rec.finalized_hall,
        }


__all__ = ["Desk"]
