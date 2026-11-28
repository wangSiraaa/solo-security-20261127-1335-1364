"""Classroom credential material.

In production these keys live in a per-classroom credential store.  In the
drill they are derived deterministically from the classroom name so the
*recovery* process can verify held receipts without importing the scenario
builder or the engine.
"""
from __future__ import annotations

import hashlib
from typing import Dict


def classroom_key(name: str) -> bytes:
    return hashlib.sha256(f"classroom-key::{name}".encode()).digest()


def keys_for(*names: str) -> Dict[str, bytes]:
    return {n: classroom_key(n) for n in names}
