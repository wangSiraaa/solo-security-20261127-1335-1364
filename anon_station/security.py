"""Small HMAC token and private-file helpers (Python standard library only)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path
from typing import Any, Dict

from .clockutil import now
from .errors import TokenError


def b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def b64u_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def sha256_hex(data: str | bytes) -> str:
    raw = data.encode("utf-8") if isinstance(data, str) else data
    return hashlib.sha256(raw).hexdigest()


def load_or_create_key(path: Path) -> bytes:
    path = Path(path)
    if path.exists():
        return path.read_bytes().strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_hex(32).encode("ascii")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # Another process won the initialization race; read its durable key.
        return path.read_bytes().strip()
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(key)
        os.chmod(path, 0o600)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return key


def sign_token(payload: Dict[str, Any], key: bytes) -> str:
    header = {"alg": "HS256", "typ": "AST1"}
    header_part = b64u_encode(
        json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    payload_part = b64u_encode(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    signing_input = f"{header_part}.{payload_part}".encode("ascii")
    signature = hmac.new(key, signing_input, hashlib.sha256).digest()
    return f"{header_part}.{payload_part}.{b64u_encode(signature)}"


def verify_token(
    token: str | None,
    key: bytes,
    expected_type: str | None = None,
) -> Dict[str, Any]:
    if not token or not isinstance(token, str) or token.count(".") != 2:
        raise TokenError("token is missing or malformed")
    header_part, payload_part, signature_part = token.split(".")
    signing_input = f"{header_part}.{payload_part}".encode("ascii")
    try:
        supplied_signature = b64u_decode(signature_part)
    except Exception as exc:  # pragma: no cover - defensive
        raise TokenError("token signature is not valid base64") from exc
    expected_signature = hmac.new(key, signing_input, hashlib.sha256).digest()
    if not hmac.compare_digest(supplied_signature, expected_signature):
        raise TokenError("token signature does not match")
    try:
        payload = json.loads(b64u_decode(payload_part).decode("utf-8"))
    except Exception as exc:
        raise TokenError("token payload is malformed") from exc
    if expected_type and payload.get("typ") != expected_type:
        raise TokenError("unexpected token type", expected=expected_type)
    expires_at = payload.get("exp")
    if expires_at is not None and int(expires_at) < now():
        raise TokenError("token has expired", expired_at=expires_at)
    return payload


def write_private_json(path: str | Path, payload: Dict[str, Any]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.chmod(path, 0o600)
    return path
