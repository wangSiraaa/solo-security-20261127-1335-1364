"""Two interrogation strategies.

Both consume *verified* records only; neither sees the rig or the wiring.

``preplan``
    The full parallel schedule is fixed before entering the closed rig.  The
    schedule used here is the balanced 5-beat code

        P-A1 00001 | P-B2 00010 | P-C3 01100
        P-D4 10101 | P-E5 11010 | P-F6 11111

    (each probe's 5-bit column says, per beat, whether it is in the named
    call set).  Every column has weight 3, any two codewords differ in >= 2
    bits, and there are 5 beats for 6 probes -- fewer beats than probes, so
    calling each probe individually cannot fit the budget.

    Guarantee: if **each slot loses at most one read** over the whole run,
    every physical insertion is uniquely identified.  Proof: suppose two
    distinct insertions produce the same surviving observations.  Pick a
    probe P that they place in different slots. Re-index so P is in slot s
    under insertion 1 and in slot t under insertion 2.  Every surviving read
    of slot s (resp. t) then forces P's codeword to equal the codeword of the
    probe that insertion 2 (resp. 1) puts there, in all but at most one
    position (the slot's single allowed erasure) -- so the two codewords agree
    on all but at most one position, i.e. have distance <= 1, contradicting
    the code's minimum distance 2.  Hence no two insertions collide.
    ``verify_injectivity`` checks this by exhaustion over all 720 insertions
    and all "each slot loses <= 1 cell" patterns for a given roster.  Beyond
    one lost read per slot the schedule is *not* promised complete: the result
    keeps the affected slots ambiguous with a swap witness rather than
    guessing.

``rollcall``
    An on-site roll-caller.  After each completed beat (real bits + trusted
    receipts) it inspects only the still-ambiguous slots and asks *one* more
    named group, spending remaining beats where reads went missing.  It needs
    the roll-caller online and must keep the before/after question-receipt
    relation.  With 6 probes and 5 usable beats it can still finish when a
    single beat is fully dark, because it re-asks what is still unclear.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .reconstruct import Reconstruction, ValidBeat, reconstruct
from .rig import SamplingLog

# Balanced 5-bit schedule (see module docstring).  Columns all have weight 3,
# every pair of codewords differs in >= 2 positions (dmin = 2), and the five
# columns are distinct (no wasted beat).  Since a missing-read cell is the same
# physical cell for every candidate insertion, and swaps can only hide at most
# one of the >= 2 differing reads per slot, <= 1 lost read per slot is always
# uniquely recoverable (see the module docstring and ``verify_injectivity``).
PRESET_CODEWORDS: Dict[str, str] = {
    "P-A1": "00001",
    "P-B2": "00010",
    "P-C3": "01100",
    "P-D4": "10101",
    "P-E5": "11010",
    "P-F6": "11111",
}


def schedule_from_codewords(
    codewords: Dict[str, str]
) -> Tuple[List[Tuple[int, Tuple[str, ...]]], int]:
    """Turn per-probe codewords into (ordered beats, nbeats).

    Beat ``b`` calls the probes whose codeword has bit ``b`` set (bit 0 = the
    left-most character).  A short codeword is padded with '0'.
    """
    width = max((len(c) for c in codewords.values()), default=0)
    beats: List[Tuple[int, Tuple[str, ...]]] = []
    for b in range(width):
        call = tuple(sorted(p for p, c in codewords.items() if (c[b] if b < len(c) else "0") == "1"))
        if call:  # skip all-dark beats: they carry no information
            beats.append((b, call))
    return beats, width


def preset_beats() -> List[Tuple[int, Tuple[str, ...]]]:
    beats, _ = schedule_from_codewords(PRESET_CODEWORDS)
    return beats


# --------------------------------------------------------------------------
# Guarantee check: are per-slot erasures injective?
# --------------------------------------------------------------------------
def _erasure_signature(codewords: Dict[str, str], perm: Dict[str, str], erasures: Dict[str, Iterable[int]], slot_order: Sequence[str]) -> Tuple[int, ...]:
    """Observation vector for one insertion under per-slot skipped beats (-1)."""
    width = max((len(c) for c in codewords.values()), default=0)
    out: List[int] = []
    for s in slot_order:
        c = codewords[perm[s]]
        skipped = set(erasures.get(s, ()))
        for b in range(width):
            out.append(-1 if b in skipped else int(c[b]))
    return tuple(out)


def verify_injectivity(
    codewords: Dict[str, str],
    slots: Sequence[str],
    probe_ids: Sequence[str],
    per_slot_erasures: int = 1,
) -> Tuple[bool, Optional[Tuple[str, str, str]]]:
    """Prove that <= ``per_slot_erasures`` lost cells per slot is injective.

    Returns (ok, collision) where ``collision`` is a human-readable witness
    if two distinct insertions would look identical.  This is a proof by
    exhaustion over every insertion and every allowed erasure pattern; with
    720 insertions and 46656 patterns it takes ~90 s, so the default test only
    runs it when explicitly requested.  See the module docstring for the
    short proof that makes the guarantee.
    """
    from itertools import permutations, product

    slots = tuple(slots)
    probe_ids = tuple(probe_ids)
    width = max((len(c) for c in codewords.values()), default=0)
    erasure_sets = [
        e for k in range(per_slot_erasures + 1) for e in combinations(range(width), k)
    ]

    seen: Dict[Tuple[int, ...], Dict[str, str]] = {}
    for pm in permutations(probe_ids):
        perm = dict(zip(slots, pm))
        for esets in product(erasure_sets, repeat=len(slots)):
            erasures = {s: esets[i] for i, s in enumerate(slots)}
            sig = _erasure_signature(codewords, perm, erasures, slots)
            if sig in seen:
                return False, (str(seen[sig]), str(perm), str(erasures))
            seen[sig] = perm
    return True, None


# --------------------------------------------------------------------------
# Preplanned parallel interrogation
# --------------------------------------------------------------------------
@dataclass
class PreplanResult:
    schedule: List[Tuple[int, Tuple[str, ...]]]
    valid_beats: List[ValidBeat]
    reconstruction: Reconstruction


class PreplanStrategy:
    """Fix the whole parallel schedule *before* entering the rig."""

    def __init__(self, codewords: Optional[Dict[str, str]] = None):
        self.codewords = dict(codewords or PRESET_CODEWORDS)
        self.beats, self.width = schedule_from_codewords(self.codewords)

    def schedule(self) -> List[Tuple[int, Tuple[str, ...]]]:
        return list(self.beats)

    def reconstruct(self, valid_beats: Iterable[ValidBeat], slots, probe_ids, authenticated=None) -> Reconstruction:
        return reconstruct(valid_beats, probe_ids, slots, authenticated)


# --------------------------------------------------------------------------
# Adaptive on-site roll-caller
# --------------------------------------------------------------------------
def _greedy_split_call(
    recon: Reconstruction, slots: Sequence[str], probe_ids: Sequence[str]
) -> Optional[Tuple[str, ...]]:
    """Choose one named group that best separates the ambiguous slots.

    Only *not-yet-named* slots matter.  We minimise the worst remaining
    alternative count over those slots (counting both the lit and dark side,
    since bit 0 is also informative), then prefer a smaller call set.
    """
    targets = [s for s in slots if s in recon.ambiguous]
    if not targets:
        return None
    candidates = sorted({p for s in targets for p in recon.ambiguous[s]})
    best_key = None
    best_call: Optional[Tuple[str, ...]] = None
    for size in range(1, len(candidates) + 1):
        for C in combinations(candidates, size):
            Cset = set(C)
            worst = 0
            for s in targets:
                d = recon.ambiguous[s]
                lit = len([p for p in d if p in Cset])
                dark = len(d) - lit
                worst = max(worst, lit, dark)
            informative = sum(
                1
                for s in targets
                if any(p in Cset for p in recon.ambiguous[s])
                and any(p not in Cset for p in recon.ambiguous[s])
            )
            key = (worst, -informative, len(C), C)
            if best_key is None or key < best_key:
                best_key = key
                best_call = tuple(sorted(C))
    return best_call


@dataclass
class RollcallTurn:
    beat: int
    call: Tuple[str, ...]
    rec: object  # BeatRecord
    recon: Reconstruction


@dataclass
class RollcallResult:
    turns: List[RollcallTurn]
    final: Reconstruction
    exhausted: bool
    slots_left: Sequence[str]


class RollcallStrategy:
    """Adaptive: inspect the previous beat, then ask only what is unclear."""

    def __init__(self, probe_ids: Sequence[str], slots: Sequence[str]):
        self.probe_ids = tuple(probe_ids)
        self.slots = tuple(slots)

    def run(
        self,
        bench,
        session,
        registry,
        budget: int,
        missing_for_beat,  # callable(beat) -> set[slot]
        suppress_for_beat=None,  # callable(beat) -> set[probe_id]
    ) -> RollcallResult:
        turns: List[RollcallTurn] = []
        beats_used: List[ValidBeat] = []
        authenticated: set = set()
        for b in range(budget):
            if turns:
                call = _greedy_split_call(turns[-1].recon, self.slots, self.probe_ids)
                if call is None:
                    break
            else:
                # First beat: a broad triple to seed domains.
                call = self._initial_call()
            missing = set(missing_for_beat(b)) if missing_for_beat else set()
            suppress = set(suppress_for_beat(b)) if suppress_for_beat else set()
            rec = bench.record_beat(
                b, call, session, missing_slots=missing, suppress_receipts=suppress
            )
            # Verify *this* beat immediately (roll-caller keeps QA relation).
            vlog = SamplingLog([rec]).verify(session, registry, bench.sign_key())
            if len(vlog.beats) != 1:
                raise RuntimeError("roll-caller received an unverifiable beat")
            beats_used.append(vlog.beats[0])
            authenticated |= vlog.authenticated
            recon = reconstruct(beats_used, self.probe_ids, self.slots, authenticated)
            turns.append(RollcallTurn(b, call, rec, recon))
            if recon.complete:
                return RollcallResult(turns, recon, False, ())
        final = reconstruct(beats_used, self.probe_ids, self.slots, authenticated)
        left = self.slots if final.rejected_reason else tuple(
            s for s in self.slots if s in final.ambiguous
        )
        return RollcallResult(turns, final, True, left)

    def _initial_call(self) -> Tuple[str, ...]:
        # A triple seeded so the adaptive splitter has a non-trivial partition.
        return tuple(sorted(self.probe_ids[:3]))
