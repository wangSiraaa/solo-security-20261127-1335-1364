"""Minimal pure-Python Ed25519 (RFC 8032) for signing the edition manifest.

Only keygen / sign / verify are needed, for a handful of small documents, so a
straightforward big-integer implementation is adequate.  Correctness is pinned
to the RFC 8032 test vector in selftest().
"""
import hashlib
import os

p = 2 ** 255 - 19
q = 2 ** 252 + 27742317777372353535851937790883648493       # group order L
d = -121665 * pow(121666, p - 2, p) % p
I = pow(2, (p - 1) // 4, p)


def _sha512_int(m):
    return int.from_bytes(hashlib.sha512(m).digest(), "little")


def _inv(x):
    return pow(x, p - 2, p)


def _xrecover(y):
    xx = (y * y - 1) * _inv(d * y * y + 1)
    x = pow(xx, (p + 3) // 8, p)
    if (x * x - xx) % p != 0:
        x = (x * I) % p
    if x % 2 != 0:
        x = p - x
    return x


_By = 4 * _inv(5)
_Bx = _xrecover(_By)
_B = (_Bx % p, _By % p)


def _edwards_add(P, Q):
    x1, y1 = P
    x2, y2 = Q
    k = d * x1 * x2 * y1 * y2 % p
    x3 = (x1 * y2 + x2 * y1) * _inv(1 + k)
    y3 = (y1 * y2 + x1 * x2) * _inv(1 - k)
    return (x3 % p, y3 % p)


def _scalarmult(P, e):
    if e == 0:
        return (0, 1)
    Q = _scalarmult(P, e // 2)
    Q = _edwards_add(Q, Q)
    if e & 1:
        Q = _edwards_add(Q, P)
    return Q


def _encode_point(P):
    x, y = P
    bits = [(y >> i) & 1 for i in range(255)] + [x & 1]
    return bytes(sum(bits[i * 8 + j] << j for j in range(8))
                 for i in range(32))


def _decode_point(s):
    if len(s) != 32:
        raise ValueError("bad point length")
    y = int.from_bytes(s, "little") & ((1 << 255) - 1)
    x = _xrecover(y)
    if (x & 1) != (s[31] >> 7):
        x = p - x
    P = (x, y)
    if not _on_curve(P):
        raise ValueError("point not on curve")
    return P


def _on_curve(P):
    x, y = P
    return (-x * x + y * y - 1 - d * x * x * y * y) % p == 0


def _hash_to_point(r):
    h = hashlib.sha512(r).digest()
    return _scalarmult(_B, int.from_bytes(h, "little") % q)


def keygen(seed=None):
    if seed is None:
        seed = os.urandom(32)
    if len(seed) != 32:
        raise ValueError("seed must be 32 bytes")
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= (1 << 254)
    A = _scalarmult(_B, a)
    return seed, _encode_point(A)


def sign(seed, pub, msg):
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= (1 << 254)
    prefix = h[32:]
    r = int.from_bytes(hashlib.sha512(prefix + msg).digest(), "little") % q
    R = _scalarmult(_B, r)
    Renc = _encode_point(R)
    k = int.from_bytes(hashlib.sha512(Renc + pub + msg).digest(),
                       "little") % q
    S = (r + k * a) % q
    return Renc + S.to_bytes(32, "little")


def verify(pub, msg, signature):
    if len(signature) != 64:
        return False
    try:
        R = _decode_point(signature[:32])
        A = _decode_point(pub)
    except ValueError:
        return False
    S = int.from_bytes(signature[32:], "little")
    if S >= q:
        return False
    k = int.from_bytes(hashlib.sha512(signature[:32] + pub + msg).digest(),
                       "little") % q
    # check S*B == R + k*A
    left = _scalarmult(_B, S)
    right = _edwards_add(R, _scalarmult(A, k))
    return left == right


def selftest():
    seed = bytes.fromhex(
        "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
    want_pub = bytes.fromhex(
        "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
    want_sig = bytes.fromhex(
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
        "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b")
    _, pub = keygen(seed)
    assert pub == want_pub, "pubkey mismatch"
    sig = sign(seed, pub, b"")
    assert sig == want_sig, "signature mismatch"
    assert verify(pub, b"", sig)
    assert not verify(pub, b"x", sig)
    return True


if __name__ == "__main__":
    print("ed25519 selftest:", selftest())
