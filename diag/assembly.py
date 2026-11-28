"""Local reassembly of inner traffic.

The inbound pipe is a raw byte stream on which:

* fragments of one message may be split anywhere (even mid-marker, mid-field);
* fragments from *concurrent* components may interleave;
* two unrelated fragments may land byte-adjacent, so a naive "join the bytes
  and look for the magic" scan would fuse them.

Reassembly therefore works on **self-delimiting fragment records** keyed by
*local stream identity* (``stream_id``) and positioned by explicit byte offset.
Byte adjacency is never used as evidence of belonging together.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .model import PublicEvent
from .wire import Fragment, parse_internal


class ReassemblyError(ValueError):
    """Raised when fragment records on the wire are structurally invalid."""


class IncompleteStream(LookupError):
    """Raised when a stream is asked for before all its bytes arrived."""

    def __init__(self, stream_id: str, covered: int, total: int) -> None:
        self.stream_id = stream_id
        self.covered = covered
        self.total = total
        super().__init__(
            f"stream {stream_id!r} incomplete: "
            f"{total - covered} byte(s) still missing"
        )


@dataclass
class _StreamBuffer:
    total: Optional[int] = None
    chunks: Dict[int, bytes] = field(default_factory=dict)

    def add(self, frag: Fragment) -> None:
        if self.total is None:
            self.total = frag.total
        elif self.total != frag.total:
            raise ReassemblyError(
                f"inconsistent total for stream {frag.stream_id!r}: "
                f"{self.total} vs {frag.total}"
            )
        existing = self.chunks.get(frag.offset)
        if existing is not None and existing != frag.payload:
            raise ReassemblyError(
                f"conflicting fragment at offset {frag.offset} "
                f"in stream {frag.stream_id!r}"
            )
        self.chunks[frag.offset] = frag.payload

    def _covered(self) -> List[int]:
        return sorted(self.chunks)

    def is_complete(self) -> bool:
        if self.total is None:
            return False
        pos = 0
        for off in self._covered():
            if off != pos:
                return False
            pos += len(self.chunks[off])
        return pos == self.total

    def assemble(self) -> bytes:
        if not self.is_complete():
            covered = sum(len(self.chunks[off]) for off in self._covered())
            raise IncompleteStream("?", covered, self.total or 0)
        return b"".join(self.chunks[off] for off in self._covered())


@dataclass
class AssembledResult:
    """Outcome of trying to turn a complete stream into a public event."""

    public: Optional[PublicEvent]
    stream_id: Optional[str] = None
    marker: Optional[str] = None
    kept_inside_reason: str = ""

    @property
    def is_public(self) -> bool:
        return self.public is not None


class Assembler:
    """Demultiplexes and reassembles inner fragment records by stream identity."""

    def __init__(self) -> None:
        self._streams: Dict[str, _StreamBuffer] = {}

    def feed_record(self, record: bytes) -> Optional[str]:
        """Feed one self-delimiting fragment record.

        Returns the stream id once that record *completes* the stream (the
        caller may then call :meth:`assemble`), else ``None``.
        """

        frag = Fragment.decode(record)
        buf = self._streams.setdefault(frag.stream_id, _StreamBuffer())
        buf.add(frag)
        return frag.stream_id if buf.is_complete() else None

    def feed_bytes(self, data: bytes) -> List[str]:
        """Feed a concatenation of fragment records.

        Consumers must already have split the raw pipe into records (see
        :func:`split_records`); this only decodes well-delimited records.
        Returns ids of streams that became complete.
        """

        completed: List[str] = []
        for record in split_records(data):
            sid = self.feed_record(record)
            if sid is not None:
                completed.append(sid)
        return completed

    def is_complete(self, stream_id: str) -> bool:
        buf = self._streams.get(stream_id)
        return bool(buf and buf.is_complete())

    def known_streams(self) -> List[str]:
        return list(self._streams)

    def assemble(self, stream_id: str) -> AssembledResult:
        """Turn a completed stream into a public event, or keep it inside.

        A stream is only promoted to a public event when its public fields pass
        their integrity check.  Anything that cannot be *proven* public stays on
        the inner side: it is reported here with ``public is None`` and a reason,
        and is never encoded into an outer frame.
        """

        buf = self._streams.get(stream_id)
        if buf is None:
            raise KeyError(stream_id)
        if not buf.is_complete():
            covered = sum(len(buf.chunks[off]) for off in buf._covered())
            raise IncompleteStream(stream_id, covered, buf.total or 0)
        raw = buf.assemble()
        try:
            public, sid, marker = parse_internal(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            return AssembledResult(
                public=None,
                stream_id=stream_id,
                marker=None,
                kept_inside_reason=f"cannot prove public: {exc}",
            )
        return AssembledResult(public=public, stream_id=sid, marker=marker)


def split_records(data: bytes) -> List[bytes]:
    """Split a raw local pipe into fragment records.

    Records are length-prefixed, so this is unambiguous even when records from
    different streams are interleaved or byte-adjacent.  Leftover trailing bytes
    (a record still in flight) are ignored.
    """

    from .wire import _take_lp  # local import to keep the struct helpers private

    out: List[bytes] = []
    pos = 0
    magic_len = 3
    while pos < len(data):
        start = pos
        if data[pos : pos + magic_len] != b"FR1":
            # Resync: drop one byte and retry (defensive for noisy pipes).
            pos += 1
            continue
        p = pos + magic_len
        try:
            _stream, p = _take_lp(data, p)
        except ValueError:
            break
        if p + 8 > len(data):
            break
        p += 8  # total + offset
        try:
            _payload, p = _take_lp(data, p)
        except ValueError:
            break
        out.append(data[start:p])
        pos = p
    return out
