# -*- coding: utf-8 -*-
"""
codec.py —— 数据结构的序列化。

所有落盘/过网的东西都是"封件"(bytes) 或可 JSON 化的 dict；
私钥材料一律以封件形态存在，柜面目录里看不到明文状态。
"""
from __future__ import annotations

import base64
import json
from typing import Any


def b64e(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def b64d(txt: str) -> bytes:
    return base64.b64decode(txt.encode("ascii"))


def to_json(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def from_json(raw: bytes) -> Any:
    return json.loads(raw.decode("utf-8"))


def envelope_to_wire(env: dict) -> bytes:
    """来信封套（含密文，不含任何明文密钥）。"""
    return to_json(env)


def wire_to_envelope(raw: bytes) -> dict:
    return from_json(raw)
