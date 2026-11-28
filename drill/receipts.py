"""Signed save receipts.

A receipt is what the teacher actually holds.  Recovery must be able to
*reverify* a receipt with only the receipt bytes + the classroom's shared key,
so a receipt the server never actually minted (e.g. one backed by cache-only
writes) can be told apart from a real one.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any, Dict, Optional


def _canon(payload: Dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign_receipt(payload: Dict[str, Any], key: bytes) -> Dict[str, Any]:
    mac = hmac.new(key, _canon(payload), hashlib.sha256).digest()
    return {
        "payload": payload,
        "mac": base64.b64encode(mac).decode("ascii"),
    }


def verify_receipt(receipt: Dict[str, Any], key: bytes) -> bool:
    try:
        payload = receipt["payload"]
        mac = base64.b64decode(receipt["mac"])
    except (KeyError, TypeError, ValueError):
        return False
    return hmac.compare_digest(mac, hmac.new(key, _canon(payload), hashlib.sha256).digest())


def receipt_digest(receipt: Dict[str, Any]) -> str:
    return hashlib.sha256(_canon(receipt)).hexdigest()
