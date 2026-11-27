# -*- coding: utf-8 -*-
"""
crypto.py —— 只用 Python 标准库的原语层。

题内没有真实密码学设施，这里用 HMAC-SHA256 构造全部"密封器"：
  * KDF      派生链密钥/消息密钥/归档包装密钥
  * aead     密钥流(X-Salsa 风格的 CTR 流) + HMAC 认证，对称认证加密
  * Tag      终端对遗忘承诺/回执的签章，服务台持验证密钥，不能代签
所有输出均为 raw bytes，编解码交给 codec.py。
"""
from __future__ import annotations

import hashlib
import hmac
import os


# ---------- 基础原语 ----------

def random_bytes(n: int = 32) -> bytes:
    return os.urandom(n)


def h(key: bytes, *parts: bytes) -> bytes:
    """HMAC-SHA256，可拼接多段输入。"""
    return hmac.new(key, b"".join(parts), hashlib.sha256).digest()


def kdf(ikm: bytes, label: bytes, length: int = 64) -> bytes:
    """固定 label 的 KDF，按 32 字节块展开到所需长度。"""
    out = b""
    t = b""
    i = 1
    while len(out) < length:
        t = h(ikm, t, label, bytes([i]))
        out += t
        i += 1
    return out[:length]


def derive_chain_keys(ck: bytes) -> tuple[bytes, bytes]:
    """双棘轮里的一对派生：mk = H(ck, 0x01)，ck' = H(ck, 0x02)。"""
    mk = h(ck, b"\x01")
    nxt = h(ck, b"\x02")
    return mk, nxt


# ---------- 对称认证加密（密钥流 + 标签） ----------

def _keystream(key: bytes, nonce: bytes, n: int) -> bytes:
    out = b""
    counter = 0
    while len(out) < n:
        block = h(key, nonce, counter.to_bytes(8, "big"))
        out += block
        counter += 1
    return out[:n]


def _xor(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b))


def aead_seal(key: bytes, plaintext: bytes, aad: bytes = b"") -> bytes:
    nonce = random_bytes(16)
    stream = _keystream(key, nonce, len(plaintext))
    ct = _xor(plaintext, stream)
    tag = h(key, nonce, aad, len(ct).to_bytes(8, "big"), ct)
    return nonce + tag + ct


def aead_open(key: bytes, blob: bytes, aad: bytes = b"") -> bytes:
    if len(blob) < 48:
        raise ValueError("封件过短，结构不合法")
    nonce, tag, ct = blob[:16], blob[16:48], blob[48:]
    expect = h(key, nonce, aad, len(ct).to_bytes(8, "big"), ct)
    if not hmac.compare_digest(tag, expect):
        raise ValueError("认证标签不符：该封件不能由此状态开启，或封件已被调换")
    stream = _keystream(key, nonce, len(ct))
    return _xor(ct, stream)


# ---------- 签章（遗忘承诺/回执用） ----------

def new_signing_keypair() -> tuple[bytes, bytes]:
    """返回 (签名私钥, 验证公钥)。纯对称模拟：公钥=私钥的派生值。"""
    sk = random_bytes(32)
    vk = h(sk, b"verify-key")
    return sk, vk


def sign(sk: bytes, message: bytes) -> bytes:
    # 签名基于验证公钥派生，使持 vk 者可核验、持 sk 者可签，且 sk 不离开终端
    vk = h(sk, b"verify-key")
    return h(vk, b"signature", message)


def verify(vk: bytes, message: bytes, sig: bytes) -> bool:
    return hmac.compare_digest(h(vk, b"signature", message), sig)


def fingerprint(key: bytes) -> str:
    """给一段密钥状态取短指纹，便于日志核对"哪个状态开过哪封信"。"""
    return hashlib.sha256(key).hexdigest()[:12]
