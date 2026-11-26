"""The privacy engine.

Everything here reasons over **public** artefacts only: a set of
:class:`~privacy_desk.model.Constraint` objects plus the public structure of
the week (which household belongs to which group, and how many households
exist).  The private true answers are NEVER consulted by these functions -- a
publisher may know them in order to issue truthful numbers, but an attacker
only ever sees constraints.

Definitions
-----------
*admissible assignment*
    A ``{household: 0|1}`` mapping consistent with every constraint.  ``1``
    means "has noise / complained", ``0`` means "no noise".

*household is safe*
    It has at least two possible answers across the admissible set.  If every
    admissible assignment gives it the same value, that value has been
    *derived* and the household is no longer protected.

*state is safe*
    The admissible set is non-empty and every protected household is safe.
"""

from __future__ import annotations

from itertools import product
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .model import Constraint, PrivacyError, WeekConfig


Assignment = Dict[str, int]


def _constraints_list(constraints: Iterable[Constraint]) -> List[Constraint]:
    return list(constraints)


def admissible_assignments(
    config: WeekConfig,
    constraints: Iterable[Constraint],
) -> List[Assignment]:
    """All binary assignments consistent with ``constraints``.

    Enumerates ``2**len(households)`` candidates; the acceptance bound is eight
    households, so this is at most 256 candidates.
    """
    cons = _constraints_list(constraints)
    households = config.households
    out: List[Assignment] = []
    for bits in product((0, 1), repeat=len(households)):
        assignment = dict(zip(households, bits))
        if all(c.holds(assignment) for c in cons):
            out.append(assignment)
    return out


def possible_values(
    config: WeekConfig,
    constraints: Iterable[Constraint],
    household: str,
) -> Set[int]:
    """The set of answers ``household`` can still have given public info."""
    if household not in config.households:
        raise PrivacyError(f"unknown household {household!r}")
    return {a[household] for a in admissible_assignments(config, constraints)}


def all_possible_values(
    config: WeekConfig,
    constraints: Iterable[Constraint],
) -> Dict[str, Set[int]]:
    adm = admissible_assignments(config, constraints)
    result: Dict[str, Set[int]] = {h: set() for h in config.protected}
    for a in adm:
        for h in config.protected:
            result[h].add(a[h])
    return result


def forced_answers(
    config: WeekConfig,
    constraints: Iterable[Constraint],
    protected: Optional[Sequence[str]] = None,
) -> Dict[str, int]:
    """Households whose answer is now uniquely derivable -> that answer."""
    protected = protected if protected is not None else config.protected
    values = all_possible_values(config, constraints)
    forced: Dict[str, int] = {}
    for h in protected:
        vals = values.get(h, set())
        if len(vals) == 1:
            forced[h] = next(iter(vals))
    return forced


def is_safe(
    config: WeekConfig,
    constraints: Iterable[Constraint],
    protected: Optional[Sequence[str]] = None,
) -> bool:
    """True iff every protected household keeps >=2 possible answers.

    Also returns ``False`` for an empty admissible set (contradictory public
    information) so a caller never mistakes "nothing is possible" for privacy.
    """
    protected = protected if protected is not None else config.protected
    cons = _constraints_list(constraints)
    adm = admissible_assignments(config, cons)
    if not adm:
        return False
    for h in protected:
        vals = {a[h] for a in adm}
        if len(vals) < 2:
            return False
    return True


def group_count_values(
    config: WeekConfig,
    constraints: Iterable[Constraint],
    group: str,
) -> Set[int]:
    """Possible values of ``sum(answers over group)`` given public info."""
    covers = config.group_covers(group)
    adm = admissible_assignments(config, constraints)
    return {sum(a[h] for h in covers) for a in adm}


def candidate_ranges(
    config: WeekConfig,
    group: str,
    true_value: int,
) -> List[Tuple[int, int]]:
    """All truthfully-labelled ranges ``[lo, hi]`` that contain ``true_value``,
    ordered from tightest to loosest (ties: nearest the truth first)."""
    covers = config.group_covers(group)
    n = len(covers)
    if not (0 <= true_value <= n):
        raise PrivacyError("true group count outside plausible range")
    cands = [
        (lo, hi)
        for lo in range(0, true_value + 1)
        for hi in range(true_value, n + 1)
    ]
    # sort by width, then by distance of the interval from the truth
    cands.sort(key=lambda r: (r[1] - r[0], (true_value - r[0]) + (r[1] - true_value), r[0]))
    return cands


def tightest_safe_range(
    config: WeekConfig,
    constraints: Iterable[Constraint],
    group: str,
    true_value: int,
    protected: Optional[Sequence[str]] = None,
    *,
    min_width: int = 0,
    accept: Optional[Callable[[Constraint], bool]] = None,
) -> Constraint:
    """The tightest truthfully-labelled range on ``group`` that keeps the state
    safe, given the existing public constraints.

    ``min_width`` forbids degenerate ranges: a range with ``lo == hi`` is an
    exact count in disguise, so a caller that is deliberately issuing a
    *reference* (not an exact) passes ``min_width=1``.

    ``accept`` is an extra predicate a candidate must satisfy; the publisher
    uses it to require that a non-reserved range still leaves each *reserved*
    group able to be published exactly later.

    Because the full range ``[0, n]`` is non-informative, one always exists --
    provided the current state is safe.  A :class:`PrivacyError` is raised if
    the current state is already unsafe (nothing new may be published in that
    case; it must be recorded as a breach instead).
    """
    protected = protected if protected is not None else config.protected
    cons = _constraints_list(constraints)
    if not is_safe(config, cons, protected):
        raise PrivacyError(
            "current public state is already unsafe; cannot publish more safely"
        )
    covers = config.group_covers(group)
    for lo, hi in candidate_ranges(config, group, true_value):
        if hi - lo < min_width:
            continue
        cand = Constraint.range(covers, lo, hi, label=f"group:{group}")
        if not is_safe(config, cons + [cand], protected):
            continue
        if accept is not None and not accept(cand):
            continue
        return cand
    raise PrivacyError(
        "no safe range satisfies the requested constraints (unexpected)"
    )


def exact_is_safe(
    config: WeekConfig,
    constraints: Iterable[Constraint],
    group: str,
    true_value: int,
    protected: Optional[Sequence[str]] = None,
) -> bool:
    protected = protected if protected is not None else config.protected
    covers = config.group_covers(group)
    cand = Constraint.exact(covers, true_value, label=f"group:{group}")
    return is_safe(config, _constraints_list(constraints) + [cand], protected)


def range_is_blank(
    config: WeekConfig,
    group: str,
    constraint: Constraint,
) -> bool:
    """A range is *blank* when it excludes no count the group could otherwise
    take -- i.e. it is the full ``[0, n]`` interval."""
    n = len(config.group_covers(group))
    return constraint.kind == "range" and constraint.lo == 0 and constraint.hi == n


def derived_after_adding(
    config: WeekConfig,
    constraints: Iterable[Constraint],
    candidate: Constraint,
    protected: Optional[Sequence[str]] = None,
) -> Dict[str, int]:
    """Which protected households become uniquely derivable if ``candidate`` is
    published on top of ``constraints``."""
    protected = protected if protected is not None else config.protected
    return forced_answers(config, _constraints_list(constraints) + [candidate], protected)
