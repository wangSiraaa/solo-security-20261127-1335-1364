"""Reviewed mainline fix: resolve promised entries entirely locally.

This module is the exact artifact named by the mainline release endorsement.
Do not add compatibility/network code here; put branch adapters outside it so
the reviewed file can remain byte-for-byte verifiable.
"""
from __future__ import annotations

from typing import Any

from .dictionary import DictionarySet, normalize_text


def resolve(request: dict[str, Any], dictionaries: DictionarySet) -> dict[str, Any]:
    """Resolve a modern caller request without network access.

    Accepted request fields: ``id`` (modern or legacy id) or ``text``.
    The return value always reports ``egress_performed=False``; this function
    has no transport dependency.
    """
    word_id = request.get("id")
    text = normalize_text(request.get("text"))

    entry = None
    lookup_kind = None
    if word_id is not None:
        word_id = str(word_id)
        entry = dictionaries.by_id.get(word_id)
        if entry is not None:
            lookup_kind = "modern_id"
        if entry is None:
            entry = dictionaries.by_legacy_id.get(word_id)
            if entry is not None:
                lookup_kind = "legacy_id"
    if entry is None and text:
        entry = dictionaries.by_query.get(text)
        if entry is not None:
            lookup_kind = "text"

    if entry is None:
        return {
            "status": "not_found",
            "query": request.get("text") or word_id,
            "lookup_kind": lookup_kind or "unresolved",
            "canonical": None,
            "word_id": None,
            "legacy_id": None,
            "dictionary_version": None,
            "egress_performed": False,
        }

    source = next(
        (d for d in dictionaries.dictionaries if any(e.id == entry.id for e in d.entries)),
        None,
    )
    return {
        "status": "ok",
        "query": entry.query,
        "lookup_kind": lookup_kind,
        "canonical": entry.canonical,
        "word_id": entry.id,
        "legacy_id": entry.legacy_id,
        "source_list": source.list_id if source else None,
        "dictionary_version": source.version if source else None,
        "egress_performed": False,
    }
