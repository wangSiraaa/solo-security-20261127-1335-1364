"""Worked scenarios from the brief.

Households (week layout used throughout):

* North wing  北翼 = {甲, 乙}
* Whole floor 整层 = {甲, 乙, 丙}

A concrete week where each group reports exactly one complaint (甲=1, 乙=0,
丙=0) is the heart of the story: publishing *both* exact counts makes 丙
derivable as "no noise" (since 甲+乙+丙=1 and 甲+乙=1  =>  丙=0).  Publishing
either exact count alone hides which of 甲/乙 complained.

All scenarios return a :class:`ScenarioResult` with human-readable ``lines`` and
structured ``data``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from .booking import BookingDesk
from .ledger import Ledger
from .model import Constraint, PrivacyError, WeekConfig
from .privacy import forced_answers
from .publisher import PublishOutcome, Publisher

# -- canonical household names -------------------------------------------
JIA, YI, BING = "甲", "乙", "丙"
NORTH = "北翼"
FLOOR = "整层"
TENANT = "租户"
PROPERTY = "物业"

MAX_HOUSEHOLDS = 8


@dataclass
class ScenarioResult:
    name: str
    lines: List[str] = field(default_factory=list)
    data: dict = field(default_factory=dict)

    def add(self, line: str) -> None:
        self.lines.append(line)

    def render(self) -> str:
        return "\n".join(self.lines)


# ---------------------------------------------------------------------------
# week builders
# ---------------------------------------------------------------------------
def sample_week1() -> Tuple[WeekConfig, Dict[str, int]]:
    """Week 1: 甲=1, 乙=0, 丙=0 -> 北翼 count 1, 整层 count 1."""
    config = WeekConfig(
        week_id="W1",
        households=(JIA, YI, BING),
        groups={NORTH: (JIA, YI), FLOOR: (JIA, YI, BING)},
        protected=(JIA, YI, BING),
    )
    answers = {JIA: 1, YI: 0, BING: 0}
    return config, answers


def sample_week2() -> Tuple[WeekConfig, Dict[str, int]]:
    """Week 2: same layout and same reported counts, fresh week id."""
    config = WeekConfig(
        week_id="W2",
        households=(JIA, YI, BING),
        groups={NORTH: (JIA, YI), FLOOR: (JIA, YI, BING)},
        protected=(JIA, YI, BING),
    )
    answers = {JIA: 1, YI: 0, BING: 0}
    return config, answers


def _public_snapshot(ledger: Ledger, week: str) -> str:
    receipts = ledger.for_week(week)
    if not receipts:
        return "  (no public receipts yet)"
    return "\n".join("  " + r.describe() for r in receipts)


def _derived_line(config: WeekConfig, ledger: Ledger, week: str) -> str:
    derived = forced_answers(config, ledger.constraints_for_week(week))
    if not derived:
        return "  derivable protected answers: none (all still have 2+ possibilities)"
    shown = ", ".join(f"{h}={v}" for h, v in sorted(derived.items()))
    return f"  derivable protected answers: {shown}  <-- LEAK"


def _booking_line(bk) -> str:
    slot = bk.slot if bk.slot is not None else "-"
    return f"  booking {bk.booking_id}: {bk.status} slot={slot}  ({bk.reason})"


# ---------------------------------------------------------------------------
# Week 1: arrival-order replays
# ---------------------------------------------------------------------------
def _week1_replay(first: str) -> ScenarioResult:
    """Replay week 1 with one party's exact request first, the other second.

    ``first`` is ``TENANT`` or ``PROPERTY``.  Uses the arrival policy: first
    served keeps exact if safe, the later party bears the downgrade cost.
    """
    config, answers = sample_week1()
    ledger = Ledger()
    pub = Publisher(ledger, config, answers, policy="arrival")
    desk = BookingDesk(ledger, config)
    order = [TENANT, PROPERTY] if first == TENANT else [PROPERTY, TENANT]

    name = f"week1/{'tenant-first' if first == TENANT else 'property-first'}"
    res = ScenarioResult(name=name)
    res.add(f"=== {name} ===")
    res.add(
        f"truth (private): 甲={answers[JIA]} 乙={answers[YI]} 丙={answers[BING]}  "
        f"=> 北翼={sum(answers[h] for h in (JIA, YI))}, 整层={sum(answers.values())}"
    )

    outcomes: Dict[str, PublishOutcome] = {}
    for who in order:
        group = NORTH if who == TENANT else FLOOR
        out = pub.publish(week="W1", group=group, requester=who, want_exact=True)
        outcomes[who] = out
        res.add(f"\n{who} requests exact count for {group}:")
        res.add(f"  -> {out.describe()}")
        res.add(_public_snapshot(ledger, "W1"))
        res.add(_derived_line(config, ledger, "W1"))

    # each party attempts to book a slot from what it actually received
    res.add("\nbooking attempts (only an exact signed-out count may book):")
    bookings = {}
    for who in order:
        out = outcomes[who]
        grp = NORTH if who == TENANT else FLOOR
        slot = "入户时段" if who == TENANT else "入楼检修"
        bk = desk.attempt(
            receipt_id=out.receipt_id,
            requester=who,
            slot=slot,
            week="W1",
            all_slots={slot},
        )
        bookings[who] = bk
        res.add(_booking_line(bk))

    # guard: if any party could have got exact safely, the week must not be
    # all-blank.
    any_exact = any(o.got_exact for o in outcomes.values())
    if any_exact:
        all_blank = all(
            (o.kind == "range" and o.blank) or o.refused for o in outcomes.values()
        )
        if all_blank:  # pragma: no cover - guard only
            raise PrivacyError("blank-only report despite a safe exact being possible")

    res.data = {
        "outcomes": {w: o.__dict__ for w, o in outcomes.items()},
        "bookings": {w: b.to_dict() for w, b in bookings.items()},
        "who_got_exact": [w for w, o in outcomes.items() if o.got_exact],
        "who_booked": [w for w, b in bookings.items() if b.booked],
        "derived": forced_answers(config, ledger.constraints_for_week("W1")),
        "receipts": ledger.to_list(),
    }
    res.add("")
    res.add(
        "summary: exact -> "
        + (", ".join(res.data["who_got_exact"]) or "none")
        + "; booked -> "
        + (", ".join(res.data["who_booked"]) or "none")
    )
    return res


def replay_week1() -> List[ScenarioResult]:
    """Run both arrival-order replays and return them in order."""
    return [_week1_replay(TENANT), _week1_replay(PROPERTY)]


# ---------------------------------------------------------------------------
# Week 2: three replays from "north-wing report not yet mailed"
# ---------------------------------------------------------------------------
def _week2_case1() -> ScenarioResult:
    config, answers = sample_week2()
    ledger = Ledger()
    pub = Publisher(ledger, config, answers, policy="arrival")
    desk = BookingDesk(ledger, config)
    res = ScenarioResult(name="week2/case1-no-old-reports")
    res.add("=== week2 / case 1: no old reports this week ===")
    res.add("starting point: north-wing report not yet mailed; public history empty")

    out = pub.publish(week="W2", group=NORTH, requester=TENANT, want_exact=True)
    res.add(f"\n{pub and TENANT} publishes 北翼 report:")
    res.add(f"  -> {out.describe()}")
    res.add(_public_snapshot(ledger, "W2"))
    res.add(_derived_line(config, ledger, "W2"))

    bk = desk.attempt(
        receipt_id=out.receipt_id,
        requester=TENANT,
        slot="入户时段",
        week="W2",
        all_slots={"入户时段"},
    )
    res.add("\ntenant books entry slot:")
    res.add(_booking_line(bk))
    res.add(
        "\nresult: north-wing exact is safe on its own, so the tenant booked "
        "the entry repair."
    )
    res.data = {
        "outcome": out.__dict__,
        "booking": bk.to_dict(),
        "derived": forced_answers(config, ledger.constraints_for_week("W2")),
        "got_exact": out.got_exact,
        "booked": bk.booked,
    }
    return res


def _week2_case2() -> ScenarioResult:
    config, answers = sample_week2()
    ledger = Ledger()
    pub = Publisher(ledger, config, answers, policy="arrival")
    desk = BookingDesk(ledger, config)
    res = ScenarioResult(name="week2/case2-mailed-floor-exact")
    res.add("=== week2 / case 2: one already-mailed 整层 exact old report ===")
    res.add("starting point: north-wing report not yet mailed; import a mailed 整层 exact")

    mailed = pub.import_mailed(
        week="W2",
        group=FLOOR,
        constraint=Constraint.exact(config.group_covers(FLOOR), 1, label="mailed"),
        note="old whole-floor exact already in readers' hands",
    )
    res.add(f"\nimported mailed receipt: {mailed.describe()}")
    res.add(_public_snapshot(ledger, "W2"))

    out = pub.publish(week="W2", group=NORTH, requester=TENANT, want_exact=True)
    res.add("\ntenant now requests 北翼 exact (cannot be sent as exact):")
    res.add(f"  -> {out.describe()}")
    res.add(_public_snapshot(ledger, "W2"))
    res.add(_derived_line(config, ledger, "W2"))

    bk = desk.attempt(
        receipt_id=out.receipt_id,
        requester=TENANT,
        slot="入户时段",
        week="W2",
        all_slots={"入户时段"},
    )
    res.add("\ntenant tries to book entry slot:")
    res.add(_booking_line(bk))
    res.add(
        "\nresult: with the mailed 整层 exact already public, sending 北翼 exact "
        "would derive 丙; the desk issues a range and the tenant cannot book."
    )
    res.data = {
        "mailed": mailed.to_dict(),
        "outcome": out.__dict__,
        "booking": bk.to_dict(),
        "derived": forced_answers(config, ledger.constraints_for_week("W2")),
        "got_exact": out.got_exact,
        "booked": bk.booked,
    }
    return res


def _week2_case3() -> ScenarioResult:
    config, answers = sample_week2()
    ledger = Ledger()
    pub = Publisher(ledger, config, answers, policy="arrival")
    desk = BookingDesk(ledger, config)
    res = ScenarioResult(name="week2/case3-entry-slot-occupied")
    res.add("=== week2 / case 3: blank history, entry slot already occupied ===")
    res.add("starting point: north-wing report not yet mailed; public history blank")

    # the chosen entry slot is already taken by prior work
    desk.mark_occupied("入户时段-上午", holder="prior-work")
    res.add("\nentry slot 入户时段-上午 already occupied.")

    out = pub.publish(week="W2", group=NORTH, requester=TENANT, want_exact=True)
    res.add("\nnorth-wing weekly report is still mailed (exact is safe):")
    res.add(f"  -> {out.describe()}")
    res.add(_public_snapshot(ledger, "W2"))
    res.add(_derived_line(config, ledger, "W2"))

    bk = desk.attempt(
        receipt_id=out.receipt_id,
        requester=TENANT,
        slot="入户时段-上午",
        week="W2",
        all_slots={"入户时段-上午", "入户时段-下午"},
    )
    res.add("\ntenant tries to book the occupied slot:")
    res.add(_booking_line(bk))

    moved = desk.reschedule(bk.booking_id, "入户时段-下午")
    res.add("tenant reschedules to a free slot:")
    res.add(_booking_line(moved))
    res.add(
        "\nresult: the weekly report still goes out; only the appointment is "
        "moved to a later slot."
    )
    res.data = {
        "outcome": out.__dict__,
        "refused_booking": bk.to_dict(),
        "final_booking": moved.to_dict(),
        "derived": forced_answers(config, ledger.constraints_for_week("W2")),
        "report_mailed": out.receipt_id is not None,
        "booked": moved.booked,
    }
    return res


def replay_week2() -> List[ScenarioResult]:
    return [_week2_case1(), _week2_case2(), _week2_case3()]


def run_composition_demo() -> ScenarioResult:
    """The headline question: place two individually-compliant reports together.

    Each exact count is fine alone; together they derive 丙.  The desk must
    therefore never issue the second as exact — it issues a range instead, and
    the derivation disappears.
    """
    config, answers = sample_week1()
    ledger = Ledger()
    pub = Publisher(ledger, config, answers, policy="arrival")
    res = ScenarioResult(name="composition-two-compliant-reports")
    res.add("=== two individually-compliant reports: alone vs together ===")

    north_c = Constraint.exact(config.group_covers(NORTH), 1)
    floor_c = Constraint.exact(config.group_covers(FLOOR), 1)

    res.add(f"\n北翼 exact alone (public): {north_c.describe()}")
    res.add(f"  丙 possible answers: {sorted(_bing_possible(config, [north_c]))}")
    res.add(f"整层 exact alone (public): {floor_c.describe()}")
    res.add(f"  丙 possible answers: {sorted(_bing_possible(config, [floor_c]))}")
    res.add("both exact together:")
    res.add(f"  丙 possible answers: {sorted(_bing_possible(config, [north_c, floor_c]))}"
            "  <-- collapses to a single value: 丙 leaked")

    # now let the real desk serve both, in arrival order
    res.add("\nserved by the desk (arrival order):")
    o1 = pub.publish(week="W1", group=NORTH, requester=TENANT, want_exact=True)
    o2 = pub.publish(week="W1", group=FLOOR, requester=PROPERTY, want_exact=True)
    res.add("  " + o1.describe())
    res.add("  " + o2.describe())
    bing_after = sorted(_bing_possible(config, ledger.constraints_for_week("W1")))
    res.add(f"  丙 possible answers after the desk runs: {bing_after}  (still 2+)")
    res.add(f"  derivable: {forced_answers(config, ledger.constraints_for_week('W1')) or 'none'}")

    res.data = {
        "bing_alone_north": sorted(_bing_possible(config, [north_c])),
        "bing_alone_floor": sorted(_bing_possible(config, [floor_c])),
        "bing_together": sorted(_bing_possible(config, [north_c, floor_c])),
        "bing_after_desk": bing_after,
        "derived_after_desk": forced_answers(config, ledger.constraints_for_week("W1")),
    }
    return res


def _bing_possible(config: WeekConfig, constraints: Sequence[Constraint]) -> set:
    from .privacy import possible_values

    return possible_values(config, constraints, BING)


def run_correction_demo() -> ScenarioResult:
    """Prove the append-only rule: a same-week correction cannot make an
    already-mailed number disappear from what readers can see."""
    config, answers = sample_week1()
    ledger = Ledger()
    pub = Publisher(ledger, config, answers, policy="arrival")
    res = ScenarioResult(name="correction-append-only")
    res.add("=== same-week correction is append-only ===")

    mailed = pub.import_mailed(
        week="W1",
        group=FLOOR,
        constraint=Constraint.exact(config.group_covers(FLOOR), 1, label="mailed"),
    )
    res.add(f"\nmailed (original): {mailed.describe()}")
    res.add("a correction arrives, saying the floor figure was mis-tallied:")
    # The correction is APPENDED.  It cannot delete the earlier exact figure.
    correction = ledger.record(
        week="W1",
        group=FLOOR,
        constraint=Constraint.range(config.group_covers(FLOOR), 0, 3),
        origin="correction",
        requested_exact=False,
        requester="correction",
    )
    res.add(f"appended (correction): {correction.describe()}")
    res.add(_public_snapshot(ledger, "W1"))

    still_there = any(
        r.receipt_id == mailed.receipt_id for r in ledger.for_week("W1")
    )
    res.add(
        f"\noriginal mailed receipt still public? {still_there}  "
        "(a correction appends; it never retracts)"
    )
    res.data = {
        "original_still_public": still_there,
        "receipt_count": len(ledger.for_week("W1")),
        "origins": [r.origin for r in ledger.for_week("W1")],
    }
    return res


# ---------------------------------------------------------------------------
# Late discovery: both reports already mailed before an omission is noticed
# ---------------------------------------------------------------------------
def run_late_discovery() -> ScenarioResult:
    config, answers = sample_week2()
    ledger = Ledger()
    pub = Publisher(ledger, config, answers, policy="arrival")
    res = ScenarioResult(name="late-discovery-both-mailed")
    res.add("=== late discovery: both reports already mailed ===")
    res.add("an omission is noticed only after BOTH exact reports left the building")

    m_floor = pub.import_mailed(
        week="W2",
        group=FLOOR,
        constraint=Constraint.exact(config.group_covers(FLOOR), 1, label="mailed"),
    )
    m_north = pub.import_mailed(
        week="W2",
        group=NORTH,
        constraint=Constraint.exact(config.group_covers(NORTH), 1, label="mailed"),
    )
    res.add(f"\nmailed: {m_floor.describe()}")
    res.add(f"mailed: {m_north.describe()}")
    res.add(_public_snapshot(ledger, "W2"))

    derived = forced_answers(config, ledger.constraints_for_week("W2"))
    res.add("\naudit against public info only:")
    res.add(_derived_line(config, ledger, "W2"))

    if BING in derived:
        note = {
            "week": "W2",
            "finding": "leak",
            "detail": (
                "both exact counts were mailed; 甲+乙+丙=1 and 甲+乙=1 derive "
                f"丙={derived[BING]} (无噪声)"
            ),
            "derived": dict(derived),
        }
        ledger.note_audit(note)
        res.add(
            "\nrecorded faithfully in the audit log: 丙's answer is now derivable "
            "as 无噪声 (0).  This cannot be undone by any correction."
        )
    res.add(f"\naudit log entries: {len(ledger.audit_log())}")
    res.data = {
        "derived": derived,
        "audit_log": ledger.audit_log(),
        "cuang_derived": BING in derived,
    }
    return res


# ---------------------------------------------------------------------------
# Alternative service: pre-agreed non-overlapping reservation
# ---------------------------------------------------------------------------
def run_preagreed(reserve: str = NORTH) -> ScenarioResult:
    """Pre-agree to keep exact precision for one group only.

    The 北翼 and 整层 groups overlap at 甲/乙, so at most one of them can be
    reserved for exact output; the other is always served as a reference range.
    """
    config, answers = sample_week1()
    ledger = Ledger()
    pub = Publisher(
        ledger, config, answers, policy="preagreed", reserved_groups=(reserve,)
    )
    desk = BookingDesk(ledger, config)
    res = ScenarioResult(name=f"preagreed/reserve-{reserve}")
    res.add(f"=== pre-agreed policy: reserve exact precision for {reserve} ===")
    res.add("the other group is served only as a truthfully-labelled range")

    outcomes = {}
    for who in (TENANT, PROPERTY):
        grp = NORTH if who == TENANT else FLOOR
        out = pub.publish(week="W1", group=grp, requester=who, want_exact=True)
        outcomes[who] = out
        res.add(f"\n{who} requests {grp} exact:")
        res.add(f"  -> {out.describe()}")

    res.add("")
    res.add(_public_snapshot(ledger, "W1"))
    res.add(_derived_line(config, ledger, "W1"))

    bookings = {}
    for who in (TENANT, PROPERTY):
        out = outcomes[who]
        slot = "入户时段" if who == TENANT else "入楼检修"
        bk = desk.attempt(
            receipt_id=out.receipt_id, requester=who, slot=slot, week="W1",
            all_slots={slot},
        )
        bookings[who] = bk
    res.add("\nbooking attempts:")
    for who, bk in bookings.items():
        res.add(_booking_line(bk))

    res.data = {
        "reserved": reserve,
        "outcomes": {w: o.__dict__ for w, o in outcomes.items()},
        "who_got_exact": [w for w, o in outcomes.items() if o.got_exact],
        "who_booked": [w for w, b in bookings.items() if b.booked],
        "derived": forced_answers(config, ledger.constraints_for_week("W1")),
    }
    return res


# ---------------------------------------------------------------------------
# Generic acceptance week: re-enter at most eight households
# ---------------------------------------------------------------------------
def run_generic_week(
    config: WeekConfig,
    answers: Mapping[str, int],
    requests: Sequence[Tuple[str, str]],
    *,
    policy: str = "arrival",
    reserved_groups: Optional[Sequence[str]] = None,
    bookings: Sequence[Tuple[str, str, str]] = (),
) -> ScenarioResult:
    """Run an arbitrary week of at most eight households.

    ``requests`` is a sequence of ``(requester, group)`` exact requests, served
    in order.  ``bookings`` optionally is a sequence of ``(requester, group,
    slot)`` booking attempts made from the latest receipt that requester got
    for that group.  Returns a :class:`ScenarioResult` whose ``data`` includes
    the final public state, the derivable set, and booking outcomes.
    """
    if len(config.households) > MAX_HOUSEHOLDS:
        raise PrivacyError(
            f"acceptance allows at most {MAX_HOUSEHOLDS} households, "
            f"got {len(config.households)}"
        )
    ledger = Ledger()
    pub = Publisher(
        ledger, config, answers, policy=policy, reserved_groups=reserved_groups
    )
    desk = BookingDesk(ledger, config)
    res = ScenarioResult(name=f"generic/{config.week_id}")
    res.add(f"=== generic week {config.week_id} ({len(config.households)} households) ===")
    res.add(f"groups: {dict(config.groups)}")
    res.add(f"truth (private): {answers}")

    outcomes = []
    latest_receipt: Dict[Tuple[str, str], str] = {}
    for requester, group in requests:
        out = pub.publish(
            week=config.week_id, group=group, requester=requester, want_exact=True
        )
        outcomes.append(out)
        if out.receipt_id:
            latest_receipt[(requester, group)] = out.receipt_id
        res.add(f"\n{requester} -> {group}: {out.describe()}")
        res.add(_derived_line(config, ledger, config.week_id))

    booking_rows = []
    for requester, group, slot in bookings:
        rid = latest_receipt.get((requester, group))
        if rid is None:
            res.add(f"\n{requester} tries to book {slot}: no receipt for {group}")
            booking_rows.append(
                {"requester": requester, "group": group, "slot": slot,
                 "status": "refused", "reason": "no receipt held"}
            )
            continue
        bk = desk.attempt(
            receipt_id=rid, requester=requester, slot=slot,
            week=config.week_id, all_slots={slot},
        )
        booking_rows.append(bk.to_dict())
        res.add(f"\n{requester} tries to book {slot}:")
        res.add(_booking_line(bk))

    res.add("")
    res.add(_public_snapshot(ledger, config.week_id))
    res.data = {
        "week": config.week_id,
        "outcomes": [o.__dict__ for o in outcomes],
        "who_got_exact": [o.requester for o in outcomes if o.got_exact],
        "derived": forced_answers(config, ledger.constraints_for_week(config.week_id)),
        "receipts": ledger.to_list(),
        "bookings": booking_rows,
        "who_booked": [b["requester"] for b in booking_rows if b["status"] == "booked"],
        "safe": all(o.safe_after for o in outcomes),
    }
    return res
