"""规范化序列化与封印工具：全部哈希/签章都基于同一套规范编码，保证可重放。"""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any


def canon(obj: Any) -> str:
    """规范 JSON：键排序、无空白、非 ASCII 原样保留。"""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def digest(obj: Any) -> str:
    return sha256_text(canon(obj))


def station_seal(key: str, payload: Any) -> str:
    """受托站对证据/许可的封印（HMAC-SHA256），离线库房可凭同一把站钥验封。"""
    return hmac.new(key.encode("utf-8"), canon(payload).encode("utf-8"),
                    hashlib.sha256).hexdigest()


def verify_seal(key: str, payload: Any, seal: str) -> bool:
    return hmac.compare_digest(station_seal(key, payload), seal)
