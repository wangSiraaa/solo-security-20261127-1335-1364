"""校验席的签名材料。

本练习用 HMAC-SHA256 扮演“校验席的签名”：
- keyring.json 只保存在校验席自己的受信目录，选择器拿不到私钥；
- 选择器只拿到 keyring.pub.json（key_id 与核验公钥材料），用于验签；
签发记录里除签名外的任何字段（包括包名）都可以被人改字，验签会失败；
即使记录完好，选择器还会用记录里的哈希重算磁盘字节，字节被换同样不通过。
"""

from __future__ import annotations

import hmac
import json
import os
from dataclasses import dataclass
from pathlib import Path

from .digest import canonical


def _b64(data: bytes) -> str:
    import base64

    return base64.b64encode(data).decode("ascii")


def _unb64(text: str) -> bytes:
    import base64

    return base64.b64decode(text.encode("ascii"))


def sign(payload: dict, secret: bytes) -> str:
    return _b64(hmac.new(secret, canonical(payload), "sha256").digest())


def verify(payload: dict, mac: str, secret: bytes) -> bool:
    expected = sign(payload, secret)
    return hmac.compare_digest(expected, mac)


@dataclass(frozen=True)
class PublicKey:
    key_id: str
    secret_material: bytes  # HMAC 场景下两侧持有同一材料；公钥文件只放 key_id 名单


class Keyring:
    """校验席侧的钥匙环：key_id -> HMAC 材料。"""

    def __init__(self, keys: dict[str, bytes]):
        self._keys = dict(keys)

    @classmethod
    def generate(cls) -> "Keyring":
        return cls({"verifier-main-1": os.urandom(32)})

    @classmethod
    def load(cls, path: str | Path) -> "Keyring":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls({k: _unb64(v) for k, v in raw["keys"].items()})

    def save(self, path: str | Path) -> None:
        body = {
            "kind": "verifier-private-keyring",
            "keys": {k: _b64(v) for k, v in sorted(self._keys.items())},
        }
        Path(path).write_text(json.dumps(body, indent=2), encoding="utf-8")

    def export_public(self, path: str | Path) -> None:
        """只导出受信 key_id 名单与签发者名称，不含材料本身以外的新信息。

        HMAC 是对称原语，这里用“选择器侧配置了同一验签材料”来模拟公钥分发；
        导出文件明确标注用途，私钥环文件永不离开受信目录。
        """
        body = {
            "kind": "verifier-public-trust-anchors",
            "anchors": [
                {
                    "key_id": key_id,
                    "issued_by": "校验席（独立第三方）",
                    "material": _b64(self._keys[key_id]),
                }
                for key_id in sorted(self._keys)
            ],
        }
        Path(path).write_text(json.dumps(body, indent=2, ensure_ascii=False),
                              encoding="utf-8")

    @property
    def key_id(self) -> str:
        return next(iter(sorted(self._keys)))

    def secret(self, key_id: str) -> bytes:
        if key_id not in self._keys:
            raise KeyError(f"未知的签发钥匙：{key_id}")
        return self._keys[key_id]

    def trusted_ids(self) -> list[str]:
        return sorted(self._keys)


def load_public_anchors(path: str | Path) -> dict[str, bytes]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if raw.get("kind") != "verifier-public-trust-anchors":
        raise ValueError("信任锚文件类型不对，拒绝使用")
    return {a["key_id"]: _unb64(a["material"]) for a in raw["anchors"]}
