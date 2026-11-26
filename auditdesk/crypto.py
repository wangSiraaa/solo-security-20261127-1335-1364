"""自造钥匙 + 成熟库签名。

钥匙对由本项目自行生成、自行登记公钥（无 PKI、无第三方根），
签名/验签一律委托系统成熟实现 OpenSSL 3（Ed25519, RFC 8032）。
哈希用标准库 hashlib SHA-256。
"""
from __future__ import annotations

import hashlib
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

OPENSSL = "openssl"


class CryptoError(RuntimeError):
    pass


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_short(data: bytes) -> str:
    return sha256_hex(data)[:12]


@dataclass
class KeyPair:
    key_id: str
    priv_pem: bytes
    pub_pem: bytes


def _run(args: list[str], stdin: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, input=stdin, capture_output=True, check=False
    )


def generate_keypair() -> KeyPair:
    """用 OpenSSL 生成 Ed25519 私钥，再导出公钥；key_id = 公钥 DER 的哈希。"""
    gen = _run([OPENSSL, "genpkey", "-algorithm", "Ed25519"])
    if gen.returncode != 0:
        raise CryptoError(f"genpkey failed: {gen.stderr.decode(errors='replace')}")
    priv_pem = gen.stdout
    pub = _run([OPENSSL, "pkey", "-pubout"], stdin=priv_pem)
    if pub.returncode != 0:
        raise CryptoError(f"pubout failed: {pub.stderr.decode(errors='replace')}")
    pub_pem = pub.stdout
    der = _run([OPENSSL, "pkey", "-pubin", "-outform", "DER"], stdin=pub_pem)
    if der.returncode != 0:
        raise CryptoError(f"DER failed: {der.stderr.decode(errors='replace')}")
    key_id = "K" + sha256_short(der.stdout)
    return KeyPair(key_id, priv_pem, pub_pem)


def key_id_of(pub_pem: bytes) -> str:
    der = _run([OPENSSL, "pkey", "-pubin", "-outform", "DER"], stdin=pub_pem)
    if der.returncode != 0:
        raise CryptoError(f"DER failed: {der.stderr.decode(errors='replace')}")
    return "K" + sha256_short(der.stdout)


def sign(priv_pem: bytes, message: bytes) -> bytes:
    """Ed25519 对 message 直接签名（-rawin，消息参与内部预哈希）。"""
    with tempfile.TemporaryDirectory() as td:
        k = Path(td) / "k.pem"
        m = Path(td) / "m.bin"
        s = Path(td) / "sig.bin"
        k.write_bytes(priv_pem)
        m.write_bytes(message)
        r = _run([OPENSSL, "pkeyutl", "-sign", "-inkey", str(k),
                  "-rawin", "-in", str(m), "-out", str(s)])
        if r.returncode != 0:
            raise CryptoError(f"sign failed: {r.stderr.decode(errors='replace')}")
        return s.read_bytes()


def verify(pub_pem: bytes, message: bytes, signature: bytes) -> bool:
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "pub.pem"
        m = Path(td) / "m.bin"
        s = Path(td) / "sig.bin"
        p.write_bytes(pub_pem)
        m.write_bytes(message)
        s.write_bytes(signature)
        r = _run([OPENSSL, "pkeyutl", "-verify", "-pubin", "-inkey", str(p),
                  "-rawin", "-in", str(m), "-sigfile", str(s)])
        return r.returncode == 0
