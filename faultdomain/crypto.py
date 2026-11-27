"""Hashing / commitment primitives built only on the Python standard library."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical(obj: Any) -> bytes:
    """Deterministic JSON encoding used everywhere a digest is taken."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def H(obj: Any) -> str:
    """SHA-256 over the canonical encoding of ``obj``."""
    return hashlib.sha256(canonical(obj)).hexdigest()


def Hraw(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
