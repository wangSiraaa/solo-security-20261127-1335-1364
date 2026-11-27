"""Minimal, dependency-free Ed25519 operations used by the simulation.

This is intentionally small and uses only the Python standard library so the
local exercise can run without installing packages.  It implements RFC 8032
key generation/signing/verification.
"""
from __future__ import annotations

import hashlib
import secrets
from typing import Tuple

P = 2**255 - 19
L = 2**252 + 27742317777372353535851937790883648493


def _h(message: bytes) -> bytes:
    return hashlib.sha512(message).digest()


def _inv(x: int) -> int:
    return pow(x, P - 2, P)


_D = (-121665 * _inv(121666)) % P
_I = pow(2, (P - 1) // 4, P)


def _x_recover(y: int) -> int:
    xx = (y * y - 1) * _inv(_D * y * y + 1) % P
    x = pow(xx, (P + 3) // 8, P)
    if (x * x - xx) % P != 0:
        x = (x * _I) % P
    if x % 2 != 0:
        x = P - x
    return x


_BY = 4 * _inv(5) % P
_B = (_x_recover(_BY), _BY)


def _add(p: Tuple[int, int], q: Tuple[int, int]) -> Tuple[int, int]:
    x1, y1 = p
    x2, y2 = q
    x3 = (x1 * y2 + x2 * y1) * _inv(1 + _D * x1 * x2 * y1 * y2) % P
    y3 = (y1 * y2 + x1 * x2) * _inv(1 - _D * x1 * x2 * y1 * y2) % P
    return x3 % P, y3 % P


def _mul(p: Tuple[int, int], e: int) -> Tuple[int, int]:
    if e == 0:
        return 0, 1
    q = _mul(p, e // 2)
    q = _add(q, q)
    if e & 1:
        q = _add(q, p)
    return q


def _encode_point(p: Tuple[int, int]) -> bytes:
    x, y = p
    out = bytearray(y.to_bytes(32, "little"))
    out[31] |= (x & 1) << 7
    return bytes(out)


def _decode_point(blob: bytes) -> Tuple[int, int]:
    if len(blob) != 32:
        raise ValueError("bad point length")
    sign = blob[31] >> 7
    y = int.from_bytes(blob, "little") & ((1 << 255) - 1)
    if y >= P:
        raise ValueError("invalid y")
    x = _x_recover(y)
    if (x & 1) != sign:
        x = P - x
    if x >= P:
        raise ValueError("invalid x")
    return x, y


def _bit(h: bytes, i: int) -> int:
    return (h[i // 8] >> (i % 8)) & 1


def _secret_scalar(secret: bytes) -> int:
    h = _h(secret)
    return 2**254 + sum(2**i * _bit(h, i) for i in range(3, 254))


def generate_secret_key() -> bytes:
    return secrets.token_bytes(32)


def public_key(secret: bytes) -> bytes:
    if len(secret) != 32:
        raise ValueError("secret key must be 32 bytes")
    return _encode_point(_mul(_B, _secret_scalar(secret)))


def sign(message: bytes, secret: bytes) -> bytes:
    h = _h(secret)
    a = _secret_scalar(secret)
    a_blob = public_key(secret)
    r = int.from_bytes(_h(h[32:] + message), "little") % L
    r_blob = _encode_point(_mul(_B, r))
    k = int.from_bytes(_h(r_blob + a_blob + message), "little") % L
    s = (r + k * a) % L
    return r_blob + s.to_bytes(32, "little")


def verify(message: bytes, signature: bytes, public: bytes) -> bool:
    try:
        if len(signature) != 64 or len(public) != 32:
            return False
        r_blob = signature[:32]
        s = int.from_bytes(signature[32:], "little")
        if s >= L:
            return False
        r = _decode_point(r_blob)
        a = _decode_point(public)
        k = int.from_bytes(_h(r_blob + public + message), "little") % L
        lhs = _mul(_B, s)
        rhs = _add(r, _mul(a, k))
        return lhs[0] % P == rhs[0] % P and lhs[1] % P == rhs[1] % P
    except Exception:
        return False
