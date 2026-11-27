"""synth_ime - a shareable local input method with revocable personal learning.

The device learns "what follows the previous word" from synthetic corpus
sentences.  Each sentence carries the member who taught it and is sealed under
that member's secret token.  A member's contribution can be withdrawn to the
bit, and after withdrawal the device behaves exactly like one that never
contained that member's teaching.

Design: per-owner, independently sealed sentences; suggestions combine only the
libraries whose tokens are currently loaded ("present").  See DESIGN.md.
"""

from .engine import (  # noqa: F401
    AuthError,
    Device,
    ImeError,
    JsonStore,
    MemoryStore,
    ReplayError,
    Store,
)

__all__ = [
    "AuthError",
    "Device",
    "ImeError",
    "JsonStore",
    "MemoryStore",
    "ReplayError",
    "Store",
]
