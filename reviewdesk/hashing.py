from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

HASH_CHUNK = 1024 * 1024


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(HASH_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_hash(value: Any) -> str:
    return sha256_bytes(canonical_json(value))


def hash_files(root: str | Path, patterns: Iterable[str]) -> list[dict[str, str]]:
    root = Path(root)
    results: list[dict[str, str]] = []
    for pattern in patterns:
        for path in sorted(root.glob(pattern)):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            results.append({"path": path.relative_to(root).as_posix(), "sha256": sha256_file(path)})
    # glob patterns can overlap; keep the first stable record.
    deduped: dict[str, dict[str, str]] = {}
    for item in results:
        deduped.setdefault(item["path"], item)
    return [deduped[key] for key in sorted(deduped)]


def hash_python_tree(root: str | Path, package: str = "mojiao") -> list[dict[str, str]]:
    return hash_files(root, [f"{package}/**/*.py", f"{package}/*.py"])
