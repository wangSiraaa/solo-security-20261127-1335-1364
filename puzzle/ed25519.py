"""Minimal, dependency-free Ed25519 (RFC 8032).

Pure Python, extended twisted-Edwards coordinates. This is the only
cryptographic primitive the system needs: signed map commitments, signed
receipts, and signed midpoint attestations all use it. Random signatures are
deterministic per RFC 8032, so signed bytes are reproducible too.

The implementation is intentionally small and auditable rather than
constant-time; it is a puzzle-score verifier, not a side-channel-hardened
signer. Correctness is pinned by the RFC 8032 test vectors in
tests/test_ed25519.py.
"""
from __future__ import annotations

import hashlib
from typing import Tuple

# Curve constants (ed25519).
P = 2**255 - 19
L = 2**252 + 27742317777372353535851937790883648493
D = (-121665 * pow(121666, P - 2, P)) % P
I = pow(2, (P - 1) // 4, P)  # sqrt(-1)

# Points are (X, Y, Z, T) in extended coordinates; affine x=X/Z, y=Y/Z.
Point = Tuple[int, int, int, int]

_IDENTITY: Point = (0, 1, 1, 0)


def _inv(x: int) -> int:
    return pow(x, P - 2, P)


def _add(p: Point, q: Point) -> Point:
    x1, y1, z1, t1 = p
    x2, y2, z2, t2 = q
    a = ((y1 - x1) * (y2 - x2)) % P
    b = ((y1 + x1) * (y2 + x2)) % P
    c = (t1 * 2 * D * t2) % P
    d = (z1 * 2 * z2) % P
    e = (b - a) % P
    f = (d - c) % P
    g = (d + c) % P
    h = (b + a) % P
    return ((e * f) % P, (g * h) % P, (f * g) % P, (e * h) % P)


def _double(p: Point) -> Point:
    x1, y1, z1, _t1 = p
    a = (x1 * x1) % P
    b = (y1 * y1) % P
    c = (2 * z1 * z1) % P
    d = (-a) % P  # a = -1 for ed25519
    e = ((x1 + y1) * (x1 + y1) - a - b) % P
    g = (d + b) % P
    f = (g - c) % P
    h = (d - b) % P
    return ((e * f) % P, (g * h) % P, (f * g) % P, (e * h) % P)


def _mul(p: Point, e: int) -> Point:
    e %= L
    q = _IDENTITY
    while e > 0:
        if e & 1:
            q = _add(q, p)
        p = _double(p)
        e >>= 1
    return q


def _encode_point(p: Point) -> bytes:
    x, y, z, _t = p
    zi = _inv(z)
    x = (x * zi) % P
    y = (y * zi) % P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def _decode_point(s: bytes) -> Point:
    if len(s) != 32:
        raise ValueError("point must be 32 bytes")
    y = int.from_bytes(s, "little")
    sign = (y >> 255) & 1
    y &= (1 << 255) - 1
    if y >= P:
        raise ValueError("non-canonical y")
    y2 = (y * y) % P
    x2 = ((y2 - 1) * _inv((D * y2 + 1) % P)) % P
    x = pow(x2, (P + 3) // 8, P)
    if (x * x - x2) % P != 0:
        x = (x * I) % P
    if (x * x - x2) % P != 0:
        raise ValueError("point not on curve")
    if x == 0 and sign:
        raise ValueError("bad sign bit")
    if (x & 1) != sign:
        x = P - x
    return (x, y, 1, (x * y) % P)


def _sha512(data: bytes) -> bytes:
    return hashlib.sha512(data).digest()


def _expand_seed(seed: bytes) -> Tuple[int, bytes]:
    if len(seed) != 32:
        raise ValueError("seed must be 32 bytes")
    h = _sha512(seed)
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


# Base point B: y = 4/5, x is the even square root.
_BY = (4 * _inv(5)) % P
_BX = pow(((_BY * _BY - 1) * _inv((D * _BY * _BY + 1) % P)) % P, (P + 3) // 8, P)
if (_BX * _BX - ((_BY * _BY - 1) * _inv((D * _BY * _BY + 1) % P))) % P != 0:
    _BX = (_BX * I) % P
if _BX % 2 != 0:
    _BX = P - _BX
_B: Point = (_BX, _BY, 1, (_BX * _BY) % P)


def public_key(seed: bytes) -> bytes:
    a, _prefix = _expand_seed(seed)
    return _encode_point(_mul(_B, a))


def sign(seed: bytes, message: bytes) -> bytes:
    a, prefix = _expand_seed(seed)
    pub = _encode_point(_mul(_B, a))
    r = int.from_bytes(_sha512(prefix + message), "little") % L
    r_enc = _encode_point(_mul(_B, r))
    k = int.from_bytes(_sha512(r_enc + pub + message), "little") % L
    s = (r + k * a) % L
    return r_enc + s.to_bytes(32, "little")


def verify(public: bytes, message: bytes, signature: bytes) -> bool:
    if len(signature) != 64 or len(public) != 32:
        return False
    r_enc = signature[:32]
    s_int = int.from_bytes(signature[32:], "little")
    if s_int >= L:
        return False
    try:
        a_point = _decode_point(public)
        r_point = _decode_point(r_enc)
    except ValueError:
        return False
    k = int.from_bytes(_sha512(r_enc + public + message), "little") % L
    left = _mul(_B, s_int)
    right = _add(r_point, _mul(a_point, k))
    return _encode_point(left) == _encode_point(right)
