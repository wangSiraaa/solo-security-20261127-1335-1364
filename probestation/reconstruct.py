"""Constraint reconstruction from verified sampling records.

Input is strictly verified beats: per-slot contact bits (session-signed by the
bench, hence trustworthy for *physical membership* in the asked group) plus
receipt-derived authentication.  Nothing here touches the rig, its wiring,
slot order, bed numbers, or the raw readings.

Two layers, kept separate:

1. **Physical membership (bits).**  Against the *asked* call set, which is
   always correct for this session (the record is nonce-signed):
     - bit 1 on slot s  =>  occupant(s) is in the asked group;
     - bit 0 on slot s  =>  occupant(s) is not in the asked group;
     - missing (None)   =>  skip.
   These never produce a false inconsistency, so removing a cell never
   manufactures a contradiction -- it only costs information.

2. **Naming (authentication).**  To *name* a slot's occupant we additionally
   require that probe to have produced at least one valid **this-session**
   receipt.  The receipt proves "that probe answered this session"; it carries
   no slot name.  A probe whose receipts are all last shift's therefore keeps
   its physical membership but loses its naming grounds: the slot stays raw.

Identity is never inferred from slot position or reading similarity -- only
from membership bits + the one-probe-per-slot fact + authenticated presence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

Bit = Optional[int]


@dataclass(frozen=True)
class ValidBeat:
    """A beat that passed session + signature verification.

    ``call_set`` is what the bench asked (used for membership bits).
    ``trusted_call_set`` is the subset whose receipt verified for this session
    (kept for provenance / the report); it is *not* used to shrink the bit
    constraints, so a stale receipt can never create a contradiction.
    """

    beat: int
    challenge_id: str
    call_set: Tuple[str, ...]
    bits: Dict[str, Bit]
    trusted_call_set: Optional[Tuple[str, ...]] = None

    def trusted(self) -> Tuple[str, ...]:
        if self.trusted_call_set is None:
            return self.call_set
        return self.trusted_call_set


@dataclass
class Evidence:
    """Re-checkable argument for a named slot's occupant."""

    slot: str
    probe_id: str
    kind: str  # "direct" (single survivor) or "bijection"
    authenticated: bool = True
    supporting_beats: Tuple[dict, ...] = ()
    exclusions: Tuple[dict, ...] = ()

    def as_dict(self) -> dict:
        return {
            "slot": self.slot,
            "probe_id": self.probe_id,
            "kind": self.kind,
            "authenticated": self.authenticated,
            "supporting_beats": list(self.supporting_beats),
            "exclusions": list(self.exclusions),
        }


@dataclass
class Reconstruction:
    slots: Tuple[str, ...]
    probe_ids: Tuple[str, ...]
    solutions: List[Dict[str, str]]
    fixed: Dict[str, str]
    ambiguous: Dict[str, Tuple[str, ...]]
    raw_domains: Dict[str, Tuple[str, ...]]
    evidence: Dict[str, Evidence]
    reasons: Dict[str, str] = field(default_factory=dict)  # per unresolved slot
    rejected_reason: Optional[str] = None

    @property
    def complete(self) -> bool:
        return not self.ambiguous and not self.rejected_reason

    def evidence_dicts(self) -> Dict[str, dict]:
        return {s: e.as_dict() for s, e in self.evidence.items()}

    def grounds(self) -> Dict[str, Tuple[str, ...]]:
        """Which identities still have grounds per slot."""
        return {
            s: ((self.fixed[s],) if s in self.fixed else self.ambiguous.get(s, ()))
            for s in self.slots
        }


def _constrain(
    beats: Sequence[ValidBeat], slots: Sequence[str], probe_ids: Sequence[str]
) -> Tuple[Dict[str, Set[str]], Dict[Tuple[str, str], dict]]:
    """Apply membership bits against the asked call set, recording removals."""
    domains: Dict[str, Set[str]] = {s: set(probe_ids) for s in slots}
    removed: Dict[Tuple[str, str], dict] = {}
    for vb in beats:
        called = set(vb.call_set)
        for s in slots:
            bit = vb.bits.get(s)
            if bit is None:
                continue
            if bit == 1:
                for p in list(domains[s]):
                    if p not in called:
                        domains[s].discard(p)
                        removed[(s, p)] = {
                            "kind": "bit",
                            "why": "not_in_called_set",
                            "beat": vb.beat,
                            "challenge_id": vb.challenge_id,
                            "call_set": list(vb.call_set),
                            "bit": 1,
                        }
            else:
                for p in called:
                    if p in domains[s]:
                        domains[s].discard(p)
                        removed[(s, p)] = {
                            "kind": "bit",
                            "why": "in_called_set_but_dark",
                            "beat": vb.beat,
                            "challenge_id": vb.challenge_id,
                            "call_set": list(vb.call_set),
                            "bit": 0,
                        }
    return domains, removed


def _propagate_bijection(
    domains: Dict[str, Set[str]], removed: Dict[Tuple[str, str], dict]
) -> None:
    """All-different propagation: a pinned probe cannot sit in any other slot."""
    changed = True
    while changed:
        changed = False
        pinned = {s: next(iter(d)) for s, d in domains.items() if len(d) == 1}
        for t, p in pinned.items():
            for s in domains:
                if s == t or len(domains[s]) == 0:
                    continue
                if p in domains[s]:
                    domains[s].discard(p)
                    removed[(s, p)] = {
                        "kind": "bijection",
                        "why": "probe_pinned_elsewhere",
                        "other_slot": t,
                        "probe": p,
                    }
                    changed = True


def _enumerate_solutions(
    domains: Dict[str, Set[str]], slots: Sequence[str], cap: int = 200000
) -> List[Dict[str, str]]:
    """All perfect matchings consistent with the per-slot domains."""
    ordered = sorted(slots, key=lambda s: len(domains[s]))
    found: List[Dict[str, str]] = []

    def rec(i: int, used: Set[str], acc: Dict[str, str]) -> None:
        if len(found) >= cap:
            return
        if i == len(ordered):
            found.append(dict(acc))
            return
        s = ordered[i]
        for p in sorted(domains[s]):
            if p in used:
                continue
            acc[s] = p
            used.add(p)
            rec(i + 1, used, acc)
            used.discard(p)
            del acc[s]

    rec(0, set(), {})
    return found


def reconstruct(
    valid_beats: Iterable[ValidBeat],
    probe_ids: Sequence[str],
    slots: Sequence[str],
    authenticated: Optional[Iterable[str]] = None,
) -> Reconstruction:
    """Reconstruct which identity must occupy which slot, with checkable proof.

    ``authenticated``: probe ids with a valid *this-session* receipt.  When
    given, a slot is *named* only if its sole remaining candidate is an
    authenticated probe; otherwise the slot is kept raw with a reason.  The
    physical bijection still spans the whole roster either way.
    """
    slots = tuple(slots)
    probe_ids = tuple(probe_ids)
    beats = sorted(valid_beats, key=lambda b: b.beat)
    auth = None if authenticated is None else set(authenticated)

    domains, removed = _constrain(beats, slots, probe_ids)
    raw_domains = {s: tuple(sorted(domains[s])) for s in slots}

    if any(len(domains[s]) == 0 for s in slots):
        bad = [s for s in slots if not domains[s]]
        return Reconstruction(
            slots, probe_ids, [], {}, {}, raw_domains, {}, {},
            rejected_reason=f"inconsistent record: empty domain for {bad}",
        )

    _propagate_bijection(domains, removed)
    solutions = _enumerate_solutions(domains, slots)
    if not solutions:
        return Reconstruction(
            slots, probe_ids, [], {}, {}, raw_domains, {}, {},
            rejected_reason="no bijection consistent with the verified beats",
        )

    # A slot is named only if all solutions agree AND (if gating) the agreed
    # identity is authenticated this session.
    fixed: Dict[str, str] = {}
    ambiguous: Dict[str, Tuple[str, ...]] = {}
    reasons: Dict[str, str] = {}
    for s in slots:
        occupants = {sol[s] for sol in solutions}
        if len(occupants) == 1:
            p = next(iter(occupants))
            if auth is None or p in auth:
                fixed[s] = p
            else:
                ambiguous[s] = (p,)
                reasons[s] = "physical membership pinned, but identity has no valid current receipt"
        else:
            ambiguous[s] = tuple(sorted(occupants))

    # Per-slot supporting beats (bits actually read).
    support_by_slot: Dict[str, List[dict]] = {s: [] for s in slots}
    for vb in beats:
        for s in slots:
            if vb.bits.get(s) is not None:
                support_by_slot[s].append(
                    {
                        "beat": vb.beat,
                        "challenge_id": vb.challenge_id,
                        "bit": vb.bits[s],
                        "call_set": list(vb.call_set),
                        "trusted_call_set": list(vb.trusted()),
                    }
                )

    pinned_slots = {p: s for s, p in fixed.items()}
    evidence: Dict[str, Evidence] = {}
    for s, p in fixed.items():
        authenticated_flag = auth is None or p in auth
        if len(raw_domains[s]) == 1:
            evidence[s] = Evidence(s, p, "direct", authenticated_flag, tuple(support_by_slot[s]), ())
            continue
        exclusions: List[dict] = []
        for q in raw_domains[s]:
            if q == p:
                continue
            if q in pinned_slots and pinned_slots[q] != s:
                exclusions.append(
                    {"excluded_candidate": q, "because_pinned_at": pinned_slots[q]}
                )
            else:
                exclusions.append(
                    {"excluded_candidate": q, "because": "only_bijection_completion"}
                )
        evidence[s] = Evidence(s, p, "bijection", authenticated_flag, tuple(support_by_slot[s]), tuple(exclusions))

    return Reconstruction(
        slots, probe_ids, solutions, fixed, ambiguous, raw_domains, evidence, reasons
    )


def swap_witnesses(recon: Reconstruction, limit: int = 8) -> Dict[str, object]:
    """For ambiguous slots, list the distinct completions (restricted view)."""
    if not recon.ambiguous:
        return {}
    views: List[Dict[str, str]] = []
    seen: Set[Tuple[Tuple[str, str], ...]] = set()
    amb = tuple(sorted(recon.ambiguous))
    for sol in recon.solutions:
        key = tuple((s, sol[s]) for s in amb)
        if key in seen:
            continue
        seen.add(key)
        views.append({s: sol[s] for s in amb})
        if len(views) >= limit:
            break
    return {"ambiguous_slots": list(amb), "completions": views}
