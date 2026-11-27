"""Symmetric sealing used for the "sealed packet in the cabinet".

No production cipher library is installed, so this is an HMAC-SHA256 based
stream construction with an encrypt-then-MAC tag.  It is used only to make
"reading the original" a real, mediated act: the vault stores ciphertext,
and a party can only decrypt after its claim credential exposes the key.
"""

from __future__ import annotations

import hashlib
import hmac
import os

NONCE_LEN = 16
TAG_LEN = 32


def _keystream(key: bytes, nonce: bytes, n: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < n:
        block = hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:n])


def seal(plaintext: bytes, key: bytes) -> bytes:
    nonce = os.urandom(NONCE_LEN)
    ks = _keystream(key, nonce, len(plaintext))
    ct = bytes(a ^ b for a, b in zip(plaintext, ks))
    tag = hmac.new(key, nonce + ct, hashlib.sha256).digest()
    return nonce + ct + tag


def unseal(blob: bytes, key: bytes) -> bytes:
    if len(blob) < NONCE_LEN + TAG_LEN:
        raise ValueError("sealed packet truncated")
    nonce = blob[:NONCE_LEN]
    ct = blob[NONCE_LEN:-TAG_LEN]
    tag = blob[-TAG_LEN:]
    expected = hmac.new(key, nonce + ct, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected):
        raise ValueError("sealed packet failed integrity tag")
    ks = _keystream(key, nonce, len(ct))
    return bytes(a ^ b for a, b in zip(ct, ks))
