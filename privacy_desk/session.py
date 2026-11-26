"""A persistent review/booking session.

This is the "本地隐私发布台和维修预约台" glue: a reviewer imports the raw
weekly return slips (the private truth) and any already-mailed paper reports,
submits new publication requests; a maintenance worker then books work slots
from whatever *actually signed-out* receipts exist.  A session serialises to
JSON so the two roles can hand the same state back and forth on one machine.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from .booking import BookingDesk
from .ledger import Ledger
from .model import (
    Booking,
    Constraint,
    Receipt,
    WeekConfig,
)
from .privacy import forced_answers, is_safe
from .publisher import PublishOutcome, Publisher


@dataclass
class Session:
    config: WeekConfig
    answers: Dict[str, int]
    ledger: Ledger
    policy: str = "arrival"
    reserved_groups: Tuple[str, ...] = ()

    # populated lazily -----------------------------------------------------
    def __post_init__(self) -> None:
        if not isinstance(self.reserved_groups, tuple):
            self.reserved_groups = tuple(self.reserved_groups)
        self._rebuild()

    def _rebuild(self) -> None:
        self.publisher = Publisher(
            self.ledger,
            self.config,
            self.answers,
            policy=self.policy,
            reserved_groups=self.reserved_groups,
        )
        self.desk = BookingDesk(self.ledger, self.config)

    # -- construction helpers --------------------------------------------
    @classmethod
    def from_raw(
        cls,
        config: WeekConfig,
        answers: Mapping[str, int],
        *,
        policy: str = "arrival",
        reserved_groups: Sequence[str] = (),
    ) -> "Session":
        return cls(
            config=config,
            answers=dict(answers),
            ledger=Ledger(),
            policy=policy,
            reserved_groups=tuple(reserved_groups),
        )

    # -- reviewer actions -------------------------------------------------
    def import_mailed_exact(
        self, *, group: str, value: int, week: Optional[str] = None
    ) -> Receipt:
        week = week or self.config.week_id
        covers = self.config.group_covers(group)
        return self.publisher.import_mailed(
            week=week, group=group, constraint=Constraint.exact(covers, value)
        )

    def import_mailed_range(
        self, *, group: str, lo: int, hi: int, week: Optional[str] = None
    ) -> Receipt:
        week = week or self.config.week_id
        covers = self.config.group_covers(group)
        return self.publisher.import_mailed(
            week=week, group=group, constraint=Constraint.range(covers, lo, hi)
        )

    def publish(
        self,
        *,
        group: str,
        requester: str,
        want_exact: bool = True,
        week: Optional[str] = None,
    ) -> PublishOutcome:
        week = week or self.config.week_id
        return self.publisher.publish(
            week=week, group=group, requester=requester, want_exact=want_exact
        )

    # -- worker actions ---------------------------------------------------
    def book(
        self,
        *,
        receipt_id: str,
        requester: str,
        slot: str,
        week: Optional[str] = None,
        all_slots: Optional[Sequence[str]] = None,
    ) -> Booking:
        week = week or self.config.week_id
        return self.desk.attempt(
            receipt_id=receipt_id,
            requester=requester,
            slot=slot,
            week=week,
            all_slots=set(all_slots) if all_slots is not None else None,
        )

    def mark_slot_occupied(self, slot: str, holder: str = "prior-work") -> None:
        self.desk.mark_occupied(slot, holder)

    # -- reporting --------------------------------------------------------
    def week_receipts(self, week: Optional[str] = None) -> List[Receipt]:
        return self.ledger.for_week(week or self.config.week_id)

    def derived(self, week: Optional[str] = None) -> Dict[str, int]:
        return forced_answers(
            self.config, self.ledger.constraints_for_week(week or self.config.week_id)
        )

    def state_is_safe(self, week: Optional[str] = None) -> bool:
        return is_safe(
            self.config, self.ledger.constraints_for_week(week or self.config.week_id)
        )

    def report(self, week: Optional[str] = None) -> str:
        week = week or self.config.week_id
        lines = [f"week {week} report"]
        lines.append(f"  households: {', '.join(self.config.households)}")
        for name, cov in self.config.groups.items():
            lines.append(f"  group {name}: {{{', '.join(cov)}}}")
        lines.append("  public receipts:")
        recs = self.week_receipts(week)
        if not recs:
            lines.append("    (none)")
        for r in recs:
            lines.append("    " + r.describe())
        derived = self.derived(week)
        if derived:
            shown = ", ".join(f"{h}={v}" for h, v in sorted(derived.items()))
            lines.append(f"  DERIVED (leak): {shown}")
        else:
            lines.append("  derived: none (every protected household has 2+ possibilities)")
        bks = self.desk.bookings()
        if bks:
            lines.append("  bookings:")
            for b in bks:
                slot = b.slot if b.slot is not None else "-"
                lines.append(f"    {b.booking_id} {b.status} slot={slot} ({b.reason})")
        return "\n".join(lines)

    # -- serialisation ----------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "config": {
                "week_id": self.config.week_id,
                "households": list(self.config.households),
                "groups": {k: list(v) for k, v in self.config.groups.items()},
                "protected": list(self.config.protected),
            },
            "answers": dict(self.answers),
            "policy": self.policy,
            "reserved_groups": list(self.reserved_groups),
            "receipts": self.ledger.to_list(),
            "audit": self.ledger.audit_log(),
            "bookings": self.desk.to_list(),
            "occupied": sorted(self.desk.occupied_slots()),
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_json(cls, text: str) -> "Session":
        obj = json.loads(text)
        cfg = obj["config"]
        config = WeekConfig(
            week_id=cfg["week_id"],
            households=tuple(cfg["households"]),
            groups={k: tuple(v) for k, v in cfg["groups"].items()},
            protected=tuple(cfg.get("protected") or cfg["households"]),
        )
        ledger = Ledger()
        for r in obj["receipts"]:
            c = r["constraint"]
            if c["kind"] == "exact":
                con = Constraint.exact(c["covers"], c["value"], label=c.get("label", ""))
            else:
                con = Constraint.range(c["covers"], c["lo"], c["hi"], label=c.get("label", ""))
            ledger.record(
                week=r["week"],
                group=r["group"],
                constraint=con,
                origin=r["origin"],
                requested_exact=r.get("requested_exact", True),
                requester=r.get("requester", ""),
                receipt_id=r["receipt_id"],
            )
        for a in obj.get("audit", []):
            ledger.note_audit(a)
        sess = cls(
            config=config,
            answers={k: int(v) for k, v in obj["answers"].items()},
            ledger=ledger,
            policy=obj.get("policy", "arrival"),
            reserved_groups=tuple(obj.get("reserved_groups", [])),
        )
        for b in obj.get("bookings", []):
            sess.desk._bookings.append(
                Booking(
                    booking_id=b["booking_id"],
                    requester=b["requester"],
                    group=b["group"],
                    receipt_id=b["receipt_id"],
                    slot=b["slot"],
                    status=b["status"],
                    reason=b["reason"],
                    created_at=b["created_at"],
                )
            )
            if b["status"] == "booked" and b["slot"]:
                sess.desk.mark_occupied(b["slot"], f"{b['requester']}:{b['group']}")
        # restore the id counter so subsequent bookings don't reuse ids
        sess.desk._counter = len(sess.desk._bookings)
        for slot in obj.get("occupied", []):
            if sess.desk.is_slot_free(slot):
                sess.desk.mark_occupied(slot, "prior-work")
        return sess
