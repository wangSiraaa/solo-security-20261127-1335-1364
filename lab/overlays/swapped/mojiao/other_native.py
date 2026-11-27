"""Negative-control swap: same patch title, different actually-called code."""
from __future__ import annotations

from typing import Any

_HARD_CODED = {
    "1001": "安装",
    "1002": "图像",
    "1003": "订正",
}


def resolve(request: dict[str, Any], dictionaries=None) -> dict[str, Any]:
    code = str(request.get("word_id") or request.get("id") or "")
    canonical = _HARD_CODED.get(code)
    if canonical is None:
        return {"status": "old_id_not_found", "word_id": code, "canonical": None, "egress_performed": False}
    return {
        "status": "ok",
        "word_id": code,
        "canonical": canonical,
        "egress_performed": False,
        "implementation": "unreviewed_hardcoded_swap",
    }
