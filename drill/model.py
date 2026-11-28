"""Data model for the teaching-machine-room power-loss drill.

Two feeds (支路) A/B jointly feed several chassis (机箱).  A drill run loses one
feed; the remaining feed plus the battery (电池能量) has to carry the surviving
chassis long enough to flush every write point that a receipt has promised.

Units are deliberately simple and deterministic:
  * capacity / load / startup_peak : watts (W) per tick
  * battery energy                 : watt-ticks (J when tick_seconds == 1)
  * flush cost                     : watt-ticks
We never use wall-clock time; one "tick" is one scheduling round.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

KIND_RANK = {"data": 0, "index": 1, "commit": 2}


@dataclass(frozen=True)
class Feed:
    name: str
    capacity: float  # carrying capacity per tick, watts


@dataclass(frozen=True)
class Cabinet:
    name: str
    feeds: Tuple[str, ...]
    base_load: float          # watts while powered
    startup_peak: float       # extra watts for one tick at cold start
    flush_cost: float = 6.0   # energy per flush op performed by this chassis

    def powered_by(self, present: frozenset) -> bool:
        return any(f in present for f in self.feeds)


@dataclass
class PageImage:
    """A single physical page that ends up on exactly one volume."""
    vol: str          # "vol1" / "vol2"
    page_no: int
    kind: str         # "data" | "index"
    content: str
    version: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "vol": self.vol,
            "page_no": self.page_no,
            "kind": self.kind,
            "content": self.content,
            "version": self.version,
        }


@dataclass
class Op:
    """A durable write point.  It only exists on disk once it has *flushed*."""
    op_id: str
    txid: str
    classroom: str
    assignment: str
    cabinet: str
    kind: str                 # data | index | commit
    vol: Optional[str]        # None for the commit record
    page_no: Optional[int]
    content: str
    cost: float
    deps: Tuple[str, ...] = ()          # op_ids that must flush first
    cross_volume: bool = False          # belongs to a cross-book reference order
    deferred_once: bool = False          # fault injection: slip one tick once
    defer_used: bool = False
    not_before_tick: int = 0
    # runtime
    flushed: bool = False
    flush_tick: Optional[int] = None
    seq: int = 0                         # submission order tiebreak

    @property
    def rank(self) -> int:
        return KIND_RANK[self.kind]


@dataclass
class Submission:
    classroom: str
    assignment: str
    txid: str
    cabinet: str
    submitted_tick: int
    cross_volume: bool
    pages: List[PageImage]
    ops: List[Op] = field(default_factory=list)
    promised: bool = False
    receipt: Optional[Dict[str, Any]] = None
    pending_receipt: Optional[Dict[str, Any]] = None
    aborted_reason: Optional[str] = None

    @property
    def commit_op(self) -> Optional[Op]:
        for op in self.ops:
            if op.kind == "commit":
                return op
        return None


@dataclass(frozen=True)
class Topology:
    """A concrete feed/chassis wiring, used for startup feasibility checks."""
    feeds: Tuple[Feed, ...]
    cabinets: Tuple[Cabinet, ...]

    def feed(self, name: str) -> Feed:
        for f in self.feeds:
            if f.name == name:
                return f
        raise KeyError(name)

    def cabinet(self, name: str) -> Cabinet:
        for c in self.cabinets:
            if c.name == name:
                return c
        raise KeyError(name)


@dataclass
class DrillConfig:
    topology: Topology
    battery_energy: float
    present_feeds_after_loss: Tuple[str, ...]
    feed_loss_tick: int
    kill_tick: int
    min_service: Optional[Dict[str, Any]]       # {"cabinet":..., "classroom":..., "ticks":...}
    classroom_keys: Dict[str, bytes]
    submissions: List[Submission]
    chassis_flush_order: Tuple[str, ...]
    shed_order: Tuple[str, ...]
    receipt_policy: str = "commit"              # "commit" (safe) | "cache" (unsafe demo)
    delay_index: Optional[Dict[str, str]] = None  # {"classroom":..., "assignment":...}
    startup_schedule: Dict[str, int] = field(default_factory=dict)
    max_ticks: int = 64
    flushes_per_tick: int = 2      # 每拍承载: concurrent disk-write lanes
    flush_ticks: int = 1           # 磁盘落盘时间: ticks an op occupies a lane

    @property
    def all_feeds(self) -> Tuple[str, ...]:
        return tuple(f.name for f in self.topology.feeds)


@dataclass
class Event:
    tick: int
    kind: str
    detail: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {"tick": self.tick, "kind": self.kind, **self.detail}
