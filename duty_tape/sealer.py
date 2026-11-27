"""Test trusted sealer and receipt verifier.

The exercise uses a publicly known test HMAC key.  It is not production
cryptography; it gives the recovery terminal a concrete, checkable boundary:
only receipts carrying a valid seal from this sealer are accepted.
"""

from __future__ import annotations

import hmac
import os
from pathlib import Path
from typing import Any, Dict

from .canonical import canonical_bytes

DEFAULT_TEST_KEY = b"duty-tape-public-test-sealer-key-v1"
DEFAULT_SEALER_ID = "in-problem-test-sealer-v1"


class ReceiptError(ValueError):
    pass


class TestSealer:
    def __init__(self, key: bytes = DEFAULT_TEST_KEY, sealer_id: str = DEFAULT_SEALER_ID):
        self.key = key
        self.sealer_id = sealer_id

    @classmethod
    def from_key_file(cls, path: str | os.PathLike[str] | None) -> "TestSealer":
        if path is None:
            return cls()
        p = Path(path)
        return cls(p.read_bytes().strip())

    def _mac(self, payload: Dict[str, Any]) -> str:
        return hmac.new(self.key, canonical_bytes(payload), "sha256").hexdigest()

    def seal(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        receipt = dict(payload)
        receipt["sealer_id"] = self.sealer_id
        receipt["mac"] = self._mac({k: v for k, v in receipt.items() if k != "mac"})
        return receipt

    def verify(self, receipt: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(receipt, dict):
            raise ReceiptError("receipt must be a JSON object")
        mac = receipt.get("mac")
        if not isinstance(mac, str):
            raise ReceiptError("receipt has no mac")
        payload = {k: v for k, v in receipt.items() if k != "mac"}
        expected = self._mac(payload)
        if not hmac.compare_digest(mac, expected):
            raise ReceiptError("receipt seal is invalid")
        if receipt.get("sealer_id") != self.sealer_id:
            raise ReceiptError(f"unknown sealer: {receipt.get('sealer_id')!r}")
        return payload


def new_revocation(
    sealer: TestSealer,
    *,
    reason: str,
    issued_at: str,
    artifact_digest: str | None = None,
    batch: str | None = None,
) -> Dict[str, Any]:
    if (artifact_digest is None) == (batch is None):
        raise ValueError("exactly one of artifact_digest or batch is required")
    target: Dict[str, Any]
    if artifact_digest is not None:
        target = {"type": "artifact", "digest": artifact_digest}
    else:
        target = {"type": "batch", "batch": batch}
    payload = {
        "schema": "duty-tape.revocation/v1",
        "target": target,
        "reason": reason,
        "issued_at": issued_at,
    }
    return sealer.seal(payload)
