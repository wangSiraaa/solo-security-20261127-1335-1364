"""Canonical serialization and hashing.

Everything that is signed, chained, or handed to another party is first
serialized with a single canonical JSON encoding so that *bytes* are
reproducible: same object in, same bytes out, regardless of insertion order.
The acceptance check ("compare the actual handed-over bytes") relies on this.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def canon_bytes(obj: Any) -> bytes:
    """Deterministic JSON encoding used for every signature/hash/file."""
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def hx(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hx_obj(obj: Any) -> str:
    """sha256 over the canonical encoding of ``obj`` (hex)."""
    return hashlib.sha256(canon_bytes(obj)).hexdigest()
