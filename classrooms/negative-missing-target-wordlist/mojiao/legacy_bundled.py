"""Historical Qianzi implementation where lookup method and table shipped together.

It remains in the tree so reviewers can inspect the old packaging model, but
the school classroom manifest calls ``mojiao.native_legacy:resolve``.  Review
records hash the actual entrypoint and the full Mojiao Python tree rather than
trusting a patch/merge title.
"""
from __future__ import annotations

import re
from typing import Any

_BUNDLED_TABLE_VERSION = "bundled-legacy-table-1.3.0"
_BUNDLED_WORDS = {
    "1001": {"query": "按装", "output": "安装"},
    "1002": {"query": "图象", "output": "图像"},
    "1003": {"query": "订正", "output": "订正"},
}
_OLD_ID = re.compile(r"^\d{4}$")


def lookup_bundled(request: dict[str, Any], _dictionaries=None) -> dict[str, Any]:
    code = str(request.get("word_id") or request.get("id") or "")
    if not _OLD_ID.fullmatch(code):
        return {"status": "contract_error", "word_id": code, "canonical": None, "egress_performed": False}
    row = _BUNDLED_WORDS.get(code)
    if row is None:
        return {"status": "old_id_not_found", "word_id": code, "canonical": None, "egress_performed": False}
    return {
        "status": "ok",
        "word_id": code,
        "canonical": row["output"],
        "query": row["query"],
        "dictionary_version": _BUNDLED_TABLE_VERSION,
        "packaging": "lookup-method-and-word-table-bundled",
        "egress_performed": False,
    }
