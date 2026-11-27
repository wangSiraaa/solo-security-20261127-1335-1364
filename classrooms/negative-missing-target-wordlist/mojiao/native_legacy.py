"""Native legacy-word-number implementation for the Qianzi branch.

This is not a copy of the mainline reviewed resolver.  It is the branch
maintainer's own implementation and therefore needs branch-scoped validation
and a branch-only endorsement.
"""
from __future__ import annotations

import re
from typing import Any

from mojiao.dictionary import DictionarySet

OLD_WORD_ID = re.compile(r"^\d{4}$")


def resolve(request: dict[str, Any], dictionaries: DictionarySet) -> dict[str, Any]:
    code = str(request.get("word_id") or request.get("id") or "").strip()
    if not OLD_WORD_ID.fullmatch(code):
        return {
            "status": "contract_error",
            "word_id": code or None,
            "canonical": None,
            "egress_performed": False,
            "error": "caller/legacy-wordid-v1 requires a four-digit word_id",
        }

    entry = dictionaries.by_legacy_id.get(code)
    if entry is None:
        return {
            "status": "old_id_not_found",
            "word_id": code,
            "canonical": None,
            "egress_performed": False,
        }

    source = next(
        (d for d in dictionaries.dictionaries if any(e.id == entry.id for e in d.entries)),
        None,
    )
    return {
        "status": "ok",
        "word_id": code,
        "internal_word_id": entry.id,
        "canonical": entry.canonical,
        "query": entry.query,
        "source_list": source.list_id if source else None,
        "dictionary_version": source.version if source else None,
        "egress_performed": False,
        "implementation": "native_legacy",
    }


# Explicit contract name used in review records.
resolve_code = resolve
