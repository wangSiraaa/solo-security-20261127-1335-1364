"""字节哈希与规范编码。

信任的根基只有一个：包的实际字节。名字、简介里出现什么字都不参与
信任判断，只有哈希被写入校验席的签发记录，之后再与磁盘上的字节重算
比对，才能回答“这份字节是不是当初核验过的那份”。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

SHA_ALGO = "sha256"


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def bytes_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(obj) -> bytes:
    """规范 JSON：键排序、无空白、非 ASCII 不转义，保证签名可复现。"""
    return json.dumps(
        obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
