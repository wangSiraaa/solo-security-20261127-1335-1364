"""Boundary adapter for Bihe classrooms that still emit legacy word ids.

The reviewed resolver remains untouched.  All compatibility mapping and any
future transport policy lives here, so a change to this file invalidates only
the boundary/composition review, not the byte-identical reviewed component.
"""
from __future__ import annotations

from typing import Any

from mojiao.dictionary import DictionarySet
from mojiao.reviewed_resolver import resolve as reviewed_resolve


def resolve(request: dict[str, Any], dictionaries: DictionarySet) -> dict[str, Any]:
    legacy_code = str(request.get("word_id") or request.get("id") or "").strip()
    entry = dictionaries.by_legacy_id.get(legacy_code)
    modern_id = entry.id if entry is not None and entry.id.startswith("W-") else None

    modern_request = {"id": modern_id} if modern_id else {"id": legacy_code}
    result = reviewed_resolve(modern_request, dictionaries)
    result["boundary"] = "compat_oldid_v1"
    result["requested_legacy_id"] = legacy_code
    if result.get("status") == "ok":
        result["word_id"] = legacy_code
    return result


# Contract-style alias.
resolve_code = resolve
