"""Toy cryptography.

NO REAL SECURITY. These are deterministic HMACs over a hard-coded toy secret,
used only so the demo/tests can show that a swapped or edited receipt does not
verify. There are no real keys anywhere in this project.

The point of the "signature" here is *binding*, not secrecy: a receipt is bound
to (chapter, lease, device, counter). If you substitute a receipt from another
chapter, the binding check fails even before the MAC is consulted.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Mapping

# Deliberately public toy secret. Not a key you would ever use for anything.
TOY_DESK_SECRET = b"toy-desk-secret::exercise-1384"
TOY_DEVICE_SECRET_PREFIX = b"toy-device::"


def device_secret(device_id: str) -> bytes:
    """Deterministic per-device toy key. The desk 'knows' it (toy trust model)."""
    return TOY_DEVICE_SECRET_PREFIX + device_id.encode("utf-8")


def canonical(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")


def mac(key: bytes, payload: Mapping[str, Any]) -> str:
    return hmac.new(key, canonical(payload), hashlib.sha256).hexdigest()


def verify(key: bytes, payload: Mapping[str, Any], signature: str) -> bool:
    expected = mac(key, payload)
    return hmac.compare_digest(expected, signature or "")
