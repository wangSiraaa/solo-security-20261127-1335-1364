"""Simulated rig, contact wiring, and independent sampling bench.

Physical model
--------------
* Six slots (甲..己); exactly one probe per slot (a bijection).  The bench
  records, for every slot and every beat, a *response bit*: did the probe in
  that slot light its electronic contact this beat?  A bad read channel is an
  explicit ``None`` ("missing read") and is signed as such.
* A beat asks a *named group* of probes to light their contacts.  A probe
  actuates iff it is in the called group.  The bench therefore only learns
  ``occupant(slot) in call_set`` -- never which identity lit.
* ``ContactBus.wire_matrix`` is the rig's internal wiring.  It exists only so
  that ``stimulate`` can make a contact actually light.  The certification /
  reconstruction code receives only verified record data and must never
  consult it.  (A test corrupts the matrix after records are made and checks
  results are unchanged.)
* The bench signs *its observations* (beat, nonce, challenge, call set, bit
  matrix).  Probe receipts are forwarded separately and carry their own
  signature under the probe's factory secret.  A receipt from the previous
  shift has a foreign nonce and is rejected; the beat's bits stay usable but
  that probe is no longer *trusted* to have answered this session's call.

There is deliberately *no* mapping table identity->slot anywhere on the
bench side.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .crypto import mac, verify_mac
from .entities import ProbeRegistry
from .reconstruct import ValidBeat

SLOT_LABELS = ("甲", "乙", "丙", "丁", "戊", "己")
_BENCH_NAME = "independent-sampling-bench"

Bit = Optional[int]  # 0 / 1 / None(=missing read)


# --------------------------------------------------------------------------
# Simulated internal wiring (hardware only; certification must not read it)
# --------------------------------------------------------------------------
class ContactBus:
    """Maps (slot, probe) contacts to physical response lines.

    Only ``Rig.stimulate`` uses this.  It is a separate object so a test can
    show reconstruction never touches it.
    """

    def __init__(self, slots: Sequence[str], probe_ids: Sequence[str]):
        self._lines = {slot: f"line-{i}" for i, slot in enumerate(slots)}
        self._contact_slot: Dict[str, str] = {}
        self._probe_ids = tuple(probe_ids)
        self.stimulate_count = 0  # instrumentation: certify must leave it unchanged

    def install(self, perm: Dict[str, str]) -> None:
        self._contact_slot = {probe_id: slot for slot, probe_id in perm.items()}

    def stimulate(self, slot: str, probe_id: str) -> bool:
        """Light the slot's line iff the contacted probe is inserted there."""
        self.stimulate_count += 1
        return self._contact_slot.get(probe_id) == slot

    def corrupt(self) -> None:
        """Test helper: break the wiring without changing any record."""
        self._contact_slot = {pid: "<nowhere>" for pid in self._probe_ids}


# --------------------------------------------------------------------------
# Sessions and records
# --------------------------------------------------------------------------
@dataclass
class Session:
    """A calibration-room session with a fresh nonce.

    The previous shift used a different nonce, so its records / receipts
    cannot be replayed.  Challenge ids are per beat and derived lazily from
    this session, so they are fresh as well.
    """

    nonce: str
    challenge_ids: Dict[int, str] = field(default_factory=dict)

    @classmethod
    def new(cls) -> "Session":
        """Start a fresh session (e.g. today's shift)."""
        return cls(nonce=secrets.token_hex(16))

    def challenge(self, beat: int) -> str:
        if beat not in self.challenge_ids:
            self.challenge_ids[beat] = f"beat-{beat}-{secrets.token_hex(4)}"
        return self.challenge_ids[beat]

    def new_shift(self) -> "Session":
        """A *different* session (e.g. last shift) with a fresh nonce."""
        return Session.new()


@dataclass
class BeatRecord:
    """One beat as recorded by the bench, with forwarded probe receipts."""

    beat: int
    nonce: str
    challenge_id: str
    call_set: Tuple[str, ...]
    bits: Dict[str, Bit]  # slot -> 0/1/None(missing)
    receipts: Dict[str, dict]  # probe_id -> signed answer (may be omitted)
    bench_tag: str

    def signed_body(self) -> dict:
        """Exactly what the bench signs (its own observations only)."""
        return {
            "beat": self.beat,
            "nonce": self.nonce,
            "challenge_id": self.challenge_id,
            "call_set": list(self.call_set),
            "bits": {s: b for s, b in self.bits.items()},
            "bench": _BENCH_NAME,
        }

    def as_dict(self) -> dict:
        d = self.signed_body()
        d["receipts"] = self.receipts
        d["bench_tag"] = self.bench_tag
        return d


class Bench:
    """The independent sampling bench on the far side of the rig.

    It holds the bench signing key (the session credential) and the rig.  It
    produces signed :class:`BeatRecord` objects.  It knows the physical
    insertion internally but signs only per-slot bits.
    """

    def __init__(self, rig: "Rig", bench_key: Optional[bytes] = None):
        self.rig = rig
        self._key = bench_key if bench_key is not None else secrets.token_bytes(32)

    def record_beat(
        self,
        beat: int,
        call_set: Sequence[str],
        session: Session,
        missing_slots: Iterable[str] = (),
        suppress_receipts: Iterable[str] = (),
        receipts_override: Optional[Dict[str, dict]] = None,
    ) -> BeatRecord:
        missing = set(missing_slots)
        call = tuple(call_set)
        challenge = session.challenge(beat)
        bits: Dict[str, Bit] = {}
        for slot in self.rig.slots:
            occupant = self.rig.occupant(slot)
            lit = self.rig.stimulate(slot, occupant, call)
            bits[slot] = None if slot in missing else (1 if lit else 0)
        receipts: Dict[str, dict] = {}
        if receipts_override is None:
            for pid in call:
                if pid in set(suppress_receipts):
                    continue
                ans = self.rig.probe(pid).answer(session.nonce, challenge)
                if ans is not None:
                    receipts[pid] = ans
        else:
            receipts = dict(receipts_override)
        tag = mac(self._key, {
            "beat": beat,
            "nonce": session.nonce,
            "challenge_id": challenge,
            "call_set": list(call),
            "bits": bits,
            "bench": _BENCH_NAME,
        })
        return BeatRecord(beat, session.nonce, challenge, call, bits, receipts, tag)

    def sign_key(self) -> bytes:
        return self._key


# --------------------------------------------------------------------------
# Verification of a hand-off log
# --------------------------------------------------------------------------
@dataclass
class RejectedBeat:
    beat: int
    reason: str


@dataclass
class VerifiedLog:
    beats: List[ValidBeat]
    rejected: List[RejectedBeat]
    # probe ids with at least one valid this-session receipt (their presence
    # is authenticated, so we may *name* them).  A probe whose only receipts
    # are last shift's is absent here even if its bits still narrow a slot.
    authenticated: set = field(default_factory=set)
    untrusted_receipts: Dict[int, Tuple[str, ...]] = field(default_factory=dict)
    receipt_issues: Dict[Tuple[int, str], str] = field(default_factory=dict)


class SamplingLog:
    """A stack of beat records handed to the calibration room."""

    def __init__(self, records: Optional[List[BeatRecord]] = None):
        self.records: List[BeatRecord] = list(records or [])

    def add(self, rec: BeatRecord) -> None:
        self.records.append(rec)

    def as_dicts(self) -> List[dict]:
        return [r.as_dict() for r in self.records]

    def verify(
        self, session: Session, registry: ProbeRegistry, bench_key: bytes
    ) -> VerifiedLog:
        """Verify a hand-off log.

        * The bench observation tag must verify and its nonce / challenge must
          belong to *this* session; otherwise the whole beat is rejected
          (stale record or tampering).
        * Bench bits are tied to the asked ``call_set`` and are trusted for
          per-slot membership (the record is session-signed).
        * Each receipt independently authenticates a probe's presence for
          this session.  A stale / forged receipt does not invalidate the
          bits; it just means that probe may not be *named* (see
          ``authenticated``).
        """
        beats: List[ValidBeat] = []
        rejected: List[RejectedBeat] = []
        authenticated: set = set()
        untrusted: Dict[int, Tuple[str, ...]] = {}
        issues: Dict[Tuple[int, str], str] = {}
        for rec in self.records:
            if rec.nonce != session.nonce:
                rejected.append(RejectedBeat(rec.beat, "stale nonce (not this session)"))
                continue
            if rec.challenge_id != session.challenge(rec.beat):
                rejected.append(RejectedBeat(rec.beat, "stale challenge id"))
                continue
            if not verify_mac(bench_key, rec.signed_body(), rec.bench_tag):
                rejected.append(RejectedBeat(rec.beat, "bench tag does not verify"))
                continue
            trusted: List[str] = []
            bad: List[str] = []
            for pid in rec.call_set:
                rc = rec.receipts.get(pid)
                if rc is None:
                    bad.append(pid)
                    issues[(rec.beat, pid)] = "no receipt"
                elif not registry.verify_answer(rc, session.nonce, rec.challenge_id):
                    bad.append(pid)
                    issues[(rec.beat, pid)] = "receipt fails this session/nonce"
                else:
                    trusted.append(pid)
                    authenticated.add(pid)
            untrusted[rec.beat] = tuple(bad)
            beats.append(
                ValidBeat(
                    beat=rec.beat,
                    challenge_id=rec.challenge_id,
                    call_set=tuple(rec.call_set),
                    trusted_call_set=tuple(trusted),
                    bits=dict(rec.bits),
                )
            )
        return VerifiedLog(
            sorted(beats, key=lambda b: b.beat), rejected, authenticated, untrusted, issues
        )


# --------------------------------------------------------------------------
# The rig
# --------------------------------------------------------------------------
class Rig:
    """Holds the physical insertion and the internal wiring.

    ``insert`` is the only place the identity->slot relationship exists.  The
    certification room is never given a ``Rig`` -- only a verified log.
    """

    def __init__(self, registry: ProbeRegistry, slots: Sequence[str] = SLOT_LABELS):
        if len(slots) != len(registry.probe_ids):
            raise ValueError("one slot per probe required")
        self.registry = registry
        self.slots = tuple(slots)
        self.bus = ContactBus(self.slots, registry.probe_ids)
        self._perm: Dict[str, str] = {}

    def insert(self, perm: Dict[str, str]) -> None:
        """Insert probes into slots.  ``perm`` maps slot -> probe_id."""
        if set(perm) != set(self.slots):
            raise ValueError("insertion must fill every slot")
        if sorted(perm.values()) != sorted(self.registry.probe_ids):
            raise ValueError("insertion must use every probe exactly once")
        self._perm = dict(perm)
        self.bus.install(perm)

    def occupant(self, slot: str) -> str:
        return self._perm[slot]

    def probe(self, probe_id: str):
        return self.registry.probe(probe_id)

    def stimulate(self, slot: str, occupant: str, call_set: Sequence[str]) -> bool:
        # A probe actuates iff it is in the called group AND its contact is
        # actually wired to this slot.  Uses the internal wiring.
        if occupant not in call_set:
            return False
        return self.bus.stimulate(slot, occupant)

    def make_bench(self) -> Bench:
        return Bench(self)
