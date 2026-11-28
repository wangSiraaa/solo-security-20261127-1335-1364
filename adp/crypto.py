"""签名适配层。

本环境没有 PyPI（cryptography/PyNaCl 均不可用），因此通过 OpenSSL 3.0
命令行使用 Ed25519。接口刻意保持极小，若环境允许，可把本文件整体替换
为 ``cryptography.hazmat.primitives.asymmetric.ed25519`` 的实现，
其余模块不受影响。

  Signer:  编纂端持有的签发键（私钥）
  Verifier: 查词席/排字工坊持有的验签键（公钥）
"""
from __future__ import annotations

import os
import subprocess
import tempfile


class SigningError(Exception):
    pass


def _run(args: list[str], stdin: bytes | None = None) -> bytes:
    proc = subprocess.run(
        args, input=stdin, capture_output=True,
        timeout=30, check=False,
    )
    if proc.returncode != 0:
        raise SigningError(
            f"openssl 调用失败: {' '.join(args)}\n{proc.stderr.decode(errors='replace')}"
        )
    return proc.stdout


class Signer:
    def __init__(self, private_key_pem: bytes, key_id: str):
        self.private_key_pem = private_key_pem
        self.key_id = key_id

    @staticmethod
    def generate(key_id: str) -> "Signer":
        pem = _run(["openssl", "genpkey", "-algorithm", "ED25519"])
        return Signer(pem, key_id)

    def public_key_pem(self) -> bytes:
        return _run(
            ["openssl", "pkey", "-pubout"],
            stdin=self.private_key_pem,
        )

    def sign(self, message: bytes) -> bytes:
        """Ed25519 对 message 签名（64 字节）。"""
        with tempfile.TemporaryDirectory() as td:
            key_path = os.path.join(td, "sk.pem")
            msg_path = os.path.join(td, "msg")
            sig_path = os.path.join(td, "sig")
            with open(key_path, "wb") as f:
                f.write(self.private_key_pem)
            with open(msg_path, "wb") as f:
                f.write(message)
            _run([
                "openssl", "pkeyutl", "-sign",
                "-inkey", key_path, "-rawin",
                "-in", msg_path, "-out", sig_path,
            ])
            with open(sig_path, "rb") as f:
                return f.read()


class Verifier:
    def __init__(self, public_key_pem: bytes, key_id: str):
        self.public_key_pem = public_key_pem
        self.key_id = key_id

    def verify(self, message: bytes, signature: bytes) -> bool:
        with tempfile.TemporaryDirectory() as td:
            key_path = os.path.join(td, "vk.pem")
            msg_path = os.path.join(td, "msg")
            sig_path = os.path.join(td, "sig")
            with open(key_path, "wb") as f:
                f.write(self.public_key_pem)
            with open(msg_path, "wb") as f:
                f.write(message)
            with open(sig_path, "wb") as f:
                f.write(signature)
            proc = subprocess.run([
                "openssl", "pkeyutl", "-verify",
                "-pubin", "-inkey", key_path, "-rawin",
                "-in", msg_path, "-sigfile", sig_path,
            ], capture_output=True, timeout=30)
            return proc.returncode == 0
