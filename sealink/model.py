"""Domain model for the sealink hand-off proxy / island merge desk.

Vocabulary (kept deliberately small):

  voyage      one sortie of one carrier vessel. Has a byte cap and a set of
              rendezvous windows (chance meetings with stations). A voyage may
              legitimately miss a window ("allowed voyage miss").

  Rendezvous  a (vessel, station, cycle) slot. A station meeting can either
              exchange small files (interact) or be skipped without failing.
              A port control order can forbid a slot before or after it ran.

  Segment     the raw observation sliced into independent bytes ("只交换手头的
              小文件"). Each segment has an explicit position so reassembly can
              verify "序列连续" (sequence continuity).

  Observation the logical whole. "现场只认最终可独立打开的整条观测": the
              desk only recognises an observation when it can be opened
              independently start-to-finish with a verified inner digest.

  Manifest    the authoritative, content-addressed description of the whole
              observation: ordered segment list, per-segment inner digest,
              final root digest. The manifest is what makes an observation
              "independently openable" and what anchors "实际装运".

  Bag         what a carrier actually holds. Two kinds:
                PUBLIC  -- plain, unaffected by witness seals (carrier sees it)
                SEALED  -- only the designated island station can open it.
                           Ordinary carriers only accept sealed bags.
              A bag carries a set of segment ids; its payload root is a
              deterministic function of exactly those ids, so a copy of a bag
              yields an identical root (replay cannot double the promise).

  CustodyChain a single chain of custody for a piece of material, with one
              designated primary custodian and optional independent secondary
              routes. "实交后带回签收再释放本地副本" = release is triggered by a
              signed-returned receipt, not by a physical handoff alone.

  RecoveryExpr the honest, per-carrier recovery capability. It is NOT a
              head-count: receiving the same number of bytes as another island
              can still leave a different gap (see contiguous/overlap tests).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from typing import Optional


PUBLIC = "public"
SEALED = "sealed"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def payload_root(segment_ids) -> str:
    """Content-addressed root of an exact set of segment ids.

    Deterministic and order-independent. Copying a bag therefore reproduces the
    same root: two physical copies of "the same bag" cannot be counted as two
    distinct recovery promises.
    """
    ids = sorted(segment_ids)
    h = hashlib.sha256()
    h.update(b"bag|")
    for sid in ids:
        h.update(sid.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


# --------------------------------------------------------------------------- #
# Space / time
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SpaceTime:
    """A position in the observation (segment index, time index)."""

    seg: int
    t: int

    def as_tuple(self):
        return (self.seg, self.t)


@dataclass(frozen=True)
class Calendar:
    """A station's own track (轨段) calendar: which positions it can cover."""

    station: str
    steps: tuple[SpaceTime, ...]

    def covers(self, rng: "TrackRange") -> bool:
        have = {s.as_tuple() for s in self.steps}
        return all(s.as_tuple() in have for s in rng.iter_steps())


@dataclass(frozen=True)
class TrackRange:
    """Contiguous span over (segment, time): the requested coverage window."""

    seg_lo: int
    seg_hi: int
    t_lo: int
    t_hi: int

    def iter_steps(self):
        for seg in range(self.seg_lo, self.seg_hi + 1):
            for t in range(self.t_lo, self.t_hi + 1):
                yield SpaceTime(seg, t)


# --------------------------------------------------------------------------- #
# Observation / manifest / segments
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Segment:
    sid: str
    seg: int
    t: int
    data: bytes

    @property
    def inner_digest(self) -> str:
        return sha256_hex(self.data)

    @property
    def length(self) -> int:
        return len(self.data)


@dataclass(frozen=True)
class Manifest:
    """Authoritative description of the whole observation."""

    obs_id: str
    segments: tuple[Segment, ...]

    @property
    def root(self) -> str:
        """Content-addressed root over the full ordered segment list."""
        h = hashlib.sha256()
        h.update(b"manifest|")
        h.update(self.obs_id.encode("utf-8"))
        h.update(b"\x00")
        for seg in self.segments:
            h.update(seg.sid.encode("utf-8"))
            h.update(seg.inner_digest.encode("utf-8"))
            h.update(b"\x00")
        return h.hexdigest()

    def by_id(self) -> dict[str, Segment]:
        return {s.sid: s for s in self.segments}

    def ids(self) -> set[str]:
        return {s.sid for s in self.segments}

    def as_dict(self) -> dict:
        return {
            "obs_id": self.obs_id,
            "root": self.root,
            "segments": [
                {
                    "sid": s.sid,
                    "seg": s.seg,
                    "t": s.t,
                    "len": s.length,
                    "inner_digest": s.inner_digest,
                }
                for s in self.segments
            ],
        }


def build_observation(obs_id: str, seg_count: int, t_count: int, byte_fn=None) -> Manifest:
    """Build a synthetic observation split into small independent segments."""
    segs = []
    for seg in range(seg_count):
        for t in range(t_count):
            sid = f"{obs_id}:s{seg}:t{t}"
            payload = (
                f"{obs_id}:seg={seg}:t={t}".encode("utf-8")
                + b"|"
                + (byte_fn(seg, t) if byte_fn else (f"v={seg * t + seg}".encode("utf-8")))
            )
            segs.append(Segment(sid=sid, seg=seg, t=t, data=payload))
    return Manifest(obs_id=obs_id, segments=tuple(segs))


# --------------------------------------------------------------------------- #
# Bags
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Bag:
    """A physical carrier payload: a set of segment ids under a class.

    sealed_to: station that can unseal a SEALED bag. None => public.
    """

    bag_id: str
    obs_id: str
    klass: str
    segment_ids: frozenset[str]
    sealed_to: Optional[str] = None

    def __post_init__(self):
        if self.klass == SEALED and not self.sealed_to:
            raise ValueError("sealed bag requires a designated station")

    @property
    def root(self) -> str:
        return payload_root(self.segment_ids)

    @property
    def bytes(self) -> int:
        # bytes are enumerated by the sink; this is a count of ids only (the
        # desk measures real bytes when it writes them).
        return len(self.segment_ids)

    def can_open_by(self, station: str, carrier_is_trusted: bool) -> bool:
        """A bag is openable by a station iff.

        - public: any station (carrier may also see it, untouched by seals), or
        - sealed: only the designed island station (`sealed_to`).
        """
        if self.klass == PUBLIC:
            return True
        return station == self.sealed_to

    def fingerprint(self) -> str:
        """Identity of the physical bag *as a recovery promise*.

        Deliberately content-addressed (class + designated seal + segment root)
        and independent of `bag_id`, so a photocopy of the same bag -- or a
        renamed copy -- reproduces this fingerprint and therefore cannot be
        counted as a second committable promise.
        """
        return sha256_hex(
            f"{self.klass}|{self.sealed_to or ''}|{self.obs_id}|{self.root}".encode("utf-8")
        )


@dataclass(frozen=True)
class Receipt:
    """Signed (in this model: tamper-evident) acknowledgment of a real exchange.

    `ship_ledger` is what the carrier keeps: "船上只看实际交换回单并能重开目录
    续运". A missing or skipped slot yields no receipt.
    """

    receipt_id: str
    voyage: str
    carrier: str
    station: str
    cycle: int
    direction: str  # "out" (station->carrier) or "back" (carrier->station)
    bag_fingerprint: str
    segment_root: str
    n_segments: int
    mac: str

    def as_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Custody
# --------------------------------------------------------------------------- #


@dataclass
class CustodyChain:
    """Custody that follows *actual shipment*.

    The operator designates one chain for a piece of material. Two supported
    strategies; we implement the custody-chain strategy and reject the
    distributed-coding one (the task asks to pick one):

      mode = "single": exactly one carrier answerable per id at a time; shipping
              to another carrier transfers responsibility ("实交后带回签收再释放
              本地副本"). After a real hand-off the loader brings a signed
              returned receipt, and only then is its local copy released.
      mode = "dual":   important material additionally gets a *second,
              independent* transport route. Independence is judged by bag
              fingerprint: a mere photocopy of the same bag shares the
              fingerprint and is therefore NOT a second recoverability promise
              ("复印同一袋不得产生两份可承诺的恢复能力"). A genuinely independent
              route must carry a distinct representation.

    Responsibility is always a function of what was actually shipped, not of
    what was promised or of head-counts.
    """

    chain_id: str
    obs_id: str
    mode: str
    primary: Optional[str] = None
    secondary: list[str] = field(default_factory=list)
    holders: dict[str, set[str]] = field(default_factory=dict)  # carrier -> ids
    released_local: dict[str, set[str]] = field(default_factory=dict)
    independent_fingerprints: dict[str, set[str]] = field(default_factory=dict)
    duplicate_routes: set[str] = field(default_factory=set)

    def committable_routes(self) -> dict[str, set[str]]:
        """Independent routes only; duplicate-copy routes are excluded."""
        return {
            c: set(ids) for c, ids in self.holders.items()
            if ids and c not in self.duplicate_routes
        }

    def __post_init__(self):
        if self.mode not in ("single", "dual"):
            raise ValueError("mode must be 'single' or 'dual'")

    def ship(self, carrier: str, ids: set[str], *, bag: Optional[Bag] = None,
             independent: bool = False) -> None:
        """Actual shipment: responsibility for `ids` moves onto `carrier`."""
        if self.mode == "single":
            # Single chain: the same ids may not be answerable on two carriers
            # at once. A carrier that has brought back a signed receipt has
            # released its local copy, so responsibility may then transfer.
            for c, held in list(self.holders.items()):
                if c == carrier:
                    continue
                overlap = held & ids
                if not overlap:
                    continue
                if not self.is_released(c, overlap):
                    raise ValueError(
                        f"single custody chain: {sorted(overlap)} still "
                        f"answerable on {c}; it must return a receipt and "
                        f"release its local copy before re-shipment"
                    )
                held -= overlap
            self.primary = carrier
        else:
            if self.primary is None:
                self.primary = carrier
                if bag is not None:
                    self.independent_fingerprints.setdefault(carrier, set()).add(
                        bag.fingerprint()
                    )
            elif carrier != self.primary:
                if bag is None:
                    raise ValueError("second route requires its bag")
                fps = self.independent_fingerprints.setdefault(self.primary, set())
                if bag.fingerprint() in fps:
                    # A photocopy of the primary bag is physically possible and
                    # may ride a second route for transport redundancy, but it is
                    # recorded as a duplicate so it can never be counted as a
                    # second committable recovery promise.
                    self.duplicate_routes.add(carrier)
                elif independent:
                    fps.add(bag.fingerprint())
                    self.independent_fingerprints.setdefault(carrier, set()).add(
                        bag.fingerprint())
                else:
                    raise ValueError(
                        "a second route with a distinct bag must be declared "
                        "independent, or it is just an undeclared copy"
                    )
                if carrier not in self.secondary:
                    self.secondary.append(carrier)
        self.holders.setdefault(carrier, set()).update(ids)
        self.released_local.setdefault(carrier, set()).difference_update(ids)

    def return_receipt(self, carrier: str, receipt: "Receipt", ids: set[str]) -> None:
        """A signed returned receipt allows releasing the local copy of `ids`."""
        self.released_local.setdefault(carrier, set()).update(ids)

    def is_released(self, carrier: str, ids: set[str]) -> bool:
        return ids <= self.released_local.get(carrier, set())

    def responsibility(self) -> dict[str, set[str]]:
        return {c: set(ids) for c, ids in self.holders.items() if ids}


# --------------------------------------------------------------------------- #
# Port control
# --------------------------------------------------------------------------- #


@dataclass
class PortOrder:
    """Port controls carrier byte capacity and rendezvous windows."""

    voyage_cap_bytes: dict[str, int] = field(default_factory=dict)
    dropped_contacts: set[tuple[str, str, int]] = field(default_factory=set)  # (voyage, station, cycle)

    def cap_for(self, voyage: str) -> int:
        return self.voyage_cap_bytes.get(voyage, 0)

    def drop_contact(self, voyage: str, station: str, cycle: int) -> None:
        """Remove one agreed contact ("港口随后撤去一笔约定接触")."""
        self.dropped_contacts.add((voyage, station, cycle))

    def contact_allowed(self, voyage: str, station: str, cycle: int) -> bool:
        return (voyage, station, cycle) not in self.dropped_contacts
