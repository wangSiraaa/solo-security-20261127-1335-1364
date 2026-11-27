"""Content hashing, canonical JSON, and page-range helpers.

The simulation deliberately uses only the Python standard library so that a
third party can re-run verification without installing forensic software.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Iterable, List, Sequence


GENESIS_HASH = "0" * 64


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def canonical_json(value: Any) -> bytes:
    """Return deterministic JSON bytes used for receipt hashes."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def object_hash(value: Any) -> str:
    return sha256_bytes(canonical_json(value))


def short_hash(digest: str, length: int = 12) -> str:
    return digest[:length]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


_PAGE_RANGE_RE = re.compile(r"^([A-Za-z]*)(\d+)(?:-([A-Za-z]*)(\d+))?$")


def expand_page_token(token: str) -> List[str]:
    """Expand ``P001-P003`` to ``['P001', 'P002', 'P003']``.

    Prefix and zero padding are preserved from the first page.  A range may
    also be a single page.
    """
    token = token.strip()
    match = _PAGE_RANGE_RE.match(token)
    if not match:
        raise ValueError(f"invalid page/range token: {token!r}")

    prefix1, num1_s, prefix2, num2_s = match.groups()
    if num2_s is None:
        return [f"{prefix1}{int(num1_s):0{len(num1_s)}d}"]

    prefix2 = prefix2 or prefix1
    if prefix2 != prefix1:
        raise ValueError(f"page range prefix changes: {token!r}")
    start = int(num1_s)
    end = int(num2_s)
    if end < start:
        raise ValueError(f"page range descends: {token!r}")
    width = len(num1_s)
    return [f"{prefix1}{n:0{width}d}" for n in range(start, end + 1)]


def expand_page_ranges(tokens: Iterable[str] | None) -> List[str]:
    if tokens is None:
        return []
    pages: List[str] = []
    for token in tokens:
        pages.extend(expand_page_token(token))
    # Preserve order while removing duplicates.
    seen = set()
    result = []
    for page in pages:
        if page not in seen:
            seen.add(page)
            result.append(page)
    return result


def page_allowed(page_id: str, allowed_pages: Sequence[str] | set[str] | None) -> bool:
    """Return whether a page is covered by a page grant.

    ``None`` means "no page restriction"; an empty list means no pages.  This
    distinction matters at intake, where omitted ranges are explicit ``None``
    after normalization only for unrestricted block-like artifacts.
    """
    if allowed_pages is None:
        return True
    return page_id in set(allowed_pages)
