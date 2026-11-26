"""Public lottery service: commitments, sealed rules, revealed draws, claims.

Core contract
-------------
* The roster, its order, the quota and the mixing rule are sealed (digest) at
  creation, before any reveal is accepted.
* Each participant commits to ``(salt, value)`` bound to their round *and*
  ticket; they reveal later.  The mixed seed includes *every* revealed value in
  roster order, so an organiser who has already seen some reveals cannot choose
  "this one or the backup": the full committed set fixes the order.
* One witness, one shift.  A round is sealed with a ``witness_shift``:

    - ``escrow``  : the witness oversaw early 2-of-2 cabinet custody, so is not
      on opening duty.  Every original must come from the cabinets (``source ==
      "escrow"``).  An absent depositor does not stall the draw: the cabinets
      still release the same original against matching receipts.
    - ``opening`` : the witness hosts opening-day synchronous sealing, so there
      is no prior custody.  Every original is handed in on site (``source ==
      "onsite"``).  A missing original postpones the whole round.

  A single shift therefore cannot serve a mixed round (甲 wants escrow, 乙 wants
  onsite).  The service *decides which sources to accept* and rejects a round
  whose sources do not all match its shift, making the trade-off explicit.
* First reveal gate: reveals are stored privately; they are shown to anyone
  only once the round is LOCKED (all originals present / retrieval condition
  met).  The POSTPONABLE -> LOCKED transition is where the draw becomes
  unchangeable.
* Complete material must open.  Postponing a LOCKED round is refused.  A
  postponed (延期) round publishes no winners; it is reopened (补开) only under a
  fresh public witness.  Re-issuing (同轮重发) reproduces the identical packet.
* Same reservation settles at most once; a household claims at most once.
* Published packages are kept per round id forever, so after later rounds anyone
  can still retrieve round one's original packet by its round number.
"""

from __future__ import annotations

import copy
import secrets
from dataclasses import dataclass, field
from typing import Any

from .cabinets import CabinetMissing, CabinetOffline, CabinetPair
from .crypto import (
    SOURCES,
    WITNESS_SHIFTS,
    SHIFT_COVERS,
    commitment,
    compute_draw,
    digest,
    original_payload,
    parse_original,
    promote_replacements,
    receipt_digest,
    seal_digest,
    seal_payload,
)
from .models import (
    COMMITTING,
    INVALID,
    LOCKED,
    OPENED,
    POSTPONABLE,
    POSTPONED,
    SEALED,
    Event,
    Outcome,
    Receipt,
)
from .public_package import DEFAULT_PACKAGE_DIR, write_package


class DrawError(Exception):
    """Base class for rule violations raised by the service."""


class WrongState(DrawError):
    pass


class CommitmentMismatch(DrawError):
    pass


class DuplicateSettlement(DrawError):
    pass


class RulesLocked(DrawError):
    pass


class UploadError(DrawError):
    pass


class RevealGate(DrawError):
    pass


@dataclass
class Round:
    round_id: str
    tickets: list[dict]
    quota: int
    mixing_rule: str
    witness_shift: str
    state: str = COMMITTING
    phase: str = POSTPONABLE
    seal_hash: str = ""
    commitments: dict[str, str] = field(default_factory=dict)
    reveals: dict[str, dict] = field(default_factory=dict)
    draw_order: list[str] = field(default_factory=list)
    draw_winners: list[str] = field(default_factory=list)
    winners: list[str] = field(default_factory=list)
    seed: str = ""
    postponed_reason: str = ""
    reopened_from: str = ""
    reissue_of: str = ""
    invalid_reason: str = ""
    attended: set[str] = field(default_factory=set)
    replacements_log: list[dict] = field(default_factory=list)

    def ticket(self, ticket_id: str) -> dict:
        for t in self.tickets:
            if t["ticket_id"] == ticket_id:
                return t
        raise DrawError(f"ticket {ticket_id!r} is not in round {self.round_id!r}")

    def household_of(self, ticket_id: str) -> str:
        return self.ticket(ticket_id)["household_id"]

    def source_of(self, ticket_id: str) -> str:
        return self.ticket(ticket_id).get("source", "")


class DrawService:
    """Authoritative in-process engine; the public package is derived from it."""

    def __init__(
        self,
        cabinets: CabinetPair | None = None,
        package_dir: str | None = DEFAULT_PACKAGE_DIR,
    ):
        self.cabinets = cabinets or CabinetPair()
        self.package_dir = package_dir
        self.rounds: dict[str, Round] = {}
        self.events: list[Event] = []
        self.packages: dict[str, dict] = {}
        self.round_package_hash: dict[str, str] = {}
        self.settlements: dict[str, Receipt] = {}   # "round:ticket" -> receipt (签领账)
        self.claim_index: dict[str, str] = {}
        self._escrowed: dict[tuple[str, str], bool] = {}

    # ------------------------------------------------------------------
    # event log
    # ------------------------------------------------------------------
    def _emit(self, type_: str, data: dict) -> Event:
        prev = self.events[-1].hash if self.events else ""
        ev = Event(seq=len(self.events), type=type_, data=data, prev_hash=prev).finalize()
        self.events.append(ev)
        return ev

    def log(self) -> list[dict]:
        return [e.to_dict() for e in self.events]

    # ------------------------------------------------------------------
    # round creation and sealing
    # ------------------------------------------------------------------
    def create_round(
        self,
        round_id: str,
        tickets: list[dict],
        quota: int,
        mixing_rule: str,
        witness_shift: str,
        *,
        reopened_from: str = "",
        reissue_of: str = "",
    ) -> Round:
        if round_id in self.rounds:
            raise DrawError(f"round {round_id!r} already exists")
        if witness_shift not in WITNESS_SHIFTS:
            raise DrawError(f"unknown witness shift {witness_shift!r}")
        if quota < 1:
            raise DrawError("quota must be >= 1")
        tids = [t["ticket_id"] for t in tickets]
        if len(set(tids)) != len(tids):
            raise DrawError("duplicate ticket ids in roster")
        if len(tickets) < quota:
            raise DrawError("roster smaller than quota")
        # "同一户最多领一次": quota seats must be fillable by distinct households.
        if len({t["household_id"] for t in tickets}) < quota:
            raise DrawError(
                "not enough distinct households to fill the quota "
                "(one household may hold at most one seat)"
            )

        allowed_source = SHIFT_COVERS[witness_shift]
        norm_tickets: list[dict] = []
        for t in tickets:
            src = t.get("source", allowed_source)
            if src not in SOURCES:
                raise DrawError(f"unknown source {src!r} for ticket {t['ticket_id']!r}")
            if src != allowed_source:
                raise DrawError(
                    f"ticket {t['ticket_id']!r} needs source {src!r} but a "
                    f"{witness_shift!r} shift can only serve {allowed_source!r} "
                    f"originals; one witness cannot cover both custody routes"
                )
            norm_tickets.append(
                {
                    "ticket_id": t["ticket_id"],
                    "household_id": t["household_id"],
                    "source": src,
                }
            )

        if reissue_of:
            src = self.rounds.get(reissue_of)
            if src is None or src.state != OPENED:
                raise DrawError("can only re-issue an opened round")
        if reopened_from:
            src = self.rounds.get(reopened_from)
            if src is None or src.state != POSTPONED:
                raise DrawError("can only reopen a postponed round")

        rnd = Round(
            round_id=round_id,
            tickets=norm_tickets,
            quota=quota,
            mixing_rule=mixing_rule,
            witness_shift=witness_shift,
            reopened_from=reopened_from,
            reissue_of=reissue_of,
        )
        self.rounds[round_id] = rnd
        self._emit(
            "ROUND_CREATED",
            {
                "round_id": round_id,
                "quota": quota,
                "witness_shift": witness_shift,
                "roster": [
                    {
                        "ticket_id": t["ticket_id"],
                        "household_id": t["household_id"],
                        "source": t["source"],
                    }
                    for t in norm_tickets
                ],
                "reopened_from": reopened_from,
                "reissue_of": reissue_of,
            },
        )
        self.seal(round_id)
        return rnd

    def seal(self, round_id: str, override_hash: str = "") -> str:
        rnd = self._get(round_id)
        if rnd.state not in (COMMITTING, SEALED):
            raise WrongState(f"cannot seal a round in state {rnd.state}")
        if rnd.seal_hash and not override_hash:
            return rnd.seal_hash
        h = override_hash or seal_digest(
            rnd.round_id, rnd.tickets, rnd.quota, rnd.mixing_rule, rnd.witness_shift
        )
        rnd.seal_hash = h
        if rnd.state == COMMITTING:
            rnd.state = SEALED
        self._emit(
            "ROUND_SEALED",
            {
                "round_id": round_id,
                "seal_hash": h,
                "quota": rnd.quota,
                "mixing_rule": rnd.mixing_rule,
                "witness_shift": rnd.witness_shift,
                "cutoff": seal_payload(
                    round_id, rnd.tickets, rnd.quota, rnd.mixing_rule, rnd.witness_shift
                )["cutoff"],
                "roster": [
                    {
                        "ticket_id": t["ticket_id"],
                        "household_id": t["household_id"],
                        "source": t["source"],
                    }
                    for t in rnd.tickets
                ],
            },
        )
        return h

    # ------------------------------------------------------------------
    # commitments and escrow deposits (before opening)
    # ------------------------------------------------------------------
    def commit(
        self,
        round_id: str,
        ticket_id: str,
        *,
        salt: str = "",
        value: str = "",
        commit_hash: str = "",
    ) -> str:
        rnd = self._get(round_id)
        if rnd.state != SEALED:
            raise WrongState(f"commitments only accepted while SEALED (state={rnd.state})")
        rnd.ticket(ticket_id)
        if ticket_id in rnd.commitments:
            raise DrawError(f"ticket {ticket_id!r} already committed")
        if not commit_hash:
            if salt == "" and value == "":
                raise DrawError("provide salt/value or a commit_hash")
            commit_hash = commitment(round_id, ticket_id, salt, value)
        rnd.commitments[ticket_id] = commit_hash
        self._emit(
            "COMMITMENT_REGISTERED",
            {"round_id": round_id, "ticket_id": ticket_id, "commitment": commit_hash},
        )
        return commit_hash

    def deposit_escrow(
        self, round_id: str, ticket_id: str, salt: str, value: str
    ) -> list[dict]:
        """甲-style: hand the sealed original to both cabinets before opening."""
        rnd = self._get(round_id)
        if rnd.witness_shift != "escrow" or rnd.source_of(ticket_id) != "escrow":
            raise UploadError("escrow deposits only apply to escrow-source tickets in an escrow shift")
        if rnd.state != SEALED or rnd.phase != POSTPONABLE:
            raise UploadError("escrow deposit only accepted before locking")
        committed = rnd.commitments.get(ticket_id)
        if committed is None:
            raise UploadError(f"ticket {ticket_id!r} must commit before escrowing")
        recomputed = commitment(round_id, ticket_id, salt, value)
        if not secrets.compare_digest(recomputed, committed):
            raise CommitmentMismatch(
                f"escrow original for {ticket_id!r} does not match its commitment"
            )
        payload = original_payload(salt, value)
        receipts = self.cabinets.deposit(round_id, ticket_id, payload)
        self._escrowed[(round_id, ticket_id)] = True
        self._emit(
            "ESCROW_DEPOSITED",
            {
                "round_id": round_id,
                "ticket_id": ticket_id,
                "receipts": [r.to_dict() for r in receipts],
                "original_digest": digest({"salt": salt, "value": value}),
            },
        )
        return [r.to_dict() for r in receipts]

    # ------------------------------------------------------------------
    # onsite reveal intake (opening shift)
    # ------------------------------------------------------------------
    def submit_reveal(self, round_id: str, ticket_id: str, salt: str, value: str) -> str:
        """乙-style: hand in the original on site; checked against commitment."""
        rnd = self._get(round_id)
        if rnd.state not in (SEALED, COMMITTING):
            raise WrongState(f"reveals not accepted in state {rnd.state}")
        if rnd.witness_shift != "opening" or rnd.source_of(ticket_id) != "onsite":
            raise UploadError("onsite reveals only apply to onsite-source tickets in an opening shift")
        committed = rnd.commitments.get(ticket_id)
        if committed is None:
            raise DrawError(f"ticket {ticket_id!r} has no commitment")
        recomputed = commitment(round_id, ticket_id, salt, value)
        if not secrets.compare_digest(recomputed, committed):
            self._mark_invalid(rnd, f"reveal for {ticket_id!r} does not match commitment")
            raise CommitmentMismatch(
                f"reveal for {ticket_id!r} does not match its sealed commitment"
            )
        rnd.reveals[ticket_id] = {"salt": salt, "value": value}
        rnd.attended.add(ticket_id)
        od = digest({"salt": salt, "value": value})
        self._emit(
            "REVEAL_ACCEPTED",
            {"round_id": round_id, "ticket_id": ticket_id, "original_digest": od},
        )
        self.refresh_lock(round_id)
        return od

    def _mark_invalid(self, rnd: Round, reason: str) -> None:
        rnd.state = INVALID
        rnd.invalid_reason = reason
        self._emit("ROUND_INVALIDATED", {"round_id": rnd.round_id, "reason": reason})

    def _missing_reveals(self, rnd: Round) -> list[str]:
        return [t["ticket_id"] for t in rnd.tickets if t["ticket_id"] not in rnd.reveals]

    def refresh_lock(self, round_id: str) -> bool:
        rnd = self._get(round_id)
        if rnd.phase == LOCKED:
            return True
        if rnd.state != SEALED:
            return False
        if self._missing_reveals(rnd):
            return False
        rnd.phase = LOCKED
        self._emit(
            "ROUND_LOCKED",
            {"round_id": round_id, "reason": "all original reveals present and verified"},
        )
        return True

    def first_reveal(self, round_id: str) -> dict[str, dict]:
        """Release the reveals to any party only once the round is LOCKED.

        Before locking, callers get ``RevealGate``: "只有在所有原值或事先约定的
        取回条件都已锁定时才准让人见到首份揭示".
        """
        rnd = self._get(round_id)
        if rnd.phase != LOCKED:
            raise RevealGate(
                f"round {round_id!r} is not locked; the first reveal may not be shown yet"
            )
        return copy.deepcopy(rnd.reveals)

    # ------------------------------------------------------------------
    # escrow recovery: cabinets supply the sealed originals
    # ------------------------------------------------------------------
    def recover_from_cabinets(self, round_id: str) -> list[str]:
        rnd = self._get(round_id)
        if rnd.witness_shift != "escrow":
            raise DrawError("recovery only applies to escrow-shift rounds")
        # Phase 1: retrieve everything first.  Any offline/missing cabinet
        # raises before we touch round state, so a partial set can never leak.
        staged: list[tuple[str, str, str, list]] = []
        for t in rnd.tickets:
            tid = t["ticket_id"]
            if rnd.source_of(tid) != "escrow":
                continue
            if not self.cabinets.has_deposit(round_id, tid):
                continue
            res = self.cabinets.retrieve(round_id, tid)  # raises if any cabinet down
            salt, value = parse_original(res.original)
            committed = rnd.commitments.get(tid)
            if committed is not None:
                recomputed = commitment(round_id, tid, salt, value)
                if not secrets.compare_digest(recomputed, committed):
                    self._mark_invalid(
                        rnd, f"escrowed original for {tid!r} fails its commitment"
                    )
                    raise CommitmentMismatch(
                        f"escrowed original for {tid!r} fails its commitment"
                    )
            staged.append((tid, salt, value, res.receipts))

        # Phase 2: commit the whole retrieved set at once.
        recovered: list[str] = []
        for tid, salt, value, receipts in staged:
            rnd.reveals[tid] = {"salt": salt, "value": value}
            recovered.append(tid)
            self._emit(
                "ORIGINAL_RECOVERED",
                {
                    "round_id": round_id,
                    "ticket_id": tid,
                    "receipts": [r.to_dict() for r in receipts],
                },
            )
        self.refresh_lock(round_id)
        return recovered

    # ------------------------------------------------------------------
    # postpone / reopen / reissue
    # ------------------------------------------------------------------
    def postpone(self, round_id: str, reason: str, witness: str) -> None:
        rnd = self._get(round_id)
        if rnd.state == OPENED:
            raise WrongState("cannot postpone an opened round")
        if rnd.state == POSTPONED:
            raise WrongState("round already postponed")
        if rnd.phase == LOCKED:
            raise RulesLocked(
                "all originals are present and locked; this round must open, not postpone"
            )
        if rnd.state not in (SEALED, COMMITTING):
            raise WrongState(f"cannot postpone from state {rnd.state}")
        if not witness:
            raise DrawError("postponement requires a named public witness")
        rnd.state = POSTPONED
        rnd.postponed_reason = reason
        rnd.reveals = {}
        rnd.winners = []
        self._emit(
            "ROUND_POSTPONED",
            {"round_id": round_id, "reason": reason, "witness": witness},
        )

    def reopen(self, round_id: str, new_round_id: str, witness: str) -> Round:
        if not witness:
            raise DrawError("reopening requires a named public witness")
        src = self._get(round_id)
        if src.state != POSTPONED:
            raise WrongState("only a postponed round can be reopened")
        new = self.create_round(
            new_round_id,
            src.tickets,
            src.quota,
            src.mixing_rule,
            src.witness_shift,
            reopened_from=round_id,
        )
        self._emit(
            "ROUND_REOPENED",
            {"round_id": new_round_id, "reopened_from": round_id, "witness": witness},
        )
        return new

    def reissue(self, round_id: str) -> dict:
        rnd = self._get(round_id)
        if rnd.state != OPENED:
            raise WrongState("only an opened round can be re-issued")
        orig = self.packages[round_id]
        orig_hash = self.round_package_hash[round_id]
        clone = copy.deepcopy(orig)
        assert clone["packet_hash"] == orig_hash
        self._emit(
            "ROUND_REISSUED",
            {
                "round_id": round_id,
                "original_package_hash": orig_hash,
                "winners": list(rnd.winners),
            },
        )
        return clone

    # ------------------------------------------------------------------
    # opening the draw
    # ------------------------------------------------------------------
    def open_round(self, round_id: str, *, witness: str) -> Outcome:
        rnd = self._get(round_id)
        if rnd.state == OPENED:
            raise WrongState("round already opened")
        if rnd.state == POSTPONED:
            raise WrongState("round is postponed; reopen it first")
        if rnd.state == INVALID:
            raise WrongState(f"round is invalid: {rnd.invalid_reason}")

        offline: list[str] = []
        if rnd.witness_shift == "escrow":
            try:
                self.recover_from_cabinets(round_id)
            except (CabinetOffline, CabinetMissing):
                offline = self._offline_cabinets(rnd)

        self.refresh_lock(round_id)
        missing = self._missing_reveals(rnd)

        if missing:
            if rnd.phase == LOCKED:
                raise DrawError("internal invariant violated: locked round missing reveals")
            if not offline:
                offline = self._offline_cabinets(rnd)
            if offline:
                reason = "witness cabinet offline while originals incomplete -> postpone"
            else:
                reason = "originals incomplete; round postponed (no winners published)"
            self.postpone(round_id, reason, witness)
            return Outcome(
                opened=False,
                postponed=True,
                reason=reason,
                missing_tickets=missing,
                offline_cabinets=offline,
                state=rnd.state,
            )

        seed, draw_order, winners = self._recompute(rnd)
        rnd.seed = seed
        rnd.draw_order = draw_order
        rnd.draw_winners = list(winners)
        rnd.winners = list(winners)
        rnd.state = OPENED
        self._emit(
            "ROUND_OPENED",
            {
                "round_id": round_id,
                "seed": seed,
                "draw_order": draw_order,
                "winners": winners,
                "witness": witness,
            },
        )
        pkg = self._build_package(rnd)
        self._publish(rnd, pkg)
        return Outcome(opened=True, postponed=False, winners=list(winners), state=rnd.state)

    def _offline_cabinets(self, rnd: Round) -> list[str]:
        if rnd.witness_shift != "escrow":
            return []
        if not any(self._escrowed.get((rnd.round_id, t["ticket_id"])) for t in rnd.tickets):
            return []
        return [c.cabinet_id for c in self.cabinets.cabinets if not c.online]

    def _recompute(self, rnd: Round):
        return compute_draw(rnd.tickets, rnd.quota, rnd.mixing_rule, rnd.reveals)

    # ------------------------------------------------------------------
    # public package
    # ------------------------------------------------------------------
    def _build_package(self, rnd: Round) -> dict:
        seal = seal_payload(
            rnd.round_id, rnd.tickets, rnd.quota, rnd.mixing_rule, rnd.witness_shift
        )
        reveals_pub = {
            t["ticket_id"]: {
                "salt": rnd.reveals[t["ticket_id"]]["salt"],
                "value": rnd.reveals[t["ticket_id"]]["value"],
            }
            for t in rnd.tickets
            if t["ticket_id"] in rnd.reveals
        }
        pkg = {
            "round_id": rnd.round_id,
            "witness_shift": rnd.witness_shift,
            "seal": seal,
            "seal_hash": rnd.seal_hash,
            "commitments": dict(rnd.commitments),
            "reveals": reveals_pub,
            "seed": rnd.seed,
            "draw_order": list(rnd.draw_order),
            "draw_winners": list(rnd.draw_winners),
            "winners": list(rnd.winners),
            "replacements": list(rnd.replacements_log),
            "roster": list(seal["roster"]),
            "quota": rnd.quota,
            "mixing_rule": rnd.mixing_rule,
            "reopened_from": rnd.reopened_from,
            "reissue_of": rnd.reissue_of,
        }
        pkg["packet_hash"] = digest({k: v for k, v in pkg.items() if k != "packet_hash"})
        return pkg

    def _publish(self, rnd: Round, pkg: dict) -> None:
        self.packages[rnd.round_id] = pkg
        self.round_package_hash[rnd.round_id] = pkg["packet_hash"]
        if self.package_dir is not None:
            try:
                write_package(self.package_dir, rnd.round_id, pkg)
            except OSError:
                pass
        self._emit(
            "PACKAGE_PUBLISHED",
            {"round_id": rnd.round_id, "packet_hash": pkg["packet_hash"]},
        )

    # ------------------------------------------------------------------
    # claims and settlements
    # ------------------------------------------------------------------
    def _settle_key(self, round_id: str, ticket_id: str) -> str:
        return f"{round_id}:{ticket_id}"

    def claim(self, round_id: str, ticket_id: str) -> Receipt:
        """Issue a claim receipt; a reservation settles at most once."""
        rnd = self._get(round_id)
        if rnd.state != OPENED:
            raise WrongState("round not opened; no claims possible")
        if ticket_id not in rnd.winners:
            raise DrawError(f"ticket {ticket_id!r} is not a winner in {round_id!r}")
        key = self._settle_key(round_id, ticket_id)
        if key in self.settlements:
            raise DuplicateSettlement(f"{key} already settled")
        hh = rnd.household_of(ticket_id)
        if self.household_has_settled(round_id, hh):
            raise DuplicateSettlement(f"household {hh!r} already claimed in {round_id!r}")
        token = secrets.token_hex(16)
        receipt_id = secrets.token_hex(12)
        rh = receipt_digest("service", round_id, ticket_id, receipt_id)
        receipt = Receipt(
            round_id=round_id,
            ticket_id=ticket_id,
            household_id=hh,
            claim_token=token,
            receipt_id=receipt_id,
            receipt_hash=rh,
        )
        self.settlements[key] = receipt
        self.claim_index[token] = key
        self._emit(
            "TICKET_CLAIMED",
            {
                "round_id": round_id,
                "ticket_id": ticket_id,
                "household_id": hh,
                "receipt_hash": rh,
            },
        )
        return receipt

    def mark_settled(self, round_id: str, ticket_id: str, receipt_id: str = "external") -> Receipt:
        """Simulate the claims ledger (签领账) already showing a hand-over."""
        rnd = self._get(round_id)
        key = self._settle_key(round_id, ticket_id)
        if key in self.settlements:
            return self.settlements[key]
        token = secrets.token_hex(16)
        rh = receipt_digest("ledger", round_id, ticket_id, receipt_id)
        receipt = Receipt(
            round_id=round_id,
            ticket_id=ticket_id,
            household_id=rnd.household_of(ticket_id),
            claim_token=token,
            receipt_id=receipt_id,
            receipt_hash=rh,
        )
        self.settlements[key] = receipt
        self.claim_index[token] = key
        self._emit(
            "TICKET_SETTLED_ON_LEDGER",
            {"round_id": round_id, "ticket_id": ticket_id, "receipt_id": receipt_id},
        )
        return receipt

    def is_settled(self, round_id: str, ticket_id: str) -> bool:
        return self._settle_key(round_id, ticket_id) in self.settlements

    def household_has_settled(self, round_id: str, household_id: str) -> bool:
        for rec in self.settlements.values():
            if rec.round_id == round_id and rec.household_id == household_id:
                return True
        return False

    def settlement_receipt(self, round_id: str, ticket_id: str) -> Receipt | None:
        return self.settlements.get(self._settle_key(round_id, ticket_id))

    # ------------------------------------------------------------------
    # replacement along the originally sealed order
    # ------------------------------------------------------------------
    def replace(
        self,
        round_id: str,
        absent: list[str],
        *,
        attended: set[str] | None = None,
        refreshed_reveals: dict[str, dict] | None = None,
    ) -> list[str]:
        """Promote replacements strictly along the sealed draw order.

        ``absent`` tickets must be seated winners with no settlement.
        Replacements are the next entries of the *original* recomputed order,
        honouring "same household at most once", on-site attendance, and the
        claims ledger ("本户已领记录").  Never picks out of order.
        """
        rnd = self._get(round_id)
        if rnd.state != OPENED:
            raise WrongState("replacements only apply to an opened round")
        reveals = refreshed_reveals if refreshed_reveals is not None else rnd.reveals
        _seed, draw_order, base_winners = compute_draw(
            rnd.tickets, rnd.quota, rnd.mixing_rule, reveals
        )
        if base_winners != rnd.draw_winners:
            raise DrawError("published draw winners do not match recomputed order; refusing")

        present = attended if attended is not None else set(rnd.attended)

        absent_set = set(absent)
        for tid in absent_set:
            rnd.ticket(tid)
            if tid not in rnd.winners:
                raise DrawError(f"{tid!r} is not a seated winner; nothing to replace")
            if self.is_settled(round_id, tid):
                raise DrawError(f"{tid!r} already claimed; cannot be replaced")

        replacements: list[str] = []
        hh_by_ticket = {t["ticket_id"]: t["household_id"] for t in rnd.tickets}
        # Households already seated by the remaining winners are excluded by the
        # rule itself; claimed households are tracked from the ledger.
        claimed_households = {
            rec.household_id
            for rec in self.settlements.values()
            if rec.round_id == round_id
        }
        replacements = promote_replacements(
            draw_order,
            rnd.winners,
            sorted(absent_set),
            sorted(present),
            claimed_households,
            hh_by_ticket,
        )

        if len(replacements) < len(absent_set):
            raise DrawError("not enough eligible replacements in sealed order")

        new_winners = [w for w in rnd.winners if w not in absent_set] + replacements
        rnd.winners = new_winners
        step = {
            "absent": sorted(absent_set),
            "attended": sorted(present),
            "replacements": list(replacements),
            "winners": list(new_winners),
        }
        rnd.replacements_log.append(step)
        self._emit("REPLACEMENTS_PROMOTED", {"round_id": round_id, **step})
        pkg = self._build_package(rnd)
        self._publish(rnd, pkg)
        return replacements

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _get(self, round_id: str) -> Round:
        try:
            return self.rounds[round_id]
        except KeyError:
            raise DrawError(f"unknown round {round_id!r}") from None

    def package(self, round_id: str) -> dict:
        try:
            return copy.deepcopy(self.packages[round_id])
        except KeyError:
            raise DrawError(f"round {round_id!r} has no published package") from None

    def winners(self, round_id: str) -> list[str]:
        return list(self._get(round_id).winners)
