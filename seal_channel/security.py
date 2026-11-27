"""摘要、HMAC 与规范化 JSON 工具。

这里用预置密钥/回执模拟真实验证器。威胁模型是发现登记或电文被改动，
而不是防御能够同时改写本机全部可信根文件的攻击者。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from typing import Any


def canonical_bytes(value: Any) -> bytes:
    """生成无歧义的规范化字节。"""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_hex(value: str | bytes | Any) -> str:
    if isinstance(value, str):
        raw = value.encode("utf-8")
    elif isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
    else:
        raw = canonical_bytes(value)
    return hashlib.sha256(raw).hexdigest()


def new_key_hex(num_bytes: int = 32) -> str:
    return secrets.token_hex(num_bytes)


def hmac_hex(key_hex: str, value: Any) -> str:
    key = bytes.fromhex(key_hex)
    if isinstance(value, str):
        msg = value.encode("utf-8")
    elif isinstance(value, (bytes, bytearray)):
        msg = bytes(value)
    else:
        msg = canonical_bytes(value)
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def same_digest(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return False
    return hmac.compare_digest(str(left), str(right))
