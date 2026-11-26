#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Local mail-sending and relay simulator for a shared dormitory relay box.

Story
-----
A duty dormitory receives private mail through one shared relay box.  The
corridor record only shows *which beat a bag with how many slots went by*;
recipient names and message bodies stay sealed inside the bag.  Within one
shift the corridor record must not reveal whether anybody sent mail, nor who
asked for help during the first two beats.

The module has three cooperating pieces:

``Simulator``
    A local simulator driven by the office (posted letters), a simulated beat
    clock, and the dormitory's signing.  It exposes two *separate* views:
    ``corridor`` (what an outside observer sees) and ``outcomes`` (each
    recipient's own delivery record).

``Session`` / ``DutyPlan``
    The planning layer.  A shift publishes public transfer windows (beat +
    bag type + capacity + visible size) and per-recipient service promises
    (earliest arrival / hard deadline).  A plan is decided *before* the shift
    and replayed unchanged under every incoming-mail case, so the corridor
    sees identical beats and sizes in all of them.

``CustodyLedger``
    Bag receipts bind a letter to the serial *and* round *and* load beat of
    the bag that actually carried it.  A receipt therefore cannot vouch for a
    different shift's bag, for a bag from another round, or for a letter that
    was "loaded" before it even existed.

The standard shift's two limits cannot both be met
--------------------------------------------------
Four beats, one bag per shift, capacity two.  A (if sent) is present from
beat 1 and must be handed over by the end of beat 2.  B arrives earliest at
beat 3 and is handed over at the end of beat 4.  One bag therefore either
goes early (saves A; B is missed) or goes late (saves B; A gets a late
letter).  ``plan_shift`` never claims both and reports the other recipient's
real outcome.

Run ``python3 mailbox_sim.py`` for a demonstration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Fixed facts about the "standard" night shift.
# ---------------------------------------------------------------------------

RECIPIENT_A = "A"
RECIPIENT_B = "B"

STANDARD_BEATS = 4          # the night shift has four beats (四拍)
ARRIVAL_A = 1               # A's letter, if it comes, is present from beat 1
DEADLINE_A = 2              # A must be handed over by the end of beat 2
ARRIVAL_B = 3               # B's letter arrives earliest at beat 3
DEADLINE_B = 4              # B is handed over at the end of beat 4

__all__ = [
    "RECIPIENT_A", "RECIPIENT_B", "STANDARD_BEATS",
    "ARRIVAL_A", "DEADLINE_A", "ARRIVAL_B", "DEADLINE_B",
    "Letter", "Service", "Window", "PlannedBag", "DutyPlan",
    "CorridorLog", "Bag", "BagReceipt", "ReleaseResult", "CustodyLedger",
    "RecipientOutcome", "SimResult", "Simulator", "simulate",
    "PlanResult", "plan_shift", "design_plan",
    "ReplayReport", "replay_plan", "all_cases",
    "Session", "Cancellation",
    "standard_session", "alternate_session", "b_only_session",
    "main",
]


def _digest(recipient: str, body: str) -> str:
    import hashlib
    return hashlib.sha256((recipient + "\x1f" + body).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Core value types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Letter:
    """A sealed private letter produced by the office at ``produced_beat``."""

    recipient: str
    body: str
    produced_beat: int

    def __post_init__(self) -> None:
        if not self.recipient:
            raise ValueError("letter.recipient must be non-empty")
        if self.produced_beat < 1:
            raise ValueError("letter.produced_beat must be >= 1")

    @property
    def digest(self) -> str:
        return _digest(self.recipient, self.body)


@dataclass(frozen=True)
class Service:
    """A recipient's delivery promise: earliest arrival and hard deadline.

    A delivery is ``on_time`` when its beat lies in ``[arrival, deadline]``,
    ``late`` when it is later, and ``missed`` when there is none.
    """

    recipient: str
    arrival: int
    deadline: int

    def __post_init__(self) -> None:
        if self.arrival < 1:
            raise ValueError("service.arrival must be >= 1")
        if self.deadline < self.arrival:
            raise ValueError("service.deadline must be >= arrival")

    def covers(self, beat: int) -> bool:
        return self.arrival <= beat <= self.deadline


@dataclass(frozen=True)
class Window:
    """A *public* transfer window: one beat plus a bag type.

    ``capacity`` is how many letters the bag may carry; ``bag_size`` is the
    fixed number of slots the corridor sees (extra slots are blank fillers).
    """

    beat: int
    label: str
    capacity: int = 2
    bag_size: int = 2

    def __post_init__(self) -> None:
        if self.beat < 1:
            raise ValueError("window.beat must be >= 1")
        if not self.label:
            raise ValueError("window.label must be non-empty")
        if self.capacity < 1:
            raise ValueError("window.capacity must be >= 1")
        if self.bag_size < self.capacity:
            raise ValueError("window.bag_size must be >= capacity")


@dataclass(frozen=True)
class PlannedBag:
    """One bag fixed by the pre-shift plan.

    The corridor only ever sees ``(beat, size)``.  ``priority`` decides which
    arrived letters are loaded first when the bag is released at runtime;
    leftovers stay pending for a later bag or are reported missed.
    """

    beat: int
    label: str
    size: int
    capacity: int
    priority: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.capacity < 1:
            raise ValueError("planned bag.capacity must be >= 1")
        if self.size < self.capacity:
            raise ValueError("planned bag.size must be >= capacity")


@dataclass(frozen=True)
class DutyPlan:
    """A complete pre-shift organization of one shift.

    It holds no reference to which letters will arrive, so the same plan is
    replayed under every incoming-mail case.
    """

    bags: Tuple[PlannedBag, ...]

    def __post_init__(self) -> None:
        beats = [bag.beat for bag in self.bags]
        if beats != sorted(beats) or len(set(beats)) != len(beats):
            raise ValueError("bags must be in strictly increasing beat order")

    def bag_at(self, beat: int) -> Optional[PlannedBag]:
        for bag in self.bags:
            if bag.beat == beat:
                return bag
        return None

    def has_capacity(self) -> bool:
        return any(bag.capacity >= 1 for bag in self.bags)

    def corridor_signature(self) -> Tuple[Tuple[int, int], ...]:
        """What the corridor could ever see: (beat, slot count) per bag."""
        return tuple((bag.beat, bag.size) for bag in self.bags)


# ---------------------------------------------------------------------------
# Append-only corridor record
# ---------------------------------------------------------------------------


class CorridorLog:
    """Append-only corridor record.

    A beat that already happened cannot be un-seen: ``erase`` always fails.
    This models "事后删日志不会抹掉已发生的经过记录" (deleting logs later does
    not erase the passage record that already happened).
    """

    def __init__(self, records: Iterable[Tuple[int, int]] = ()) -> None:
        self._records: List[Tuple[int, int]] = list(records)

    def append(self, beat: int, size: int) -> None:
        if self._records and beat < self._records[-1][0]:
            raise ValueError("corridor log must stay in beat order")
        self._records.append((int(beat), int(size)))

    def records(self) -> Tuple[Tuple[int, int], ...]:
        return tuple(self._records)

    def __len__(self) -> int:
        return len(self._records)

    def __iter__(self):
        return iter(self._records)

    def erase(self, *_args, **_kwargs):
        raise PermissionError(
            "corridor history already happened; it cannot be erased"
        )


# ---------------------------------------------------------------------------
# Custody: bags, receipts, and dormitory signing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Bag:
    """A physical bag instance with a serial, a round, and sealed contents."""

    shift_id: str
    serial: str
    beat: int
    size: int
    capacity: int
    contents: Tuple[Letter, ...] = ()
    loaded_beat: int = 0

    @property
    def digests(self) -> Tuple[str, ...]:
        return tuple(letter.digest for letter in self.contents)

    def contains(self, letter: Letter) -> bool:
        return any(c.digest == letter.digest for c in self.contents)

    def filler_count(self) -> int:
        return self.size - len(self.contents)


@dataclass(frozen=True)
class BagReceipt:
    """A dormitory pickup receipt for one release of one bag.

    The receipt binds the bag *serial*, the *round*, the *load beat*, and the
    digests of the letters actually loaded.  Nothing outside that binding can
    be signed for with this receipt.
    """

    shift_id: str
    serial: str
    beat: int
    load_beat: int
    digests: Tuple[str, ...]

    @classmethod
    def from_bag(cls, bag: Bag) -> "BagReceipt":
        return cls(
            shift_id=bag.shift_id,
            serial=bag.serial,
            beat=bag.beat,
            load_beat=bag.loaded_beat,
            digests=bag.digests,
        )


@dataclass(frozen=True)
class ReleaseResult:
    signed: bool
    recipient: Optional[str]
    beat: Optional[int]
    reason: str


class CustodyLedger:
    """Tracks bags and receipts and decides what the dormitory may sign for.

    ``current_shift`` (when set) pins which shift's bags may be signed for,
    so a receipt from an earlier shift cannot be presented as if it were
    tonight's -- ``set_shift`` opens each shift at its start.
    """

    def __init__(self, current_shift: Optional[str] = None) -> None:
        self._bags: Dict[str, Bag] = {}
        self._receipts: List[BagReceipt] = []
        self._current_shift: Optional[str] = current_shift

    def set_shift(self, shift_id: str) -> None:
        self._current_shift = shift_id

    def register(self, bag: Bag) -> Bag:
        if bag.serial in self._bags and self._bags[bag.serial] != bag:
            raise ValueError(f"bag serial {bag.serial!r} already registered")
        self._bags[bag.serial] = bag
        return bag

    def bag(self, serial: str) -> Bag:
        return self._bags[serial]

    def issue(self, bag: Bag) -> BagReceipt:
        """Issue a receipt for a bag that was actually released."""
        self.register(bag)
        receipt = BagReceipt.from_bag(bag)
        self._receipts.append(receipt)
        return receipt

    def release(
        self, receipt: BagReceipt, presented: Bag, letter: Letter
    ) -> ReleaseResult:
        """Try to sign ``letter`` off ``presented`` using ``receipt``.

        Succeeds only when the presented bag is the very bag the receipt was
        issued for (same serial and same round), the letter's digest is bound
        to that receipt, the letter is really inside the bag, and the bag was
        not loaded before the letter was produced.
        """
        if presented.serial != receipt.serial:
            return ReleaseResult(
                False, None, None,
                f"wrong bag: presented {presented.serial!r}, "
                f"receipt is for {receipt.serial!r}",
            )
        if presented.shift_id != receipt.shift_id:
            return ReleaseResult(
                False, None, None,
                f"wrong shift: bag belongs to {presented.shift_id!r}, "
                f"receipt is for {receipt.shift_id!r}",
            )
        if (
            self._current_shift is not None
            and receipt.shift_id != self._current_shift
        ):
            return ReleaseResult(
                False, None, None,
                f"not tonight's shift: receipt is for {receipt.shift_id!r}, "
                f"current shift is {self._current_shift!r}",
            )
        if presented.beat != receipt.beat:
            return ReleaseResult(
                False, None, None,
                f"wrong round: presented beat {presented.beat}, "
                f"receipt is for beat {receipt.beat}",
            )
        if presented.loaded_beat != receipt.load_beat:
            return ReleaseResult(
                False, None, None,
                f"load-beat mismatch: presented bag says {presented.loaded_beat}, "
                f"receipt says {receipt.load_beat}",
            )
        if letter.digest not in receipt.digests:
            return ReleaseResult(
                False, letter.recipient, receipt.beat,
                "letter was not loaded into this bag",
            )
        if not presented.contains(letter):
            return ReleaseResult(
                False, letter.recipient, receipt.beat,
                "letter is not actually inside the presented bag",
            )
        if receipt.load_beat < letter.produced_beat:
            return ReleaseResult(
                False, letter.recipient, receipt.beat,
                f"loaded at beat {receipt.load_beat}, before the letter was "
                f"produced at beat {letter.produced_beat}",
            )
        return ReleaseResult(
            True, letter.recipient, receipt.beat,
            f"signed for {letter.recipient} from bag {receipt.serial!r}",
        )

    def received(self, receipt: BagReceipt, presented: Bag, letter: Letter) -> bool:
        """Whether the dormitory may truthfully report the letter received."""
        return self.release(receipt, presented, letter).signed


# ---------------------------------------------------------------------------
# Live simulator: office -> relay box -> dormitory
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RecipientOutcome:
    """Home view for one recipient: my own letter's fate."""

    recipient: str
    arrived: bool
    delivered: bool
    beat: Optional[int]
    kind: str  # "on_time" | "late" | "missed" | "none"

    @property
    def deadline_met(self) -> bool:
        return self.kind == "on_time"


@dataclass(frozen=True)
class SimResult:
    """Both views, kept strictly apart."""

    case: FrozenSet[str]
    corridor: Tuple[Tuple[int, int], ...]   # (beat, slot count)
    outcomes: Tuple[RecipientOutcome, ...]
    bags: Tuple[Bag, ...]
    receipts: Tuple[BagReceipt, ...]
    rejected: Tuple[Tuple[int, str], ...]   # (beat, reason) for refused loads
    help_requests: Tuple[Tuple[str, int], ...] = ()  # private: (recipient, beat)

    def outcome(self, recipient: str) -> RecipientOutcome:
        for outcome in self.outcomes:
            if outcome.recipient == recipient:
                return outcome
        raise KeyError(recipient)

    def corridor_log(self) -> CorridorLog:
        return CorridorLog(self.corridor)


STANDARD_SERVICES: Tuple[Service, ...] = (
    Service(RECIPIENT_A, ARRIVAL_A, DEADLINE_A),
    Service(RECIPIENT_B, ARRIVAL_B, DEADLINE_B),
)


class Simulator:
    """Local simulator: simulated clock, office posting, dormitory signing.

    ``services`` supplies each recipient's arrival/deadline promise.  The
    office pre-produces only the letters named in ``case``; a planned bag
    simply carries whatever has arrived and is still pending, padded with
    blank fillers up to its fixed size.
    """

    def __init__(
        self,
        shift_id: str = "shift",
        services: Sequence[Service] = STANDARD_SERVICES,
    ) -> None:
        self.shift_id = shift_id
        self.services: Dict[str, Service] = {s.recipient: s for s in services}

    def run(
        self,
        plan: DutyPlan,
        case: Iterable[str],
        *,
        shift_id: Optional[str] = None,
        help_requests: Iterable[Tuple[str, int]] = (),
    ) -> SimResult:
        case = frozenset(case)
        unknown = case - set(self.services)
        if unknown:
            raise ValueError(f"unknown recipients: {sorted(unknown)}")
        if shift_id is not None:
            self.shift_id = shift_id

        # Private help requests (who asked, on which beat).  These never touch
        # the corridor: a recipient raising a hand during the first two beats
        # must be indistinguishable from a silent night outside.
        requests = tuple((str(r), int(b)) for r, b in help_requests)

        # The office pre-produces only the letters that will actually come.
        pending: Dict[str, Letter] = {
            r: Letter(r, f"letter-for-{r}", self.services[r].arrival)
            for r in case
        }

        ledger = CustodyLedger()
        corridor = CorridorLog()
        delivered: Dict[str, Tuple[int, str]] = {}
        bags: List[Bag] = []
        receipts: List[BagReceipt] = []
        rejected: List[Tuple[int, str]] = []

        last_beat = max([STANDARD_BEATS] + [b.beat for b in plan.bags])
        for beat in range(1, last_beat + 1):
            planned = plan.bag_at(beat)
            if planned is None:
                # No bag this beat: the corridor observes nothing, so nothing
                # is appended.  Entry presence is fixed by the plan alone and
                # can never depend on which letters arrived.
                continue
            # A bag of a fixed size passes; its contents never change what is
            # written to the corridor.
            corridor.append(beat, planned.size)

            loaded: List[Letter] = []
            # Load arrived, still-pending letters in priority order, then the
            # rest, up to capacity.  A letter never enters before it exists.
            order = list(planned.priority)
            order += [r for r in sorted(pending) if r not in order]
            for recipient in order:
                letter = pending.get(recipient)
                if letter is None:
                    continue
                if letter.produced_beat > beat:
                    continue
                if len(loaded) >= planned.capacity:
                    rejected.append((beat, f"{recipient}: bag full "
                                           f"(capacity {planned.capacity})"))
                    continue
                loaded.append(letter)
                del pending[recipient]

            bag = Bag(
                shift_id=self.shift_id,
                serial=f"{self.shift_id}-b{beat}",
                beat=beat,
                size=planned.size,
                capacity=planned.capacity,
                contents=tuple(loaded),
                loaded_beat=beat,
            )
            ledger.register(bag)
            bags.append(bag)
            receipt = ledger.issue(bag)
            receipts.append(receipt)

            # The dormitory signs each loaded letter against this release.
            for letter in loaded:
                res = ledger.release(receipt, bag, letter)
                if res.signed:
                    delivered[letter.recipient] = (
                        beat, self._kind(self.services[letter.recipient], beat)
                    )
                else:
                    rejected.append((beat, res.reason))

        outcomes = self._outcomes(case, delivered)
        return SimResult(
            case=case,
            corridor=corridor.records(),
            outcomes=outcomes,
            bags=tuple(bags),
            receipts=tuple(receipts),
            rejected=tuple(rejected),
            help_requests=requests,
        )

    @staticmethod
    def _kind(service: Service, beat: int) -> str:
        return "on_time" if service.covers(beat) else "late"

    @staticmethod
    def _outcomes(
        case: FrozenSet[str], delivered: Mapping[str, Tuple[int, str]]
    ) -> Tuple[RecipientOutcome, ...]:
        outcomes: List[RecipientOutcome] = []
        for recipient in sorted(case):
            if recipient in delivered:
                beat, kind = delivered[recipient]
                outcomes.append(RecipientOutcome(recipient, True, True, beat, kind))
            else:
                outcomes.append(RecipientOutcome(recipient, True, False, None, "missed"))
        return tuple(outcomes)


def simulate(
    plan: DutyPlan,
    case: Iterable[str],
    shift_id: str = "shift",
    services: Sequence[Service] = STANDARD_SERVICES,
    help_requests: Iterable[Tuple[str, int]] = (),
) -> SimResult:
    return Simulator(shift_id, services).run(
        plan, case, help_requests=help_requests
    )


# ---------------------------------------------------------------------------
# Planning: decide one shift before it happens, then replay every case
# ---------------------------------------------------------------------------

CASES: Tuple[FrozenSet[str], ...] = (
    frozenset(),
    frozenset({RECIPIENT_A}),
    frozenset({RECIPIENT_B}),
    frozenset({RECIPIENT_A, RECIPIENT_B}),
)


def all_cases() -> Tuple[FrozenSet[str], ...]:
    return CASES


def _cases_with(recipient: str) -> Tuple[FrozenSet[str], ...]:
    """The two incoming-mail cases in which this recipient did send a letter."""
    return tuple(case for case in CASES if recipient in case)


@dataclass(frozen=True)
class PlanResult:
    """Outcome of ``plan_shift``: the plan plus honest per-recipient reports."""

    plan: DutyPlan
    target: Optional[str]
    target_met: bool
    other: Optional[str]
    other_outcome: RecipientOutcome
    note: str


def plan_shift(
    target: str = RECIPIENT_B,
    *,
    shift_id: str = "shift",
    services: Sequence[Service] = STANDARD_SERVICES,
) -> PlanResult:
    """Choose ONE bag organization that meets ``target``'s deadline.

    Because the whole shift allows only one bag, the target's deadline and
    the other recipient's deadline cannot both be met.  This function picks
    the bag beat that meets ``target`` and honestly reports what the other
    recipient gets.

    * target A -> bag on beat 2 (capacity 2).  A is on time; B, arriving at
      beat 3, is missed.
    * target B -> bag on beat 4 (capacity 2).  B is on time; A, if present,
      goes along and arrives *late* (beat 4 > its deadline 2).
    """
    if target not in (RECIPIENT_A, RECIPIENT_B):
        raise ValueError(f"target must be {RECIPIENT_A!r} or {RECIPIENT_B!r}")

    svc = {s.recipient: s for s in services}
    other = RECIPIENT_B if target == RECIPIENT_A else RECIPIENT_A
    target_svc = svc[target]

    # The one bag is placed on the target's deadline beat, carrying up to 2.
    beat = target_svc.deadline
    priority = (target, other)
    bag = PlannedBag(
        beat=beat,
        label=f"{shift_id}-single-bag",
        size=2,
        capacity=2,
        priority=priority,
    )
    plan = DutyPlan(bags=(bag,))

    # Replay the two relevant cases to produce honest reports.
    case_with_other = frozenset({target, other})
    sim = Simulator(shift_id, services)
    res_both = sim.run(plan, case_with_other)
    other_outcome = res_both.outcome(other)
    target_outcome = res_both.outcome(target)
    target_met = target_outcome.deadline_met

    if target == RECIPIENT_A:
        note = (
            "bag released on beat 2: A on time; B arrives only at beat 3 and "
            "is missed"
        )
    else:
        note = (
            "bag released on beat 4: B on time; A (if present) is carried but "
            "arrives late (beat 4 > deadline 2)"
        )

    return PlanResult(
        plan=plan,
        target=target,
        target_met=target_met,
        other=other,
        other_outcome=other_outcome,
        note=note,
    )


def design_plan(
    windows: Sequence[Window],
    max_bags: int = 1,
    *,
    target: str = RECIPIENT_B,
    shift_id: str = "shift",
    services: Sequence[Service] = STANDARD_SERVICES,
) -> DutyPlan:
    """Turn public windows into a reusable plan under a bag-count rule.

    Only ``max_bags`` windows may be used.  The chosen windows are the ones
    latest in the shift that still let ``target`` be delivered on time; if
    one window alone is allowed, it is placed on the target's deadline.
    """
    if max_bags < 1:
        raise ValueError("max_bags must be >= 1")
    svc = {s.recipient: s for s in services}
    if target not in svc:
        raise ValueError(f"unknown target {target!r}")

    target_svc = svc[target]
    # Prefer windows that cover the target deadline; else the latest one.
    covering = [w for w in windows if w.beat <= target_svc.deadline]
    chosen_windows = sorted(
        covering or list(windows), key=lambda w: w.beat
    )[-max_bags:]

    bags = tuple(
        PlannedBag(
            beat=w.beat,
            label=w.label,
            size=w.bag_size,
            capacity=w.capacity,
            priority=(target,) + tuple(
                r for r in sorted(svc) if r != target
            ),
        )
        for w in chosen_windows
    )
    return DutyPlan(bags=bags)


# ---------------------------------------------------------------------------
# Replay: prove the corridor is identical across the four incoming cases
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReplayReport:
    """Result of replaying one plan under all four incoming-mail cases."""

    plan_signature: Tuple[Tuple[int, int], ...]
    case_results: Mapping[FrozenSet[str], SimResult]
    corridor_identical: bool
    corridors: Mapping[FrozenSet[str], Tuple[Tuple[int, int], ...]]

    def outcome(self, case: Iterable[str], recipient: str) -> RecipientOutcome:
        return self.case_results[frozenset(case)].outcome(recipient)


def replay_plan(
    plan: DutyPlan,
    *,
    shift_id: str = "shift",
    services: Sequence[Service] = STANDARD_SERVICES,
    cases: Sequence[FrozenSet[str]] = CASES,
) -> ReplayReport:
    """Replay the *same* plan under each case; assert identical corridors."""
    sim = Simulator(shift_id, services)
    results: Dict[FrozenSet[str], SimResult] = {}
    corridors: Dict[FrozenSet[str], Tuple[Tuple[int, int], ...]] = {}
    for case in cases:
        res = sim.run(plan, case, shift_id=shift_id)
        results[frozenset(case)] = res
        corridors[frozenset(case)] = res.corridor
    unique = {c for c in corridors.values()}
    return ReplayReport(
        plan_signature=plan.corridor_signature(),
        case_results=results,
        corridor_identical=len(unique) == 1,
        corridors=corridors,
    )


# ---------------------------------------------------------------------------
# Session: public windows + promises + cancellation handling
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Cancellation:
    """One public transfer window withdrawn by the dormitory supervisor."""

    beat: int
    label: str
    reason: str  # the reason itself is public information


@dataclass(frozen=True)
class Session:
    """A shift's public configuration and its chosen plan.

    ``windows`` are the *public* transfer windows.  ``services`` are each
    recipient's promises.  ``max_bags`` is the bag-count rule.  A plan chosen
    with ``plan()`` is stored and can be replayed.
    """

    shift_id: str
    windows: Tuple[Window, ...]
    services: Tuple[Service, ...]
    max_bags: int = 1
    target: str = RECIPIENT_B

    def service(self, recipient: str) -> Service:
        for s in self.services:
            if s.recipient == recipient:
                return s
        raise KeyError(recipient)

    def effective_windows(self, cancelled: Sequence[Cancellation] = ()) -> Tuple[Window, ...]:
        cancelled_keys = {(c.beat, c.label) for c in cancelled}
        return tuple(
            w for w in self.windows if (w.beat, w.label) not in cancelled_keys
        )

    def plan(self, cancelled: Sequence[Cancellation] = ()) -> DutyPlan:
        return design_plan(
            self.effective_windows(cancelled),
            max_bags=self.max_bags,
            target=self.target,
            shift_id=self.shift_id,
            services=self.services,
        )

    def replay(self, cancelled: Sequence[Cancellation] = ()) -> ReplayReport:
        return replay_plan(
            self.plan(cancelled),
            shift_id=self.shift_id,
            services=self.services,
        )

    def affected_by(self, cancellation: Cancellation) -> Tuple[str, ...]:
        """Which recipients' on-time promises must be revisited if this window goes.

        Computed by actually replaying the session with and without the
        cancellation: a recipient is affected when it was delivered on time
        in every case where it sent a letter *before*, but no longer is in
        at least one case *after*.  The cancellation reason itself is public
        and plays no part in this decision.
        """
        before = self.replay(())
        after = self.replay((cancellation,))
        affected: List[str] = []
        for s in self.services:
            for case in _cases_with(s.recipient):
                if before.outcome(case, s.recipient).deadline_met and not (
                    after.outcome(case, s.recipient).deadline_met
                ):
                    affected.append(s.recipient)
                    break
        return tuple(affected)


# ---------------------------------------------------------------------------
# Public sessions
# ---------------------------------------------------------------------------


def standard_session() -> Session:
    """The standard four-beat shift: one public window, one bag allowed."""
    return Session(
        shift_id="standard",
        windows=(Window(beat=4, label="night-bag", capacity=2, bag_size=2),),
        services=STANDARD_SERVICES,
        max_bags=1,
        target=RECIPIENT_B,
    )


def alternate_session() -> Session:
    """A different shift: two public windows with distinct bag types.

    Beat 2 carries a small (2-slot) bag; beat 3 carries a larger (3-slot)
    bag.  With ``max_bags=2`` both may pass.  This makes it possible to serve
    A on time (beat 2 window) and still carry B once it arrives (beat 3).
    """
    return Session(
        shift_id="alternate",
        windows=(
            Window(beat=2, label="early-small", capacity=2, bag_size=2),
            Window(beat=3, label="late-large", capacity=3, bag_size=3),
        ),
        services=STANDARD_SERVICES,
        max_bags=2,
        target=RECIPIENT_B,
    )


def b_only_session() -> Session:
    """A shift that only has to protect B (one 2-slot bag at beat 4)."""
    return Session(
        shift_id="b-only",
        windows=(Window(beat=4, label="night-bag", capacity=2, bag_size=2),),
        services=STANDARD_SERVICES,
        max_bags=1,
        target=RECIPIENT_B,
    )


# ---------------------------------------------------------------------------
# Demonstration
# ---------------------------------------------------------------------------


def _fmt_corridor(corr: Sequence[Tuple[int, int]]) -> str:
    return ", ".join(f"beat{beat}:{size}" for beat, size in corr) or "(none)"


def _fmt_outcome(outcome: RecipientOutcome) -> str:
    if not outcome.arrived:
        return f"{outcome.recipient}=none"
    if outcome.delivered:
        return f"{outcome.recipient}={outcome.kind}@beat{outcome.beat}"
    return f"{outcome.recipient}=missed"


def main() -> None:
    print("=== Standard shift: protect B, honestly report A ===")
    pr = plan_shift(RECIPIENT_B)
    print("plan:", pr.plan.corridor_signature())
    print("note:", pr.note)
    rep = replay_plan(pr.plan, shift_id="standard")
    print("corridor identical across cases:", rep.corridor_identical)
    for case in CASES:
        labels = ",".join(sorted(case)) or "none"
        corr = _fmt_corridor(rep.corridors[case])
        outs = " ".join(_fmt_outcome(o) for o in
                        rep.case_results[case].outcomes) or "(no letters)"
        print(f"  case[{labels:4}] corridor={corr:14} home: {outs}")

    print()
    print("=== Standard shift: protect A, honestly report B ===")
    pra = plan_shift(RECIPIENT_A)
    repa = replay_plan(pra.plan, shift_id="standard-A")
    for case in CASES:
        labels = ",".join(sorted(case)) or "none"
        outs = " ".join(_fmt_outcome(o) for o in
                        repa.case_results[case].outcomes) or "(no letters)"
        print(f"  case[{labels:4}] home: {outs}")

    print()
    print("=== Alternate shift: two windows, both deadlines ===")
    sess = alternate_session()
    altr = sess.replay()
    print("windows:", [(w.beat, w.label, w.bag_size) for w in sess.windows])
    print("plan:", altr.plan_signature, "identical:", altr.corridor_identical)
    for case in CASES:
        labels = ",".join(sorted(case)) or "none"
        outs = " ".join(_fmt_outcome(o) for o in
                        altr.case_results[case].outcomes) or "(no letters)"
        print(f"  case[{labels:4}] corridor={_fmt_corridor(altr.corridors[case])}"
              f" home: {outs}")

    print()
    print("=== Cancelling a window: which promise must move? ===")
    for cancel in (
        Cancellation(beat=2, label="early-small", reason="corridor work"),
        Cancellation(beat=3, label="late-large", reason="staffing"),
    ):
        moved = ", ".join(sess.affected_by(cancel)) or "nobody"
        print(f"  cancel {cancel.label!r}: affected -> {moved}")

    print()
    print("=== B-only shift: receipt correctness ===")
    bsession = b_only_session()
    plan = bsession.plan()
    res = simulate(plan, {RECIPIENT_B}, shift_id="b-only")
    print("B outcome:", _fmt_outcome(res.outcome(RECIPIENT_B)))
    bag = res.bags[-1]
    receipt = res.receipts[-1]
    ledger = CustodyLedger(current_shift="b-only")
    ledger.register(bag)
    b_letter = Letter(RECIPIENT_B, "letter-for-B", ARRIVAL_B)

    print("  valid receipt signs B:",
          ledger.received(receipt, bag, b_letter))

    # Bag supposedly loaded on beat 2, before B existed at beat 3.
    early = Bag(shift_id=bag.shift_id, serial=bag.serial, beat=bag.beat,
                size=bag.size, capacity=bag.capacity, contents=bag.contents,
                loaded_beat=2)
    early_receipt = BagReceipt(shift_id=early.shift_id, serial=early.serial,
                               beat=early.beat, load_beat=2, digests=early.digests)
    print("  bag loaded before B existed signs B:",
          ledger.received(early_receipt, early, b_letter))

    # The previous shift's beat-4 bag, also containing B, presented tonight.
    prev = Bag(shift_id="b-only-previous", serial="b-only-previous-b4", beat=4,
               size=2, capacity=2, contents=(b_letter,), loaded_beat=4)
    prev_receipt = BagReceipt.from_bag(prev)
    print("  previous shift's bag signs B:",
          ledger.received(prev_receipt, prev, b_letter))


if __name__ == "__main__":
    main()
