"""Canonical hashing helpers for short duty-terminal records."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_bytes(obj: Any) -> bytes:
    """Return deterministic canonical JSON bytes.

    Sorting keys and removing whitespace makes receipts and dependency pins
    independent of local formatting.
    """
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def digest_json(obj: Any) -> str:
    return sha256_bytes(canonical_bytes(obj))


def short_id(kind: str, generation: int, digest: str) -> str:
    bare = digest.split(":", 1)[1] if digest.startswith("sha256:") else digest
    return f"{kind}-g{generation}-{bare[:12]}"


def canonical_body(text: str) -> str:
    """Canonicalize a small CSV/text table.

    Tables are line oriented.  Trailing blank lines are insignificant, while a
    single terminating newline is always used for hashing.
    """
    lines = text.splitlines()
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + ("\n" if lines else "")
