"""本地演练用的签名与摘要工具。

这里的 HMAC 只用于在同一台演练机上区分“本模拟器签发的消息”和被手工改坏
的消息；它不是真实 TPM/远程证明，也不提供对抗本机管理员的硬件级安全。
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any


FICTIONAL_ATTESTATION_CLASS = "FICTIONAL_LOCAL_DEVICE_DOUBLE"


def canonical_bytes(payload: dict[str, Any]) -> bytes:
    """返回签名前使用的规范 JSON 字节。"""

    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def payload_digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


@dataclass(frozen=True)
class SignedMessage:
    """带本地消息认证码的 JSON 信封。"""

    payload: dict[str, Any]
    mac: str
    authority: str

    def to_wire(self) -> dict[str, Any]:
        return {
            "authority": self.authority,
            "payload": self.payload,
            "mac": self.mac,
        }

    @classmethod
    def from_wire(cls, wire: dict[str, Any]) -> "SignedMessage":
        return cls(
            payload=dict(wire["payload"]),
            mac=str(wire["mac"]),
            authority=str(wire["authority"]),
        )


class LocalHmacAuthority:
    """演练用签发者；设备替身和借稿台各持一把本地密钥。"""

    def __init__(self, name: str, key: bytes) -> None:
        self.name = name
        self._key = key

    def sign(self, payload: dict[str, Any]) -> SignedMessage:
        body = dict(payload)
        body.setdefault("authority_name", self.name)
        mac = hmac.new(self._key, canonical_bytes(body), hashlib.sha256).hexdigest()
        return SignedMessage(payload=body, mac=mac, authority=self.name)

    def verify(self, message: SignedMessage) -> bool:
        expected = hmac.new(
            self._key, canonical_bytes(message.payload), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, message.mac)
