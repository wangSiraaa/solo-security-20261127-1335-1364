"""Authenticated sealing built on HMAC-SHA256 (stdlib only).

The platform has no third-party crypto library, so we compose a sound
encrypt-then-MAC construction from HMAC-SHA256:

  * keystream: HMAC-SHA256(key_enc, nonce || counter_be64) in counter mode
  * tag:       HMAC-SHA256(key_mac, "tag" || aad_len || aad || nonce || ct)

Assuming HMAC-SHA256 is a PRF (standard), this gives IND-CPA confidentiality
plus integrity/authenticity of the ciphertext and associated data.

AAD is length-prefixed so that ambiguous concatenations cannot collide.
Nonces are 96 random bits per message; the counter is 64 bits, which is far
more than any sealed payload here needs.
"""

from __future__ import annotations

import hashlib
import hmac
import os

NONCE_SIZE = 12
TAG_SIZE = 32
KEY_SIZE = 32


class SealError(Exception):
    """Raised when a sealed blob is malformed or fails authentication."""


def new_key() -> bytes:
    return os.urandom(KEY_SIZE)


def new_token() -> str:
    """A bearer token a member keeps privately to authorise their actions."""
    return os.urandom(32).hex()


def token_hash(token: str) -> str:
    """One-way commitment to a member token; safe to persist."""
    return hashlib.sha256(("synthime-token-v1:" + token).encode("utf-8")).hexdigest()


def _derive(key: bytes, label: bytes) -> bytes:
    return hmac.new(key, b"synthime-seal-v1:" + label, hashlib.sha256).digest()


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    enc_key = _derive(key, b"enc")
    out = bytearray()
    counter = 0
    while len(out) < length:
        out.extend(hmac.new(enc_key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest())
        counter += 1
    return bytes(out[:length])


def seal(key: bytes, plaintext: bytes, aad: bytes = b"") -> bytes:
    nonce = os.urandom(NONCE_SIZE)
    stream = _keystream(key, nonce, len(plaintext))
    ciphertext = bytes(a ^ b for a, b in zip(plaintext, stream))
    mac_key = _derive(key, b"mac")
    tag = hmac.new(
        mac_key,
        b"tag" + len(aad).to_bytes(8, "big") + aad + nonce + ciphertext,
        hashlib.sha256,
    ).digest()
    return nonce + ciphertext + tag


def unseal(key: bytes, blob: bytes, aad: bytes = b"") -> bytes:
    if len(blob) < NONCE_SIZE + TAG_SIZE:
        raise SealError("sealed blob is too short")
    nonce = blob[:NONCE_SIZE]
    ciphertext = blob[NONCE_SIZE:-TAG_SIZE]
    tag = blob[-TAG_SIZE:]
    mac_key = _derive(key, b"mac")
    expected = hmac.new(
        mac_key,
        b"tag" + len(aad).to_bytes(8, "big") + aad + nonce + ciphertext,
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(tag, expected):
        raise SealError("sealed blob failed authentication")
    stream = _keystream(key, nonce, len(ciphertext))
    return bytes(a ^ b for a, b in zip(ciphertext, stream))
