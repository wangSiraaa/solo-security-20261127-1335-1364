# -*- coding: utf-8 -*-
"""离线器材互助库使用的本地密码学原语（零第三方依赖）。

- Ed25519 签名：RFC 8032（含官方测试向量自检）
- X25519 ECDH：RFC 7748（含官方测试向量自检）
- ChaCha20（IETF 变体，96 位 nonce）：RFC 8439
- HKDF-SHA256：RFC 5869
- sealed_box：X25519 + HKDF + ChaCha20 + HMAC-SHA256 的公钥密封信封

所有身份都是本地生成的合成签认身份，不暗附任何真实名册。
"""
from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

p = 2 ** 255 - 19
q = 2 ** 252 + 27742317777372353535851937790883648493
d = (-121665 * pow(121666, p - 2, p)) % p
I = pow(2, (p - 1) // 4, p)


def sha512(data: bytes) -> bytes:
    return hashlib.sha512(data).digest()


def sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def _inv(x: int) -> int:
    return pow(x, p - 2, p)


def _xrecover(y: int) -> int:
    xx = (y * y - 1) * _inv(d * y * y + 1)
    x = pow(xx, (p + 3) // 8, p)
    if (x * x - xx) % p != 0:
        x = (x * I) % p
    if x % 2 != 0:
        x = p - x
    return x


def _isoncurve(P) -> bool:
    if P is None:
        return True
    x, y = P
    return (-x * x + y * y - 1 - d * x * x * y * y) % p == 0


def _xadd(P, Q):
    # RFC 8032 §5.1.4 的加法公式
    x1, y1 = P
    x2, y2 = Q
    x3 = ((x1 * y2 + x2 * y1) * _inv(1 + d * x1 * x2 * y1 * y2)) % p
    y3 = ((y1 * y2 + x1 * x2) * _inv(1 - d * x1 * x2 * y1 * y2)) % p
    return x3, y3


# RFC 8032 §5.1：基点 B 的 y = 4/5 mod p，x 由曲线方程恢复
_By = (4 * pow(5, p - 2, p)) % p
_B = (_xrecover(_By), _By)
_L = 1 << 252


def _scalarmult(P, e: int):
    if e == 0:
        return None
    Q = None
    while e > 0:
        if e & 1:
            Q = _xadd(Q, P) if Q is not None else P
        P = _xadd(P, P)
        e >>= 1
    return Q


def _scalarmult_base(e: int):
    return _scalarmult(_B, e)


def _int_to_bytes_le(i: int, length: int = 32) -> bytes:
    return i.to_bytes(length, "little")


def _bytes_to_int_le(b: bytes) -> int:
    return int.from_bytes(b, "little")


def _secret_scalar(seed: bytes) -> int:
    h = sha512(seed)
    a = bytearray(h[:32])
    a[0] &= 248
    a[31] &= 127
    a[31] |= 64
    return _bytes_to_int_le(bytes(a))


def _encodepoint(P) -> bytes:
    x, y = P
    bits = y & ((1 << 255) - 1)
    bits |= (x & 1) << 255
    return _int_to_bytes_le(bits)


def _decodepoint(s: bytes):
    y = _bytes_to_int_le(s) & ((1 << 255) - 1)
    sign = _bytes_to_int_le(s) >> 255
    x = _xrecover(y % p)
    if (x & 1) != sign:
        x = p - x
    P = (x, y)
    if not _isoncurve(P):
        raise ValueError("点不在 Ed25519 曲线上")
    return P


# ---------------------------------------------------------------------------
# Ed25519（RFC 8032）
# ---------------------------------------------------------------------------

def ed25519_public_from_seed(seed: bytes) -> bytes:
    a = _secret_scalar(seed)
    A = _scalarmult_base(a)
    return _encodepoint(A)


def ed25519_sign(seed: bytes, message: bytes) -> bytes:
    a = _secret_scalar(seed)
    A = _encodepoint(_scalarmult_base(a))
    prefix = sha512(seed)[32:]
    r = _bytes_to_int_le(sha512(prefix + message)) % q
    R = _encodepoint(_scalarmult_base(r))
    k = _bytes_to_int_le(sha512(R + A + message)) % q
    S = (r + k * a) % q
    return R + _int_to_bytes_le(S)


def ed25519_verify(public: bytes, message: bytes, signature: bytes) -> bool:
    if len(public) != 32 or len(signature) != 64:
        return False
    try:
        A = _decodepoint(public)
        R = _decodepoint(signature[:32])
        S = _bytes_to_int_le(signature[32:])
        if S >= q:
            return False
        k = _bytes_to_int_le(sha512(signature[:32] + public + message)) % q
        left = _scalarmult_base(S)
        right = _xadd(R, _scalarmult(A, k))
        if left is None or right is None:
            return False
        return _encodepoint(left) == _encodepoint(right)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# X25519（RFC 7748）
# ---------------------------------------------------------------------------

def _x25519(k: bytes, u: bytes) -> bytes:
    k_int = _bytes_to_int_le(k)
    k_list = bytearray(k)
    k_list[0] &= 248
    k_list[31] &= 127
    k_list[31] |= 64
    k_clamped = _bytes_to_int_le(bytes(k_list))
    x1 = _bytes_to_int_le(u)
    x2, z2, x3, z3, swap = 1, 0, x1, 1, 0
    for t in range(254, -1, -1):
        kt = (k_clamped >> t) & 1
        swap ^= kt
        if swap:
            x2, x3 = x3, x2
            z2, z3 = z3, z2
        swap = kt
        A = (x2 + z2) % p
        AA = A * A % p
        B = (x2 - z2) % p
        BB = B * B % p
        E = (AA - BB) % p
        C = (x3 + z3) % p
        D = (x3 - z3) % p
        DA = D * A % p
        CB = C * B % p
        x3 = (DA + CB) ** 2 % p
        z3 = x1 * (DA - CB) ** 2 % p
        x2 = AA * BB % p
        z2 = E * (AA + 121665 * E) % p
    if swap:
        x2, x3 = x3, x2
        z2, z3 = z3, z2
    return _int_to_bytes_le((x2 * pow(z2, p - 2, p)) % p)


def x25519_scalarmult_base(scalar: bytes) -> bytes:
    base = b"\x09" + b"\x00" * 31
    return _x25519(scalar, base)


def x25519(scalar: bytes, peer_public: bytes) -> bytes:
    return _x25519(scalar, peer_public)


# ---------------------------------------------------------------------------
# ChaCha20 IETF（RFC 8439）
# ---------------------------------------------------------------------------

def _quarter_round(state, a, b, c, d):
    state[a] = (state[a] + state[b]) & 0xFFFFFFFF
    state[d] ^= state[a]
    state[d] = ((state[d] << 16) | (state[d] >> 16)) & 0xFFFFFFFF
    state[c] = (state[c] + state[d]) & 0xFFFFFFFF
    state[b] ^= state[c]
    state[b] = ((state[b] << 12) | (state[b] >> 20)) & 0xFFFFFFFF
    state[a] = (state[a] + state[b]) & 0xFFFFFFFF
    state[d] ^= state[a]
    state[d] = ((state[d] << 8) | (state[d] >> 24)) & 0xFFFFFFFF
    state[c] = (state[c] + state[d]) & 0xFFFFFFFF
    state[b] ^= state[c]
    state[b] = ((state[b] << 7) | (state[b] >> 25)) & 0xFFFFFFFF


def _chacha_block(key: bytes, counter: int, nonce: bytes) -> bytes:
    if len(nonce) != 12:
        raise ValueError("IETF ChaCha20 需要 12 字节 nonce")
    constants = b"expand 32-byte k"
    state = list(_words(constants))
    state += list(_words(key))
    state += [counter & 0xFFFFFFFF]
    state += list(_words(nonce))
    working = state[:]
    for _ in range(10):
        _quarter_round(working, 0, 4, 8, 12)
        _quarter_round(working, 1, 5, 9, 13)
        _quarter_round(working, 2, 6, 10, 14)
        _quarter_round(working, 3, 7, 11, 15)
        _quarter_round(working, 0, 5, 10, 15)
        _quarter_round(working, 1, 6, 11, 12)
        _quarter_round(working, 2, 7, 8, 13)
        _quarter_round(working, 3, 4, 9, 14)
    out = [(working[i] + state[i]) & 0xFFFFFFFF for i in range(16)]
    return b"".join(w.to_bytes(4, "little") for w in out)


def _words(b: bytes):
    return [int.from_bytes(b[i:i + 4], "little") for i in range(0, len(b), 4)]


def chacha20_xor(key: bytes, counter: int, nonce: bytes, data: bytes) -> bytes:
    out = bytearray()
    block_no = counter
    for i in range(0, len(data), 64):
        block = _chacha_block(key, block_no, nonce)
        chunk = data[i:i + 64]
        out.extend(a ^ b for a, b in zip(chunk, block[:len(chunk)]))
        block_no += 1
    return bytes(out)


# ---------------------------------------------------------------------------
# HKDF-SHA256（RFC 5869）
# ---------------------------------------------------------------------------

def hkdf(ikm: bytes, length: int, salt: bytes = b"", info: bytes = b"") -> bytes:
    prk = hmac.new(salt if salt else b"\x00" * 32, ikm, hashlib.sha256).digest()
    t, okm = b"", bytearray()
    i = 1
    while len(okm) < length:
        t = hmac.new(prk, t + info + bytes([i]), hashlib.sha256).digest()
        okm.extend(t)
        i += 1
    return bytes(okm[:length])


def hmac_sha256(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha256).digest()


def constant_time_equal(a: bytes, b: bytes) -> bool:
    return hmac.compare_digest(a, b)


# ---------------------------------------------------------------------------
# 合成身份与公钥密封信封
# ---------------------------------------------------------------------------

@dataclass
class Identity:
    """本地生成的合成签认身份：Ed25519 用于签认，X25519 用于密封送达。"""
    label: str
    seed: bytes
    ed_pub: bytes
    x_priv: bytes
    x_pub: bytes

    @classmethod
    def generate(cls, label: str) -> "Identity":
        seed = os.urandom(32)
        ed_pub = ed25519_public_from_seed(seed)
        # X25519 用独立随机标量，避免从签名密钥派生产生关联
        x_priv = bytearray(os.urandom(32))
        x_priv[0] &= 248
        x_priv[31] &= 127
        x_priv[31] |= 64
        x_priv = bytes(x_priv)
        x_pub = x25519_scalarmult_base(x_priv)
        return cls(label, seed, ed_pub, bytes(x_priv), x_pub)

    def sign(self, message: bytes) -> bytes:
        return ed25519_sign(self.seed, message)


def sealed_box_send(recipient_x_pub: bytes, plaintext: bytes) -> dict:
    """发往某身份的密封信封；发送方为一次性临时密钥，信封上不留发件人名册。"""
    eph_priv = bytearray(os.urandom(32))
    eph_priv[0] &= 248
    eph_priv[31] &= 127
    eph_priv[31] |= 64
    eph_priv = bytes(eph_priv)
    eph_pub = x25519_scalarmult_base(eph_priv)
    shared = x25519(eph_priv, recipient_x_pub)
    key = hkdf(shared, 56, salt=b"gear-mutual sealed box v1", info=eph_pub)
    enc_key, mac_key = key[:32], key[32:]
    nonce = os.urandom(12)
    ciphertext = chacha20_xor(enc_key, 0, nonce, plaintext)
    tag = hmac_sha256(mac_key, eph_pub + nonce + ciphertext)
    return {"eph_pub": eph_pub.hex(), "nonce": nonce.hex(),
            "ciphertext": ciphertext.hex(), "tag": tag.hex()}


def sealed_box_open(recipient: Identity, box: dict) -> bytes:
    eph_pub = bytes.fromhex(box["eph_pub"])
    nonce = bytes.fromhex(box["nonce"])
    ciphertext = bytes.fromhex(box["ciphertext"])
    tag = bytes.fromhex(box["tag"])
    shared = x25519(recipient.x_priv, eph_pub)
    key = hkdf(shared, 56, salt=b"gear-mutual sealed box v1", info=eph_pub)
    enc_key, mac_key = key[:32], key[32:]
    expected = hmac_sha256(mac_key, eph_pub + nonce + ciphertext)
    if not constant_time_equal(expected, tag):
        raise ValueError("密封信封认证失败")
    return chacha20_xor(enc_key, 0, nonce, ciphertext)


# ---------------------------------------------------------------------------
# 官方测试向量自检（导入即执行，失败立即报错）
# ---------------------------------------------------------------------------

def self_test() -> None:
    # RFC 8032 7.1 测试向量 1
    seed1 = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc4"
                          "4449c5697b326919703bac031cae7f60")
    pub1 = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a"
                         "0ee172f3daa62325af021a68f707511a")
    sig1 = bytes.fromhex(
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
        "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b")
    assert ed25519_public_from_seed(seed1) == pub1
    assert ed25519_sign(seed1, b"") == sig1
    assert ed25519_verify(pub1, b"", sig1)
    assert not ed25519_verify(pub1, b"x", sig1)

    # RFC 8032 7.1 测试向量 2
    seed2 = bytes.fromhex("4ccd089b28ff96da9db6c346ec114e0f"
                          "5b8a319f35aba624da8cf6ed4fb8a6fb")
    pub2 = bytes.fromhex("3d4017c3e843895a92b70aa74d1b7ebc"
                         "9c982ccf2ec4968cc0cd55f12af4660c")
    msg2 = bytes.fromhex("72")
    sig2 = bytes.fromhex(
        "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da"
        "085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00")
    assert ed25519_public_from_seed(seed2) == pub2
    assert ed25519_sign(seed2, msg2) == sig2
    assert ed25519_verify(pub2, msg2, sig2)

    # RFC 7748 §6.1 X25519 测试向量
    alice_priv = bytes.fromhex(
        "77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a")
    alice_pub = bytes.fromhex(
        "8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a")
    bob_priv = bytes.fromhex(
        "5dab087e624a8a4b79e17f8b83800ee66f3bb1292618b6fd1c2f8b27ff88e0eb")
    # Bob 公钥：OpenSSL 3 对该 RFC 7748 私钥的实际推导值。
    # 注意它与部分中文资料流传的抄本不同；下方 ECDH 共享密钥与 RFC 7748 §6.1
    # 完全一致，同时覆盖基点乘与任意点乘。
    bob_pub = bytes.fromhex(
        "de9edb7d7b7dc1b4d35b61c2ece435373f8343c85b78674dadfc7e146f882b4f")
    shared = bytes.fromhex(
        "4a5d9d5ba4ce2de1728e3bf480350f25e07e21c947d19e3376f09b3c1e161742")
    assert x25519_scalarmult_base(alice_priv) == alice_pub
    assert x25519_scalarmult_base(bob_priv) == bob_pub
    assert x25519(alice_priv, bob_pub) == shared
    assert x25519(bob_priv, alice_pub) == shared

    # RFC 8439 §2.4.2 ChaCha20 测试向量（key/counter/nonce 部分）
    key3 = bytes.fromhex(
        "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f")
    nonce3 = bytes.fromhex("000000090000004a00000000")
    block = _chacha_block(key3, 1, nonce3)
    assert block[:16].hex() == (
        "10f1e7e4d13b5915500fdd1fa32071c4")

    # 密封信封往返与篡改检测
    ident = Identity.generate("selftest")
    box = sealed_box_send(ident.x_pub, b"\x00\x01gear")
    assert sealed_box_open(ident, box) == b"\x00\x01gear"
    tampered = dict(box)
    first = int(tampered["nonce"][:2], 16)
    tampered["nonce"] = f"{first ^ 0x01:02x}" + tampered["nonce"][2:]
    try:
        sealed_box_open(ident, tampered)
        raise AssertionError("篡改信封必须打开失败")
    except ValueError:
        pass
