"""Deterministic primitives shared by the service and the auditor."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canon(obj: Any) -> str:
    """Canonical JSON: stable key order, no incidental whitespace."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(obj: Any) -> str:
    return hashlib.sha256(canon(obj).encode("utf-8")).hexdigest()[:32]


def normalize(text: str) -> str:
    return " ".join(text.strip().casefold().split())
