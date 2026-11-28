"""Offline digital signatures for the traveling-exhibition identity drill.

We deliberately do NOT re-implement any cryptography.  Ed25519 sign/verify is
delegated to the system OpenSSL CLI (a mature, audited implementation shipped
with the OS).  No network access and no real identity service are involved:
every key is a local "drill" key generated on this machine.

Canonical serialization is the only custom piece: signed bytes are the UTF-8
encoding of JSON with sorted keys and no insignificant whitespace.  Both
parties derive exactly the same bytes from the same object, so signatures are
reproducible and archives can be re-checked offline years later.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from typing import Any

# ---------------------------------------------------------------------------
# canonical bytes


def canonical_json(obj: Any) -> bytes:
    """Deterministic UTF-8 JSON: sorted keys, tight separators, unicode kept."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


# ---------------------------------------------------------------------------
# Ed25519 through the OpenSSL CLI


def _openssl() -> str:
    exe = shutil.which("openssl")
    if not exe:
        raise RuntimeError("OpenSSL CLI not found; it provides Ed25519 support")
    return exe


def _run(args: list[str], stdin: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, input=stdin, capture_output=True, check=False
    )


class Key:
    """A local Ed25519 drill key pair backed by an OpenSSL PEM file."""

    def __init__(self, private_pem: bytes):
        self._dir = tempfile.mkdtemp(prefix="museum-key-")
        self._priv = os.path.join(self._dir, "priv.pem")
        with open(self._priv, "wb") as fh:
            fh.write(private_pem)
        self._pub_der = self._derive_public()
        self._msg = os.path.join(self._dir, "msg.bin")
        self._sig = os.path.join(self._dir, "sig.bin")

    @classmethod
    def generate(cls) -> "Key":
        res = _run([_openssl(), "genpkey", "-algorithm", "ED25519"])
        if res.returncode != 0:
            raise RuntimeError(res.stderr.decode())
        return cls(res.stdout)

    def _derive_public(self) -> bytes:
        res = _run(
            [_openssl(), "pkey", "-in", self._priv, "-pubout", "-outform", "DER"]
        )
        if res.returncode != 0:
            raise RuntimeError(res.stderr.decode())
        return res.stdout

    @property
    def public_der(self) -> bytes:
        return self._pub_der

    @property
    def fp(self) -> str:
        return sha256_hex(self._pub_der)

    def sign(self, message: bytes) -> bytes:
        with open(self._msg, "wb") as fh:
            fh.write(message)
        res = _run(
            [
                _openssl(),
                "pkeyutl",
                "-sign",
                "-rawin",
                "-inkey",
                self._priv,
                "-in",
                self._msg,
                "-out",
                self._sig,
            ]
        )
        if res.returncode != 0:
            raise RuntimeError(res.stderr.decode())
        with open(self._sig, "rb") as fh:
            return fh.read()

    def __del__(self):  # best-effort cleanup
        try:
            shutil.rmtree(self._dir, ignore_errors=True)
        except Exception:
            pass


def verify(public_der: bytes, message: bytes, signature: bytes) -> bool:
    """Verify an Ed25519 signature with the OpenSSL CLI."""
    d = tempfile.mkdtemp(prefix="museum-verify-")
    try:
        pub = os.path.join(d, "pub.der")
        msg = os.path.join(d, "msg.bin")
        sig = os.path.join(d, "sig.bin")
        with open(pub, "wb") as fh:
            fh.write(public_der)
        with open(msg, "wb") as fh:
            fh.write(message)
        with open(sig, "wb") as fh:
            fh.write(signature)
        res = _run(
            [
                _openssl(),
                "pkeyutl",
                "-verify",
                "-rawin",
                "-pubin",
                "-keyform",
                "DER",
                "-inkey",
                pub,
                "-in",
                msg,
                "-sigfile",
                sig,
            ]
        )
        return res.returncode == 0
    finally:
        shutil.rmtree(d, ignore_errors=True)


# A tiny memoized verifier: the same (key, message, signature) is often checked
# several times while searching a referral graph.
_verify_cache: dict[tuple[str, str, str], bool] = {}


def verify_cached(public_der: bytes, message: bytes, signature: bytes) -> bool:
    cache_key = (
        sha256_hex(public_der),
        sha256_hex(message),
        sha256_hex(signature),
    )
    hit = _verify_cache.get(cache_key)
    if hit is None:
        hit = verify(public_der, message, signature)
        _verify_cache[cache_key] = hit
    return hit
