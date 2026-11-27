"""Stable-anchor public document model.

Only public text lives here.  Private annotations are deliberately stored in a
separate museum-only file and address the same neutral anchor IDs; they never
participate in public character offsets.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, List, Optional, Tuple

DOC_SCHEMA = "collab-anchored-document/v1"


def new_document(anchors: Iterable[Tuple[str, str]]) -> Dict[str, Any]:
    return {
        "schema": DOC_SCHEMA,
        "head": "genesis",
        "rev": 0,
        "anchors": [{"id": anchor_id, "text": text} for anchor_id, text in anchors],
    }


def clone_document(doc: Dict[str, Any]) -> Dict[str, Any]:
    return deepcopy(doc)


def anchor_index(doc: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {a["id"]: a for a in doc["anchors"]}


def get_anchor(doc: Dict[str, Any], anchor_id: str) -> Dict[str, Any]:
    for anchor in doc["anchors"]:
        if anchor["id"] == anchor_id:
            return anchor
    raise KeyError(f"unknown public anchor: {anchor_id}")


def anchor_text(doc: Dict[str, Any], anchor_id: str) -> str:
    return get_anchor(doc, anchor_id)["text"]


def render_public(doc: Dict[str, Any]) -> str:
    """Render public lines.

    Anchors are initially lines.  A split operation may itself insert newline
    characters inside an anchor, so the extra join separator is omitted for an
    anchor that already ends in a newline.  New anchors are not created by the
    simple splice editor, which keeps merge identity straightforward.
    """
    parts: List[str] = []
    for i, anchor in enumerate(doc["anchors"]):
        text = anchor["text"]
        parts.append(text)
        if i < len(doc["anchors"]) - 1 and not text.endswith("\n"):
            parts.append("\n")
    return "".join(parts)


def resolve_span(
    text: str,
    *,
    start: Optional[int] = None,
    end: Optional[int] = None,
    old: Optional[str] = None,
    occurrence: int = 0,
) -> Tuple[int, int, str]:
    """Resolve a public span.

    The caller already selected a stable anchor, so identical sentences in
    different anchors cannot collide.  ``old`` may still be used inside one
    anchor; occurrence is zero-based.
    """
    if old is not None:
        positions: List[int] = []
        at = text.find(old)
        while at != -1:
            positions.append(at)
            at = text.find(old, at + max(1, len(old)))
        if not positions:
            raise ValueError("old text not found in anchor")
        if occurrence >= len(positions):
            raise ValueError(f"occurrence {occurrence} not found; {len(positions)} match(es)")
        s = positions[occurrence]
        e = s + len(old)
    else:
        if start is None:
            raise ValueError("either old or start/end is required")
        s = start
        e = len(text) if end is None else end

    if s < 0 or e < s or e > len(text):
        raise ValueError(f"span [{s}:{e}] is outside anchor of length {len(text)}")
    return s, e, text[s:e]


def splice(
    doc: Dict[str, Any],
    anchor_id: str,
    start: int,
    end: int,
    inserted: str,
    expected_removed: Optional[str] = None,
) -> Dict[str, Any]:
    anchor = get_anchor(doc, anchor_id)
    text = anchor["text"]
    if start < 0 or end < start or end > len(text):
        raise ValueError(f"span [{start}:{end}] is outside anchor {anchor_id}")
    removed = text[start:end]
    if expected_removed is not None and removed != expected_removed:
        raise ValueError(
            f"public span changed at {anchor_id} [{start}:{end}]: "
            f"expected {expected_removed!r}, found {removed!r}"
        )
    anchor["text"] = text[:start] + inserted + text[end:]
    return {
        "anchor": anchor_id,
        "start": start,
        "end": end,
        "removed": removed,
        "inserted": inserted,
        "merged_start": start,
        "merged_end": start + len(inserted),
    }


def document_anchors_map(doc: Dict[str, Any]) -> Dict[str, str]:
    return {a["id"]: a["text"] for a in doc["anchors"]}


def documents_equal(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    return document_anchors_map(a) == document_anchors_map(b)
