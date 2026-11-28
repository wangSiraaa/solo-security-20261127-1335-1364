"""Trusted forwarder (受信转发器) — Route A.

The trusted relay sits between the local pipe (inner traffic, markers included)
and the outsourced console (outer frames only).  It:

1. reassembles each inner message by **local stream identity** and byte offset,
2. only emits an outer frame once the message's public fields are *provable*
   (integrity-checked); anything else is kept inside and never forwarded,
3. makes the **stream-break / waiting boundary explicit**: incomplete streams
   are held while their bytes may still arrive; a stall (断流) for a stream
   evicts its buffer so a truncated message can never leak half a public event.

Route B (typed emission) is provided by :class:`TypedEmitter` /
:class:`LegacyAdapter`, which never need reassembly because modern components
write typed fields and the legacy adapter must prove each fragment public.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .assembly import Assembler, AssembledResult
from .console import Console
from .model import PublicEvent, TypedEvent
from .wire import encode_frame


@dataclass
class RelayStats:
    forwarded: int = 0
    kept_inside: int = 0
    dropped_on_stall: int = 0


class TrustedRelay:
    """Assembles inner streams and forwards only provable-public events."""

    def __init__(self, console: Console) -> None:
        self.console = console
        self.assembler = Assembler()
        self.stats = RelayStats()
        self._inside: List[AssembledResult] = []

    # -- ingestion ---------------------------------------------------------

    def push_pipe(self, pipe_bytes: bytes) -> List[PublicEvent]:
        """Feed raw local pipe bytes; return events newly forwarded outward."""

        forwarded: List[PublicEvent] = []
        for sid in self.assembler.feed_bytes(pipe_bytes):
            result = self._promote(sid)
            if result.public is not None:
                frame = encode_frame(result.public)
                self.console.deliver_frame(frame)
                self.stats.forwarded += 1
                forwarded.append(result.public)
            else:
                self._inside.append(result)
                self.stats.kept_inside += 1
        return forwarded

    def push_fragment_record(self, record: bytes) -> Optional[PublicEvent]:
        sid = self.assembler.feed_record(record)
        if sid is None:
            return None
        result = self._promote(sid)
        if result.public is not None:
            self.console.deliver_frame(encode_frame(result.public))
            self.stats.forwarded += 1
            return result.public
        self._inside.append(result)
        self.stats.kept_inside += 1
        return None

    def _promote(self, sid: str) -> AssembledResult:
        result = self.assembler.assemble(sid)
        return result

    # -- stream break / waiting boundary ----------------------------------

    def waiting_streams(self) -> List[str]:
        return [
            sid
            for sid in self.assembler.known_streams()
            if not self.assembler.is_complete(sid)
        ]

    def stall(self, stream_id: str) -> None:
        """Declare a stream broken (断流): evict it, never emitting a partial.

        This is the explicit decision point: a truncated stream is not "waited
        on forever" and is not forwarded. It simply stays inside and is gone.

        A *complete* stream is never dropped here — it was already promoted the
        moment its final fragment arrived — so a late stall signal is a no-op.
        """

        buf = self.assembler._streams.get(stream_id)
        if buf is None or buf.is_complete():
            return
        del self.assembler._streams[stream_id]
        self.stats.dropped_on_stall += 1

    def close(self) -> List[str]:
        """End of batch: return stream ids still incomplete (left inside)."""

        leftover = self.waiting_streams()
        for sid in leftover:
            self.stall(sid)
        return leftover

    # -- audit -------------------------------------------------------------

    def kept_inside(self) -> List[AssembledResult]:
        return list(self._inside)


class TypedEmitter:
    """Route B: modern components write typed fields.

    The trusted side projects *only* the public fields into a standalone outer
    diagnostic event.  The marker has no path to the wire.
    """

    def __init__(self, console: Console) -> None:
        self.console = console
        self.last_internal: List[TypedEvent] = []

    def emit(self, event: TypedEvent) -> PublicEvent:
        self.last_internal.append(event)  # local-only audit copy
        public = PublicEvent(
            receipt_id=event.receipt_id,
            step_name=event.step_name,
            outcome=event.outcome,
        )
        self.console.deliver_frame(encode_frame(public))
        return public


class LegacyAdapter:
    """Route B, limited-access bridge for old segment-writing components.

    A legacy component still writes its message in pieces, possibly with the
    marker straddling two writes.  This adapter accepts each piece **for one
    declared stream only**, reassembles by offset, and will only forward a
    message whose public fields are provable.  A fragment that cannot be proven
    public (e.g. it belongs to a stream that never completes) stays inside.

    This is deliberately *limited*: no cross-stream byte fusing, no promotion of
    unproven data, and an explicit stall to bound waiting.
    """

    def __init__(self, console: Console) -> None:
        self.console = console
        self._relay = TrustedRelay(console)

    def write(self, stream_id: str, total: int, offset: int, payload: bytes) -> Optional[PublicEvent]:
        from .wire import Fragment

        record = Fragment(stream_id, total, offset, payload).encode()
        return self._relay.push_fragment_record(record)

    def stall(self, stream_id: str) -> None:
        self._relay.stall(stream_id)

    def forwarded(self) -> int:
        return self._relay.stats.forwarded

    def kept_inside(self) -> int:
        return self._relay.stats.kept_inside
