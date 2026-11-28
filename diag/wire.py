"""Wire formats.

Two encodings live here, separated by a hard trust boundary:

``encode_internal`` / ``split_internal``
    The *inner* representation.  A business component's short diagnostic
    message, including the confidential drill marker (演练标记).  This is what
    gets written to the local pipe, possibly in several fragments (the exercise
    writes one message in up to four pieces).  Fragments from concurrent
    components may interleave, and two fragments may end up byte-adjacent.

``encode_frame`` / ``decode_frame``
    The *outer* representation.  A canonical, length-prefixed diagnostic event
    carrying only receipt id, public step name and public outcome.  This is the
    only thing the outsourced console ever receives, so it must be completely
    independent of the marker and of how the inner message was chunked.

Fragment records are self-delimiting and carry an explicit stream identity and
byte offset, which lets the trusted relay demultiplex interleaved traffic by
local stream identity instead of by accidental byte adjacency.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence

from .model import PublicEvent, coerce_outcome

INTERNAL_MAGIC = b"DM1"
FRAME_MAGIC = b"DX1"
FRAGMENT_MAGIC = b"FR1"

_U32 = struct.Struct(">I")


def _lp(data: bytes) -> bytes:
    """Length-prefixed bytes (u32 big-endian length + payload)."""

    return _U32.pack(len(data)) + data


def _take_lp(buf: bytes, pos: int) -> "tuple[bytes, int]":
    if pos + 4 > len(buf):
        raise ValueError("truncated length prefix")
    (n,) = _U32.unpack_from(buf, pos)
    pos += 4
    if pos + n > len(buf):
        raise ValueError("truncated field")
    return buf[pos : pos + n], pos + n


# ---------------------------------------------------------------------------
# Outer frame: the only thing that crosses to the outsourced console.
# ---------------------------------------------------------------------------


def encode_frame(event: PublicEvent) -> bytes:
    """Canonical, deterministic outer diagnostic frame.

    The output depends *only* on public fields.  It never contains the marker,
    so its bytes and length cannot vary with it.
    """

    receipt = event.receipt_id.encode("utf-8")
    step = event.step_name.encode("utf-8")
    outcome = event.outcome.value.encode("ascii")
    public = receipt + b"\x00" + step + b"\x00" + outcome
    body = FRAME_MAGIC + _lp(receipt) + _lp(step) + _lp(outcome) + _U32.pack(
        zlib.crc32(public) & 0xFFFFFFFF
    )
    return body


def decode_frame(frame: bytes) -> PublicEvent:
    """Parse and integrity-check an outer frame.

    Raises :class:`ValueError` for anything malformed, truncated or carrying a
    bad checksum, so a corrupt / foreign fragment can never be mistaken for a
    public event.
    """

    if not frame.startswith(FRAME_MAGIC):
        raise ValueError("bad frame magic")
    pos = len(FRAME_MAGIC)
    receipt, pos = _take_lp(frame, pos)
    step, pos = _take_lp(frame, pos)
    outcome, pos = _take_lp(frame, pos)
    if pos + 4 != len(frame):
        raise ValueError("bad frame length")
    (crc,) = _U32.unpack_from(frame, pos)
    public = receipt + b"\x00" + step + b"\x00" + outcome
    if (zlib.crc32(public) & 0xFFFFFFFF) != crc:
        raise ValueError("frame checksum mismatch")
    try:
        outcome_enum = coerce_outcome(outcome.decode("ascii"))
    except (UnicodeDecodeError, ValueError) as exc:  # pragma: no cover - defensive
        raise ValueError(f"bad outcome: {exc}") from exc
    return PublicEvent(
        receipt_id=receipt.decode("utf-8"),
        step_name=step.decode("utf-8"),
        outcome=outcome_enum,
    )


# ---------------------------------------------------------------------------
# Inner message: business component -> trusted relay.  Contains the marker.
# ---------------------------------------------------------------------------


def encode_internal(
    receipt_id: str,
    stream_id: str,
    step_name: str,
    outcome: "Outcome | str",
    marker: str = "",
) -> bytes:
    """Serialize a full inner diagnostic message (marker included)."""

    outcome_enum = coerce_outcome(outcome)
    receipt = receipt_id.encode("utf-8")
    stream = stream_id.encode("utf-8")
    step = step_name.encode("utf-8")
    oc = outcome_enum.value.encode("ascii")
    mk = marker.encode("utf-8")
    public = receipt + b"\x00" + step + b"\x00" + oc
    return (
        INTERNAL_MAGIC
        + _lp(receipt)
        + _lp(stream)
        + _lp(step)
        + _lp(oc)
        + _lp(mk)
        + _U32.pack(zlib.crc32(public) & 0xFFFFFFFF)
    )


def parse_internal(raw: bytes) -> "tuple[PublicEvent, str, str]":
    """Parse a *complete* inner message.

    Returns ``(public_event, stream_id, marker)``.  The public projection is
    only returned when the public fields pass their integrity check; otherwise a
    :class:`ValueError` is raised and the relay keeps the message inside.
    """

    if not raw.startswith(INTERNAL_MAGIC):
        raise ValueError("bad internal magic")
    pos = len(INTERNAL_MAGIC)
    receipt, pos = _take_lp(raw, pos)
    stream, pos = _take_lp(raw, pos)
    step, pos = _take_lp(raw, pos)
    oc, pos = _take_lp(raw, pos)
    marker, pos = _take_lp(raw, pos)
    if pos + 4 != len(raw):
        raise ValueError("bad internal length")
    (crc,) = _U32.unpack_from(raw, pos)
    public = receipt + b"\x00" + step + b"\x00" + oc
    if (zlib.crc32(public) & 0xFFFFFFFF) != crc:
        raise ValueError("public checksum mismatch; cannot prove public")
    event = PublicEvent(
        receipt_id=receipt.decode("utf-8"),
        step_name=step.decode("utf-8"),
        outcome=coerce_outcome(oc.decode("ascii")),
    )
    return event, stream.decode("utf-8"), marker.decode("utf-8")


# ---------------------------------------------------------------------------
# Fragments: inner message -> local transport.  Self-delimiting records.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Fragment:
    """One transport fragment of an inner message."""

    stream_id: str
    total: int
    offset: int
    payload: bytes

    def encode(self) -> bytes:
        stream = self.stream_id.encode("utf-8")
        return (
            FRAGMENT_MAGIC
            + _lp(stream)
            + _U32.pack(self.total)
            + _U32.pack(self.offset)
            + _lp(self.payload)
        )

    @classmethod
    def decode(cls, record: bytes) -> "Fragment":
        if not record.startswith(FRAGMENT_MAGIC):
            raise ValueError("bad fragment magic")
        pos = len(FRAGMENT_MAGIC)
        stream, pos = _take_lp(record, pos)
        if pos + 8 > len(record):
            raise ValueError("truncated fragment header")
        (total,) = _U32.unpack_from(record, pos)
        pos += 4
        (offset,) = _U32.unpack_from(record, pos)
        pos += 4
        payload, pos = _take_lp(record, pos)
        if pos != len(record):
            raise ValueError("trailing bytes in fragment")
        return cls(
            stream_id=stream.decode("utf-8"),
            total=total,
            offset=offset,
            payload=payload,
        )


def split_internal(
    raw: bytes,
    stream_id: str,
    boundaries: Optional[Sequence[int]] = None,
) -> "List[Fragment]":
    """Split a full inner message into fragments at absolute byte offsets.

    ``boundaries`` may land anywhere, including in the middle of the marker,
    because fragments are reassembled by explicit offset rather than by byte
    adjacency.  With no boundaries the message is split into up to four pieces.
    """

    total = len(raw)
    if boundaries is None:
        # Four roughly equal pieces, exercising the "written in four parts" case.
        cuts = [0, total // 4, total // 2, (3 * total) // 4, total]
        boundaries = [c for c in cuts[1:-1] if 0 < c < total]
    points = sorted({0, total, *(b for b in boundaries if 0 < b < total)})
    frags: List[Fragment] = []
    for a, b in zip(points, points[1:]):
        frags.append(Fragment(stream_id, total, a, raw[a:b]))
    return frags


def encode_fragments(frags: Iterable[Fragment]) -> bytes:
    """Concatenate fragment records (this is what the interleaver shuffles)."""

    return b"".join(f.encode() for f in frags)
