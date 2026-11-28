"""封存件与哈希链。

每条业务记录（探头原读数、参考浴校正、失准公告、移架/交接/过秤事件）
封存为不可拆换的信封：信封含规范化记录体、前驱哈希与自身 SHA-256。
收件人可独立重算哈希链，并核对记录体里的来源绑定字段（样管/时段/柜体）。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

GENESIS = "GENESIS"


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(obj: Any) -> str:
    return hashlib.sha256(canonical(obj).encode("utf-8")).hexdigest()


def seal(record: dict, prev_hash: str) -> dict:
    body = {"record": record, "prev_hash": prev_hash}
    return {"body": body, "hash": digest(body)}


def verify_envelope(env: dict, prev_hash: str) -> tuple[bool, str]:
    if env["body"]["prev_hash"] != prev_hash:
        return False, "前驱哈希不符（链条断裂或被拆换）"
    if digest(env["body"]) != env["hash"]:
        return False, "自身哈希不符（封存件被改动）"
    return True, "ok"


class Chain:
    """一条追加式封存链；改名/抽换任一信封都会在 verify_chain 中暴露。"""

    def __init__(self, name: str):
        self.name = name
        self.envelopes: list[dict] = []
        self.tip = GENESIS

    def add(self, record: dict) -> dict:
        env = seal(record, self.tip)
        self.envelopes.append(env)
        self.tip = env["hash"]
        return env

    def verify_chain(self) -> tuple[bool, str, dict | None]:
        tip = GENESIS
        for env in self.envelopes:
            ok, msg = verify_envelope(env, tip)
            if not ok:
                return False, msg, env
            tip = env["hash"]
        return True, "ok", None

    def find(self, record_id: str) -> dict | None:
        for env in self.envelopes:
            if env["body"]["record"].get("id") == record_id:
                return env
        return None

    def export(self) -> list[dict]:
        return [dict(env) for env in self.envelopes]
