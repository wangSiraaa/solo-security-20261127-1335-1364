"""Minimal, dependency-free asymmetric crypto for the fair-exchange exercise.

Only the *structure* of asymmetric signatures matters for the protocol:
the vault must hold only the parties' **public** keys, so that the escrow
service structurally cannot sign an unseal receipt on a party's behalf.
No cryptographic library is available in the environment, so we implement
RSA-1024 + PKCS#1 v1.5 (SHA-256) in pure Python.

This is NOT production cryptography (no blinding, no constant-time
guarantees, small keys).  It is here purely to enforce key separation in a
local, restartable exercise.
"""

from __future__ import annotations

import hashlib
import json
import math
import secrets
from typing import Any, Dict

E = 65537

# --- canonical encoding -----------------------------------------------------

def canonical_json(obj: Any) -> bytes:
    """Deterministic JSON encoding used for signing and ledgers."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha256_hex(*parts: Any) -> str:
    h = hashlib.sha256()
    for p in parts:
        if isinstance(p, bytes):
            h.update(p)
        elif isinstance(p, str):
            h.update(p.encode())
        else:
            h.update(canonical_json(p))
    return h.hexdigest()


# --- primality --------------------------------------------------------------

def _sieve(limit: int):
    s = bytearray(b"\x01") * (limit + 1)
    s[0:2] = b"\x00\x00"
    for i in range(2, int(limit ** 0.5) + 1):
        if s[i]:
            s[i * i :: i] = b"\x00" * (((limit - i * i) // i) + 1)
    return [i for i in range(limit + 1) if s[i]]


_SMALL_PRIMES = _sieve(20000)


def _is_probable_prime(n: int, rounds: int = 40) -> bool:
    if n < 2:
        return False
    for p in _SMALL_PRIMES:
        if n % p == 0:
            return n == p
    d = n - 1
    s = 0
    while d % 2 == 0:
        d //= 2
        s += 1
    for _ in range(rounds):
        a = secrets.randbelow(n - 3) + 2
        x = pow(a, d, n)
        if x == 1 or x == n - 1:
            continue
        for _ in range(s - 1):
            x = x * x % n
            if x == n - 1:
                break
        else:
            return False
    return True


def _gen_prime(bits: int) -> int:
    while True:
        c = secrets.randbits(bits) | (1 << (bits - 1)) | (1 << (bits - 2)) | 1
        if math.gcd(c - 1, E) == 1 and _is_probable_prime(c):
            return c


# --- keys -------------------------------------------------------------------

def keygen(bits: int = 1024) -> Dict[str, Any]:
    p = _gen_prime(bits // 2)
    q = _gen_prime(bits // 2)
    while p == q:
        q = _gen_prime(bits // 2)
    n = p * q
    d = pow(E, -1, (p - 1) * (q - 1))
    return {"n": hex(n), "e": hex(E), "d": hex(d), "bits": bits}


def public_part(key: Dict[str, Any]) -> Dict[str, Any]:
    return {"n": key["n"], "e": key["e"], "bits": key.get("bits", 1024)}


# DigestInfo prefix for SHA-256 (PKCS#1 v1.5)
_SHA256_DER_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")


def _int_of(hexstr: str) -> int:
    return int(hexstr, 16)


def sign(message: bytes, key: Dict[str, Any]) -> str:
    n = _int_of(key["n"])
    d = _int_of(key["d"])
    k = (n.bit_length() + 7) // 8
    t = _SHA256_DER_PREFIX + hashlib.sha256(message).digest()
    if k < len(t) + 11:
        raise ValueError("key too small for PKCS#1 v1.5")
    ps = b"\xff" * (k - len(t) - 3)
    em = b"\x00\x01" + ps + b"\x00" + t
    m = int.from_bytes(em, "big")
    s = pow(m, d, n)
    return s.to_bytes(k, "big").hex()


def verify(message: bytes, signature_hex: str, pub: Dict[str, Any]) -> bool:
    try:
        n = _int_of(pub["n"])
        e = _int_of(pub["e"])
        k = (n.bit_length() + 7) // 8
        s = int(signature_hex, 16)
        if s < 0 or s >= n:
            return False
        m = pow(s, e, n)
        em = m.to_bytes(k, "big")
        t = _SHA256_DER_PREFIX + hashlib.sha256(message).digest()
        ps_len = k - len(t) - 3
        if ps_len < 8:
            return False
        expected = b"\x00\x01" + b"\xff" * ps_len + b"\x00" + t
        return em == expected
    except Exception:
        return False


def encrypt(plaintext: bytes, pub: Dict[str, Any]) -> str:
    """RSA PKCS#1 v1.5 encryption. Used so the vault only escrows a key-encryption
    blob that the *recipient* can open; the vault itself cannot recover the claim key."""
    n = _int_of(pub["n"])
    e = _int_of(pub["e"])
    k = (n.bit_length() + 7) // 8
    if len(plaintext) > k - 11:
        raise ValueError("plaintext too long for key")
    ps = bytes((b or 1) for b in secrets.token_bytes(k - len(plaintext) - 3))
    em = b"\x00\x02" + ps + b"\x00" + plaintext
    m = int.from_bytes(em, "big")
    c = pow(m, e, n)
    return c.to_bytes(k, "big").hex()


def decrypt(cipher_hex: str, key: Dict[str, Any]) -> bytes:
    n = _int_of(key["n"])
    d = _int_of(key["d"])
    k = (n.bit_length() + 7) // 8
    c = int(cipher_hex, 16)
    m = pow(c, d, n)
    em = m.to_bytes(k, "big")
    if not em.startswith(b"\x00\x02"):
        raise ValueError("bad padding")
    idx = em.find(b"\x00", 2)
    if idx < 10:
        raise ValueError("bad padding")
    return em[idx + 1 :]


def sign_payload(payload: Dict[str, Any], key: Dict[str, Any]) -> Dict[str, Any]:
    return {"payload": payload, "sig": sign(canonical_json(payload), key)}


def verify_payload(signed: Dict[str, Any], pub: Dict[str, Any]) -> bool:
    if not isinstance(signed, dict) or "payload" not in signed or "sig" not in signed:
        return False
    return verify(canonical_json(signed["payload"]), signed["sig"], pub)
