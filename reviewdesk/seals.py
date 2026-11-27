from __future__ import annotations

import base64
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .hashing import canonical_json, sha256_file

ROOT = Path(__file__).resolve().parents[1]
KEY_DIR = ROOT / "review-desk"
PRIVATE_KEY = KEY_DIR / "publisher-ed25519.key.pem"
PUBLIC_KEY = KEY_DIR / "publisher-ed25519.pub.pem"
KEY_ID = "fictional-publisher-ed25519-2026Q4"


def ensure_keypair() -> None:
    KEY_DIR.mkdir(parents=True, exist_ok=True)
    if PRIVATE_KEY.exists() and PUBLIC_KEY.exists():
        return
    if shutil.which("openssl") is None:
        raise RuntimeError("openssl is required to create the fictional publisher seal")
    subprocess.run(
        ["openssl", "genpkey", "-algorithm", "ED25519", "-out", str(PRIVATE_KEY)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    PRIVATE_KEY.chmod(0o600)
    subprocess.run(
        ["openssl", "pkey", "-in", str(PRIVATE_KEY), "-pubout", "-out", str(PUBLIC_KEY)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def sign_bytes(data: bytes) -> str:
    ensure_keypair()
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        payload_path = Path(tmp) / "payload"
        payload_path.write_bytes(data)
        proc = subprocess.run(
            [
                "openssl", "pkeyutl", "-sign", "-inkey", str(PRIVATE_KEY),
                "-rawin", "-in", str(payload_path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        signature = proc.stdout
    return base64.b64encode(signature).decode("ascii")


def verify_bytes(data: bytes, signature_b64: str, public_key: str | Path = PUBLIC_KEY) -> bool:
    if shutil.which("openssl") is None:
        raise RuntimeError("openssl is required to verify the publisher seal")
    signature = base64.b64decode(signature_b64)
    # OpenSSL needs separately seekable payload and signature files.
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        payload_path = Path(tmp) / "payload"
        sig_path = Path(tmp) / "signature"
        payload_path.write_bytes(data)
        sig_path.write_bytes(signature)
        proc = subprocess.run(
            [
                "openssl", "pkeyutl", "-verify", "-pubin", "-inkey", str(public_key),
                "-rawin", "-in", str(payload_path), "-sigfile", str(sig_path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    return proc.returncode == 0


def seal_payload(payload: dict[str, Any]) -> dict[str, Any]:
    sealed = dict(payload)
    sealed["key_id"] = KEY_ID
    sealed["signature_algorithm"] = "Ed25519"
    sealed["signature"] = sign_bytes(canonical_json(sealed))
    return sealed


def verify_sealed_payload(sealed: dict[str, Any], public_key: str | Path = PUBLIC_KEY) -> dict[str, Any]:
    signature = sealed.get("signature")
    if not signature:
        return {"ok": False, "reason": "missing signature"}
    unsigned = {k: v for k, v in sealed.items() if k != "signature"}
    try:
        ok = verify_bytes(canonical_json(unsigned), signature, public_key)
    except Exception as exc:  # pragma: no cover - environment dependent
        return {"ok": False, "reason": str(exc)}
    return {"ok": ok, "key_id": sealed.get("key_id")}


def public_key_sha256() -> str:
    ensure_keypair()
    return sha256_file(PUBLIC_KEY)
