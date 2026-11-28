"""Signed artifacts and content commitments.

Every trusted object is a ``{"body": {...}, "sig": hex}`` envelope where the
signature is Ed25519 over ``canon_bytes(body)``.  ``body["signer"]`` names the
public key so a verifier knows whose signature to check.  A *commitment* is the
sha256 of the canonical body — it is what receipts chain to, so a whole run can
be referenced by a short hash that cannot be swapped for different content.
"""
from __future__ import annotations

import os
from typing import Any, Dict

from . import ed25519
from .canon import canon_bytes, hx_obj

ENVELOPE_VERSION = 1


class SignatureError(Exception):
    pass


class Key:
    """An Ed25519 keypair. Seeds are 32 bytes."""

    def __init__(self, seed: bytes):
        if len(seed) != 32:
            raise ValueError("seed must be 32 bytes")
        self._seed = seed
        self.public = ed25519.public_key(seed)

    @classmethod
    def generate(cls) -> "Key":
        return cls(os.urandom(32))

    @classmethod
    def from_label(cls, label: str) -> "Key":
        """Deterministic key from a label (for tests/fixtures, not production)."""
        from .canon import sha256

        return cls(sha256(b"racekey/" + label.encode("utf-8")))

    @property
    def pub_hex(self) -> str:
        return self.public.hex()

    def sign_body(self, body: Dict[str, Any]) -> Dict[str, Any]:
        b = dict(body)
        b["signer"] = self.pub_hex
        sig = ed25519.sign(self._seed, canon_bytes(b))
        return {"v": ENVELOPE_VERSION, "body": b, "sig": sig.hex()}

    def verify_envelope(self, env: Dict[str, Any]) -> Dict[str, Any]:
        """Verify that ``env`` was signed by *this* key; return its body."""
        body = _check_envelope(env)
        if body["signer"] != self.pub_hex:
            raise SignatureError("signer mismatch")
        return body


def _check_envelope(env: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(env, dict) or set(env) != {"v", "body", "sig"}:
        raise SignatureError("malformed envelope")
    if env["v"] != ENVELOPE_VERSION:
        raise SignatureError("unsupported envelope version")
    body = env["body"]
    if not isinstance(body, dict) or "signer" not in body:
        raise SignatureError("malformed body")
    try:
        pub = bytes.fromhex(body["signer"])
        sig = bytes.fromhex(env["sig"])
    except (ValueError, TypeError) as exc:
        raise SignatureError("bad hex") from exc
    if not ed25519.verify(pub, canon_bytes(body), sig):
        raise SignatureError("bad signature")
    return body


def verify_any(env: Dict[str, Any]) -> Dict[str, Any]:
    """Verify an envelope against the public key named inside it."""
    return _check_envelope(env)


def commit_of(env: Dict[str, Any]) -> str:
    """Content commitment of a signed envelope (hash of its body)."""
    return hx_obj(env["body"])


def envelope_bytes(env: Dict[str, Any]) -> bytes:
    """The exact bytes handed to another party for a signed object."""
    return canon_bytes(env)
