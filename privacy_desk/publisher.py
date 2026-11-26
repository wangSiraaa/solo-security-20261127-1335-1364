"""Publishing desk.

The publisher is the only component that knows the *true* weekly answers (it
imports the raw return slips).  It uses them solely to issue **truthful**
numbers/ranges; nothing it publishes is ever allowed to leave a protected
household with fewer than two possible answers, judged against the union of all
public receipts for the week.

Two service policies are supported, matching the brief:

``arrival`` (default)
    Process requests in arrival order.  Each request first tries its exact
    count; if that exact count would expose anyone *given everything already
    public*, the desk falls back to the tightest truthfully-labelled range and
    the requester keeps only a reference.  Whoever is served first keeps the
    usable precision; the later party bears the cost.

``preagreed``
    Reserve exact precision for a pre-agreed set of pairwise non-overlapping
    groups (typically the ones with booking value).  Requests for a reserved
    group are served exact whenever possible; conflicting later requests are
    downgraded.  ``reserved_groups`` must be pairwise disjoint, else two exact
    counts over overlapping groups could compose into a leak.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence

from .ledger import Ledger
from .model import Constraint, PrivacyError, WeekConfig
from .privacy import (
    exact_is_safe,
    is_safe,
    range_is_blank,
    tightest_safe_range,
)


@dataclass
class PublishOutcome:
    """What the desk actually did with one request."""

    group: str
    requester: str
    requested_exact: bool
    receipt_id: Optional[str]
    kind: Optional[str]  # "exact" | "range" | None if refused
    value: Optional[int] = None
    lo: Optional[int] = None
    hi: Optional[int] = None
    got_exact: bool = False
    downgraded: bool = False
    blank: bool = False
    reference_only: bool = False
    refused: bool = False
    reason: str = ""
    derived: Mapping[str, int] = field(default_factory=dict)
    safe_after: bool = True

    def describe(self) -> str:
        if self.refused:
            return f"[{self.requester}/{self.group}] REFUSED: {self.reason}"
        if self.got_exact:
            tag = "exact"
            body = f"count = {self.value}"
        else:
            tag = "range"
            body = f"count in [{self.lo}, {self.hi}]" + (" (blank)" if self.blank else "")
        extra = ""
        if self.downgraded:
            extra = " [downgraded from exact request]"
        return f"[{self.requester}/{self.group}] {tag}: {body}{extra}  <{self.reason}>"


class Publisher:
    def __init__(
        self,
        ledger: Ledger,
        config: WeekConfig,
        answers: Mapping[str, int],
        *,
        policy: str = "arrival",
        reserved_groups: Optional[Sequence[str]] = None,
    ) -> None:
        self.ledger = ledger
        self.config = config
        self._answers = dict(answers)
        self.policy = policy
        self.reserved_groups = tuple(reserved_groups or ())
        # validate ground truth is complete and binary
        for h in config.households:
            if h not in self._answers:
                raise PrivacyError(f"missing raw answer for household {h!r}")
            if self._answers[h] not in (0, 1):
                raise PrivacyError(f"answer for {h!r} must be 0 or 1")
        if policy == "preagreed":
            self._validate_reserved()

    # -- helpers ----------------------------------------------------------
    def _validate_reserved(self) -> None:
        seen: Dict[str, str] = {}
        for g in self.reserved_groups:
            covers = self.config.group_covers(g)
            for h in covers:
                if h in seen and seen[h] != g:
                    raise PrivacyError(
                        f"reserved groups {seen[h]!r} and {g!r} overlap at {h!r}; "
                        "exact counts over overlapping groups can compose into a leak"
                    )
                seen[h] = g

    def _true_count(self, group: str) -> int:
        covers = self.config.group_covers(group)
        return sum(self._answers[h] for h in covers)

    def _public_constraints(self, week: str) -> List[Constraint]:
        # The union of everything public this week: published + mailed + corrections.
        return self.ledger.constraints_for_week(week)

    # -- main entry -------------------------------------------------------
    def publish(
        self,
        *,
        week: str,
        group: str,
        requester: str,
        want_exact: bool = True,
        force_reference: bool = False,
    ) -> PublishOutcome:
        """Serve one publication request against the current public state."""
        covers = self.config.group_covers(group)
        prior = self._public_constraints(week)
        true_value = self._true_count(group)
        reserved = (
            self.policy != "preagreed"
            or not self.reserved_groups
            or group in self.reserved_groups
        )

        # If the public state is already unsafe, we may not add more numbers.
        if not is_safe(self.config, prior):
            return PublishOutcome(
                group=group,
                requester=requester,
                requested_exact=want_exact,
                receipt_id=None,
                kind=None,
                refused=True,
                reason="public state is already unsafe; no further publication is safe",
                safe_after=False,
            )

        # 1) Try exact when allowed.
        if want_exact and not force_reference and reserved:
            if exact_is_safe(self.config, prior, group, true_value):
                receipt = self.ledger.record(
                    week=week,
                    group=group,
                    constraint=Constraint.exact(covers, true_value, label=f"group:{group}"),
                    origin="published",
                    requested_exact=True,
                    requester=requester,
                )
                return PublishOutcome(
                    group=group,
                    requester=requester,
                    requested_exact=True,
                    receipt_id=receipt.receipt_id,
                    kind="exact",
                    value=true_value,
                    got_exact=True,
                    reason="exact count is safe on its own given current public info",
                    safe_after=True,
                )

        # 2) Fall back to the tightest truthfully-labelled safe range.
        #    min_width=1 so a "range" is never an exact count in disguise.
        #    Under the pre-agreed policy, a non-reserved range must also leave
        #    every reserved group able to be published exactly afterwards.
        accept = None
        if self.policy == "preagreed" and self.reserved_groups:
            others = [g for g in self.reserved_groups if g != group]
            # Only preserve exactness that still *exists* under prior public
            # info.  If a reserved group is already spoiled (e.g. a mailed
            # exact compromised it), a non-reserved range must not be refused
            # for failing to restore the impossible.
            still_exact_capable = [
                g for g in others
                if exact_is_safe(self.config, prior, g, self._true_count(g))
            ]

            def accept(cand: Constraint) -> bool:
                for g in still_exact_capable:
                    val = self._true_count(g)
                    if not exact_is_safe(self.config, prior + [cand], g, val):
                        return False
                return True

        try:
            rng = tightest_safe_range(
                self.config, prior, group, true_value, min_width=1, accept=accept
            )
        except PrivacyError as exc:
            return PublishOutcome(
                group=group,
                requester=requester,
                requested_exact=want_exact,
                receipt_id=None,
                kind=None,
                refused=True,
                reason=str(exc),
                safe_after=False,
            )

        receipt = self.ledger.record(
            week=week,
            group=group,
            constraint=rng,
            origin="published",
            requested_exact=want_exact,
            requester=requester,
        )
        blank = range_is_blank(self.config, group, rng)
        if want_exact:
            if reserved:
                reason = (
                    "exact count would expose a protected household given public info; "
                    "issued tightest safe range instead"
                )
            else:
                reason = (
                    f"group {group!r} is not reserved for exact output under the "
                    "pre-agreed policy; served as a reference range"
                )
        else:
            reason = "reference range requested"
        if blank:
            reason += " (range excludes nothing: blank)"
        return PublishOutcome(
            group=group,
            requester=requester,
            requested_exact=want_exact,
            receipt_id=receipt.receipt_id,
            kind="range",
            lo=rng.lo,
            hi=rng.hi,
            got_exact=False,
            downgraded=want_exact,
            blank=blank,
            reference_only=True,
            reason=reason,
            safe_after=is_safe(self.config, self._public_constraints(week)),
        )

    # -- importing already-mailed paper reports ---------------------------
    def import_mailed(
        self,
        *,
        week: str,
        group: str,
        constraint: Constraint,
        note: str = "",
    ):
        """Record a paper report that was already mailed out.

        Mailed numbers are public the moment they left the building: they are
        appended unconditionally, even if they make the state unsafe.  Callers
        that care about a subsequent leak use :meth:`detect_breach`.
        """
        receipt = self.ledger.record(
            week=week,
            group=group,
            constraint=constraint,
            origin="mailed",
            requested_exact=constraint.is_exact(),
            requester="mail",
        )
        return receipt

    # -- after-the-fact safety audit --------------------------------------
    def detect_breach(self, week: str) -> Dict[str, int]:
        """Households derivable from public info alone (should normally be empty)."""
        from .privacy import forced_answers

        return forced_answers(self.config, self._public_constraints(week))
