"""Confidential diagnostic channel for the fictional shipping program.

Public (outer) surface: step names, success/failure, receipt ids, and derived
:class:`~diag.model.Report` values.  Confidential (inner) surface: the drill
marker (演练标记), kept strictly on the local side.

See :mod:`diag.relay` for the two supported routes, :mod:`diag.console` for the
outsourced display console, and :mod:`diag.assembly` for stream reassembly.
"""

from .console import Console, StepTable, StepTableError
from .interleave import interleave_as_pipe, interleave_records, to_pipe_bytes
from .model import (
    Outcome,
    PublicEvent,
    Report,
    Status,
    TypedEvent,
    coerce_outcome,
)
from .relay import LegacyAdapter, TrustedRelay, TypedEmitter
from .wire import (
    Fragment,
    decode_frame,
    encode_frame,
    encode_fragments,
    encode_internal,
    parse_internal,
    split_internal,
)

__all__ = [
    "Console",
    "StepTable",
    "StepTableError",
    "Outcome",
    "PublicEvent",
    "Report",
    "Status",
    "TypedEvent",
    "coerce_outcome",
    "LegacyAdapter",
    "TrustedRelay",
    "TypedEmitter",
    "Fragment",
    "decode_frame",
    "encode_frame",
    "encode_fragments",
    "encode_internal",
    "parse_internal",
    "split_internal",
    "interleave_as_pipe",
    "interleave_records",
    "to_pipe_bytes",
]
