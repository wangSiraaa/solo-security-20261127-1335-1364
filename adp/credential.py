"""版本凭据（credential）。

每次修订后，编纂端对新版本的树根签发一张凭据。凭据与根哈希绑定，
客户端只信任自己保存的验签键，因此服务器无法为任何非编纂端生成的
树状态提供有效凭据——也无法自报总数（条目数同样在签名之内，只可
用于一致性核对，分页终止绝不依赖它）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import canonical as C
from .crypto import Signer, Verifier

# 空树根哈希约定：32 字节零（与 tree.verify_proof_structure 对应）
EMPTY_ROOT_HEX = "00" * 32


def canonical_credential_payload(version: int, root_hash_hex: str,
                                  entry_count: int, created_at: str,
                                  key_id: str) -> bytes:
    """凭据中被签名的确定性字节串。"""
    return b"".join([
        C.enc_int(version),
        C.enc_bytes(C.from_hex(root_hash_hex)),
        C.enc_int(entry_count),
        C.enc_str(created_at),
        C.enc_str(key_id),
    ])


@dataclass
class Credential:
    version: int
    root_hash_hex: str
    entry_count: int
    created_at: str
    key_id: str
    signature_hex: str

    def payload(self) -> bytes:
        return canonical_credential_payload(
            self.version, self.root_hash_hex, self.entry_count,
            self.created_at, self.key_id,
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "root_hash_hex": self.root_hash_hex,
            "entry_count": self.entry_count,
            "created_at": self.created_at,
            "key_id": self.key_id,
            "signature_hex": self.signature_hex,
        }

    @staticmethod
    def from_json(d: dict[str, Any]) -> "Credential":
        return Credential(
            version=int(d["version"]),
            root_hash_hex=d["root_hash_hex"],
            entry_count=int(d["entry_count"]),
            created_at=d["created_at"],
            key_id=d["key_id"],
            signature_hex=d["signature_hex"],
        )

    def verify_with(self, verifier: Verifier) -> None:
        """验签并核对 key_id；失败抛异常。"""
        if verifier.key_id != self.key_id:
            raise PermissionError(
                f"凭据由 key_id={self.key_id} 签发，本地信任的是 {verifier.key_id}"
            )
        ok = verifier.verify(
            self.payload(), C.from_hex(self.signature_hex),
        )
        if not ok:
            raise PermissionError("版本凭据签名无效（服务器可能伪造了版本）")


def issue_credential(signer: Signer, version: int, root_hash_hex: str,
                     entry_count: int, created_at: str) -> Credential:
    payload = canonical_credential_payload(
        version, root_hash_hex, entry_count, created_at, signer.key_id,
    )
    sig = signer.sign(payload)
    return Credential(
        version=version, root_hash_hex=root_hash_hex,
        entry_count=entry_count, created_at=created_at,
        key_id=signer.key_id, signature_hex=C.to_hex(sig),
    )
