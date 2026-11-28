"""Reset / clear / window event model.

A *run* is a list of events applied at integer times ("ticks").  Real resets
also have a bounded latency: a domain reset is not complete until it has had
time to settle, and cells cleared by that reset are unusable until then.  This
module records the raw events; the simulator (``sim.py``) resolves them into a
tick-by-tick register trace.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .model import SpecError

VALID_EVENT_KINDS = ("reset", "local_power_loss", "clear", "write", "window")


@dataclass
class Event:
    """A single scheduled event.

    ``kind``
        reset / local_power_loss / clear / write / window
    ``domains``
        reset events: domains being reset (in the given order).
    ``domain``
        local_power_loss: the domain losing power.
    ``cell``
        clear / write: target cell.
    ``value`` / ``gen``
        new content / round token for a write.
    ``window``
        name of the read window opened by a window event.
    ``latency``
        for resets, the number of ticks the reset takes to settle.  Cells
        cleared by the reset stay invalid until ``at + latency``.
    ``note``
        free-form maintainer annotation.
    """

    at: int
    kind: str
    domains: list[str] = field(default_factory=list)
    domain: Optional[str] = None
    cell: Optional[str] = None
    value: Any = None
    gen: Optional[str] = None
    window: Optional[str] = None
    latency: int = 0
    note: str = ""

    def __post_init__(self) -> None:
        if self.kind not in VALID_EVENT_KINDS:
            raise SpecError(f"bad event kind {self.kind!r} at tick {self.at}")
        if self.at < 0:
            raise SpecError(f"event at negative tick {self.at}")
        if self.latency < 0:
            raise SpecError(f"negative latency at tick {self.at}")
        if self.kind in ("reset", "local_power_loss"):
            if not self.domains and not self.domain:
                raise SpecError(f"{self.kind} event at {self.at} names no domain")
        if self.kind in ("clear", "write") and not self.cell:
            raise SpecError(f"{self.kind} event at {self.at} names no cell")
        if self.kind == "window" and not self.window:
            raise SpecError(f"window event at {self.at} names no window")

    @property
    def reset_domains(self) -> list[str]:
        if self.kind == "local_power_loss":
            return [self.domain] if self.domain else list(self.domains)
        return list(self.domains)

    @classmethod
    def from_dict(cls, doc: dict) -> "Event":
        return cls(
            at=int(doc["at"]),
            kind=doc["kind"],
            domains=list(doc.get("domains") or ([doc["domain"]] if doc.get("domain") else [])),
            domain=doc.get("domain"),
            cell=doc.get("cell"),
            value=doc.get("value"),
            gen=doc.get("gen"),
            window=doc.get("window"),
            latency=int(doc.get("latency", 0)),
            note=doc.get("note", ""),
        )


@dataclass
class ReadRequest:
    """A window read scheduled by a ``window`` event.

    ``at`` is the tick the window fires (or, for handshake mode, the earliest
    tick it may open).  ``window`` is the port name.  ``want`` is the set of
    public outputs (cell names, possibly via wiring) the port publishes.
    ``generation`` optionally pins the required calibration round.

    ``mode`` is ``"fixed"`` (commit to ``at``) or ``"handshake"`` (publish at
    the first coherent tick in ``[at, deadline]``).  ``deadline`` is required
    for handshake mode and otherwise ignored.
    """

    at: int
    window: str
    want: list[str] = field(default_factory=list)
    generation: Optional[str] = None
    note: str = ""
    mode: str = "fixed"
    deadline: Optional[int] = None

    def __post_init__(self) -> None:
        if self.mode not in ("fixed", "handshake"):
            raise SpecError(f"bad window mode {self.mode!r}")
        if self.mode == "handshake":
            if self.deadline is None:
                raise SpecError(
                    f"handshake window {self.window!r} needs a deadline"
                )
            if self.deadline < self.at:
                raise SpecError(
                    f"handshake window {self.window!r} deadline precedes open"
                )

    @classmethod
    def from_dict(cls, doc: dict) -> "ReadRequest":
        return cls(
            at=int(doc["at"]),
            window=doc["window"],
            want=list(doc.get("want") or []),
            generation=doc.get("generation"),
            note=doc.get("note", ""),
            mode=doc.get("mode", "fixed"),
            deadline=(int(doc["deadline"]) if doc.get("deadline") is not None else None),
        )


def events_from_doc(doc: list[dict]) -> list[Event]:
    out = [Event.from_dict(e) for e in doc]
    return out


def windows_from_events(events: list[Event]) -> list[ReadRequest]:
    out = []
    for e in events:
        if e.kind == "window":
            out.append(ReadRequest(at=e.at, window=e.window or "", note=e.note))
    return out
