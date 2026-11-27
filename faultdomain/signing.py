"""A tiny RSA signer so the trusted verifier can issue proofs the station can
*verify* but cannot *forge or renew on its own*.

This is deliberately a toy: textbook RSA over SHA-256, no padding, small
modulus.  It is here to model the **asymmetry of capability** that matters for
the exercise -- the station holds only the verification key, so it cannot mint a
fresh isolation proof after a segment changes.  Do not use this for anything
real.
"""

from __future__ import annotations

import hashlib
import math
import secrets
from typing import Tuple


def _is_probable_prime(n: int, rounds: int = 40) -> bool:
    if n < 2:
        return False
    for p in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if n % p == 0:
            return n == p
    d, r = n - 1, 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for _ in range(rounds):
        a = secrets.randbelow(n - 3) + 2
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _gen_prime(bits: int) -> int:
    while True:
        cand = secrets.randbits(bits) | (1 << (bits - 1)) | 1
        if _is_probable_prime(cand):
            return cand


def _digest_int(message: bytes) -> int:
    tagged = b"faultdomain-proof-v1\x00" + message
    return int.from_bytes(hashlib.sha256(tagged).digest(), "big")


class ToySigner:
    """Holds the signing key.  ``public()`` returns the verification key."""

    def __init__(self, bits: int = 512) -> None:
        e = 65537
        while True:
            p = _gen_prime(bits)
            q = _gen_prime(bits)
            if p == q:
                continue
            n = p * q
            phi = (p - 1) * (q - 1)
            if math.gcd(e, phi) == 1:
                self.n, self.e, self.d = n, e, pow(e, -1, phi)
                break

    def public(self) -> Tuple[int, int, int]:
        """Return ``(n, e, max_sig_bits)`` to transport the verify key as JSON."""
        return (self.n, self.e, self.n.bit_length())

    def sign(self, message: bytes) -> int:
        return pow(_digest_int(message), self.d, self.n)


def verify(message: bytes, signature: int, public: Tuple[int, int, int]) -> bool:
    n, e, _bits = public
    if signature <= 0 or signature >= n:
        return False
    return pow(signature, e, n) == _digest_int(message)
