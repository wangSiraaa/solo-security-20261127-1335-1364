"""数据模型与规范序列化（canonical JSON）。

所有待签名、待摘要的字节都走 dumps_canonical，保证跨进程字节一致。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from . import crypto


def dumps_canonical(obj: Any) -> bytes:
    return json.dumps(
        obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


@dataclass
class Entry:
    """柜台提交的一条具体日志条目。"""
    entry_id: str
    shift: str
    author: str
    payload: str
    ts: str

    def digest(self) -> str:
        return crypto.sha256_hex(dumps_canonical(asdict(self)))


@dataclass
class Page:
    """一个班次一页；页内条目先折叠成 root，再与前页摘要连成链。"""
    page_no: int
    shift: str
    prev_digest: str          # 前一页 page_digest；创世页为 GENESIS
    entry_digests: list[str]
    entries_root: str = ""
    page_digest: str = ""
    ts: str = ""

    def seal(self, ts: str) -> "Page":
        self.ts = ts
        self.entries_root = crypto.sha256_hex(
            dumps_canonical(self.entry_digests)
        )
        body = {
            "page_no": self.page_no,
            "shift": self.shift,
            "prev_digest": self.prev_digest,
            "entries_root": self.entries_root,
            "ts": self.ts,
        }
        self.page_digest = crypto.sha256_hex(dumps_canonical(body))
        return self

    def body_for_verify(self) -> dict:
        return {
            "page_no": self.page_no,
            "shift": self.shift,
            "prev_digest": self.prev_digest,
            "entries_root": self.entries_root,
            "ts": self.ts,
        }


# 见证站对某一页出具的本地收件凭证类型
RECEIPT_VERIFIED = "VERIFIED"   # 已验页：链接关系与条目根均核对通过
RECEIPT_DELIVERED = "DELIVERED"  # 包裹已送达、尚未验页（不承担页内容属实）


@dataclass
class Receipt:
    receipt_id: str
    station_id: str
    key_id: str
    page_digest: str | None       # DELIVERED 时可为空（包裹未拆，未见页摘要）
    page_no: int | None
    kind: str                     # VERIFIED / DELIVERED
    ts: str
    note: str = ""
    signature: str = ""           # hex

    def signed_message(self) -> bytes:
        body = {
            "receipt_id": self.receipt_id,
            "station_id": self.station_id,
            "key_id": self.key_id,
            "page_digest": self.page_digest,
            "page_no": self.page_no,
            "kind": self.kind,
            "ts": self.ts,
            "note": self.note,
        }
        return dumps_canonical(body)


@dataclass
class Bundle:
    """可离线搬运的一包材料：页、条目、签收、公钥。"""
    pages: list[dict] = field(default_factory=list)
    entries: list[dict] = field(default_factory=list)
    receipts: list[dict] = field(default_factory=list)
    pubkeys: dict[str, str] = field(default_factory=dict)  # key_id -> PEM

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2,
                          sort_keys=True)

    @staticmethod
    def from_json(text: str) -> "Bundle":
        d = json.loads(text)
        return Bundle(**d)
