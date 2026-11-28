"""哈希承诺（commitment）原语。

承诺阶段只公布 commitment；揭密阶段公布 payload，任何人可验
payload 与当初的 commitment 是否一致。暗区支路在落片时只公开承诺，
对手看不到配对，揭片时再用同一原语核对。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical(obj: Any) -> bytes:
    """稳定序列化：键排序、无空白，保证同值同字节。"""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def digest(obj: Any) -> str:
    return hashlib.sha256(canonical(obj)).hexdigest()[:16]


def make_commitment(payload: Any) -> str:
    return digest(payload)


def open_commitment(commitment: str, payload: Any) -> bool:
    return digest(payload) == commitment


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
