# -*- coding: utf-8 -*-
"""受限分支的密封/启封（仅教学用途的对称认证加密）。

格式（base64 后）： nonce(16) || hmac_sha256(32) || ciphertext
加密为 SHA256 计数器密钥流异或；MAC 覆盖 nonce 与密文，防篡改。
生产环境应替换为平台 Keystore/TEE 与成熟 AEAD（如 AES-GCM）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os


class UnsealError(Exception):
    """密钥不符、密文被改或载荷不是合法分支时抛出。"""


def new_key() -> str:
    """生成一把终端密钥（base64-urlsafe 字符串）。"""
    return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")


def _keystream(key: bytes, nonce: bytes, size: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < size:
        block = hashlib.sha256(key + nonce + counter.to_bytes(8, "big")).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:size])


def _xor(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b))


def seal(payload: dict, key: str) -> bytes:
    """把分支字典密封成可交接的令牌字节。"""
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    key_bytes = base64.urlsafe_b64decode(key.encode("ascii"))
    nonce = os.urandom(16)
    ciphertext = _xor(raw, _keystream(key_bytes, nonce, len(raw)))
    mac = hmac.new(key_bytes, nonce + ciphertext, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(nonce + mac + ciphertext)


def unseal(token: bytes, key: str) -> dict:
    """启封；任何核验失败统一抛 UnsealError，不泄露具体原因。"""
    try:
        blob = base64.urlsafe_b64decode(token)
        nonce, mac, ciphertext = blob[:16], blob[16:48], blob[48:]
        key_bytes = base64.urlsafe_b64decode(key.encode("ascii"))
        expected = hmac.new(key_bytes, nonce + ciphertext, hashlib.sha256).digest()
        if not hmac.compare_digest(mac, expected):
            raise UnsealError("密封分支核验失败")
        raw = _xor(ciphertext, _keystream(key_bytes, nonce, len(ciphertext)))
        payload = json.loads(raw.decode("utf-8"))
    except UnsealError:
        raise
    except Exception as exc:  # 解码/填充/JSON 等任何异常归一化
        raise UnsealError("密封分支无法启封") from exc
    if not isinstance(payload, dict) or payload.get("kind") != "branch":
        raise UnsealError("载荷不是合法分支")
    return payload
