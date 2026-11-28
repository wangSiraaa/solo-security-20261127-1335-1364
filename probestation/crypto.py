"""Off-the-shelf message authentication (HMAC-SHA256) with canonical encoding.

No real hardware is involved: keys here are local stand-ins for the factory
secret of a probe and the management-line signing key.  The point is that
every receipt is *authenticated* and bound to a *fresh session nonce*, so a
receipt left over from the previous shift proves nothing for this session.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from typing import Any


def canon(obj: Any) -> bytes:
    """Deterministic canonical bytes for a JSON-serialisable object."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def mac(key: bytes, obj: Any) -> str:
    """HMAC-SHA256 tag over the canonical form of ``obj``."""
    return hmac.new(key, canon(obj), hashlib.sha256).hexdigest()


def verify_mac(key: bytes, obj: Any, tag: str) -> bool:
    """Constant-time verification of an HMAC tag."""
    return hmac.compare_digest(mac(key, obj), tag)


def key_fingerprint(secret: bytes) -> str:
    """Short public fingerprint of a probe secret, bound inside its attestation."""
    return hashlib.sha256(secret).hexdigest()[:16]


def fresh_nonce(nbytes: int = 16) -> str:
    """A fresh per-session random nonce."""
    return secrets.token_hex(nbytes)
