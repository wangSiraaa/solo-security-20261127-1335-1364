"""Cryptographic helpers used by the simulated update controller.

The simulator uses HMAC receipts to model receipts issued by a device's
protected key.  It is not intended as a real boot-chain implementation.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from typing import Any, Dict


def sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def set_digest(file_digests: Dict[str, str]) -> str:
    """Digest of the complete approved path -> digest set."""
    return sha256_bytes(canonical_json(file_digests))


def sign_payload(secret: str, payload: Dict[str, Any]) -> str:
    body = {k: v for k, v in payload.items() if k != "signature"}
    mac = hmac.new(secret.encode("utf-8"), canonical_json(body), hashlib.sha256)
    return mac.hexdigest()


def verify_signature(secret: str, payload: Dict[str, Any]) -> bool:
    supplied = payload.get("signature")
    if not isinstance(supplied, str):
        return False
    expected = sign_payload(secret, payload)
    return hmac.compare_digest(expected, supplied)
