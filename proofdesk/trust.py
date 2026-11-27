from __future__ import annotations

import hmac
import secrets
from pathlib import Path
from typing import Any

from .common import canonical_json, read_json, write_json_atomic


class LocalTrust:
    """A small local key store.

    This is the simulation's trust anchor.  It is not a network PKI: the
    state directory stands in for a physically/local trusted machine, and
    HMAC signatures make accidental tampering visible in demonstrations.
    """

    def __init__(self, path: Path):
        self.path = path
        self._keys: dict[str, str] = read_json(path, default={}) or {}
        if not self._keys:
            write_json_atomic(path, self._keys)
        try:
            path.chmod(0o600)
        except OSError:
            pass

    def secret_for(self, key_id: str) -> bytes:
        if key_id not in self._keys:
            self._keys[key_id] = secrets.token_hex(32)
            write_json_atomic(self.path, self._keys)
            try:
                self.path.chmod(0o600)
            except OSError:
                pass
        return self._keys[key_id].encode("ascii")

    def sign(self, key_id: str, doc: dict[str, Any]) -> dict[str, Any]:
        out = dict(doc)
        out.pop("signature", None)
        out["signature_key"] = key_id
        secret = self.secret_for(key_id)
        out["signature"] = hmac.new(
            secret, canonical_json(out), "sha256"
        ).hexdigest()
        return out

    def verify(self, doc: dict[str, Any]) -> bool:
        if not isinstance(doc, dict) or "signature" not in doc:
            return False
        key_id = doc.get("signature_key")
        if not key_id or key_id not in self._keys:
            return False
        claimed = doc["signature"]
        # Underscore fields are local-only transport metadata, never part of
        # the signed document.
        body = {
            k: v
            for k, v in doc.items()
            if k != "signature" and not k.startswith("_")
        }
        expected = hmac.new(
            self.secret_for(key_id), canonical_json(body), "sha256"
        ).hexdigest()
        return hmac.compare_digest(claimed, expected)
