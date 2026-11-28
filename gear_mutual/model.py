# -*- coding: utf-8 -*-
"""规范化编码、合成物件、寄存行收据与成对器材订单的数据模型。

设计要点：
- 所有需要签名/哈希的文档都走 ``canonical``（键按字典序、紧凑分隔、无空白），
  消除键序差异导致的验签歧义。
- 物件只有合成编号（sid），不携带押物人真实名册；真实对应关系只存在于
  受托托管席的私有跨行台账中。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Optional

from crypto import Identity, ed25519_verify

BODY = "body"   # 镜身
RING = "ring"   # 转接环


def canonical(obj) -> bytes:
    """确定性 JSON：dict 键排序、无空白、默认 ensure_ascii=False。"""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def digest(obj) -> str:
    import hashlib
    return hashlib.sha256(canonical(obj)).hexdigest()


@dataclass
class SyntheticItem:
    """合成物件：可互换部件的最小物理单位，编号与真实名册解耦。"""
    sid: str                  # 合成编号，全网只认这个
    kind: str                 # BODY / RING
    mount: str                # 镜座类别，如 M1/M2/M3
    tolerance_um: float       # 装配公差（微米），越小越精密
    expires_on: str           # 外借期限（ISO 日期）
    bank: str                 # 当前保管的寄存行（合成代号）

    def public_descriptor(self) -> dict:
        # 客户可自行核验的可互换部件清单条目：不含押物人/合同
        return {
            "sid": self.sid,
            "kind": self.kind,
            "mount": self.mount,
            "tolerance_um": self.tolerance_um,
            "expires_on": self.expires_on,
        }


@dataclass
class BankReceipt:
    """寄存行对某合成物件的足额章收据（存在性 + 可用期证明）。

    多家寄存行可以为同一件物件盖章；托管席负责把重复盖章去重，
    公开数据里只出现 sid，不出现跨行物理对应名册。
    """
    sid: str
    bank: str
    kind: str
    mount: str
    tolerance_um: float
    expires_on: str
    bank_pub: str                 # 寄存行 Ed25519 公钥（hex）
    signature: Optional[str] = None

    def doc(self) -> dict:
        d = asdict(self)
        d.pop("signature", None)
        return d

    def sign(self, bank_id: Identity) -> "BankReceipt":
        assert bank_id.ed_pub.hex() == self.bank_pub
        self.signature = bank_id.sign(canonical(self.doc())).hex()
        return self


def verify_receipt(receipt: BankReceipt) -> bool:
    try:
        return ed25519_verify(bytes.fromhex(receipt.bank_pub),
                              canonical(receipt.doc()),
                              bytes.fromhex(receipt.signature or ""))
    except Exception:
        return False


@dataclass
class PairedOrder:
    """需要成对器材的普通单：一只镜身 + 一只匹配转接环。

    - ``allowed_mounts``：客户接受的替代镜座范围（含首选与替代）。
    - ``max_tolerance_um``：镜身装配公差上限。
    - 实际使用日在签发时无法提前知道，因此只记录承诺窗口，不写死使用日。
    """
    order_id: str
    client_pub: str               # 客户 X25519 密封公钥（hex）
    client_ed_pub: str            # 客户签认公钥（hex），用于取件签认
    preferred_mount: str
    allowed_mounts: list          # 允许的替代镜座
    max_tolerance_um: float
    need_before: str              # 承诺窗口截止日
    qty_assemblies: int = 1       # 普通单固定为 1 套成对器材


@dataclass
class AttestationBundle:
    """寄存行提交给托管席的一批足额章（公开侧只承诺存在性与可用期）。"""
    bank: str
    bank_pub: str
    receipts: list = field(default_factory=list)   # list[BankReceipt]
    bundle_id: str = ""

    def to_doc(self) -> dict:
        return {
            "bank": self.bank,
            "bank_pub": self.bank_pub,
            "bundle_id": self.bundle_id,
            "receipts": [asdict(r) for r in self.receipts],
        }
