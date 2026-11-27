"""Three-way public-text merge against the last common public draft.

The merger intentionally knows nothing about private annotations.  It works on
stable anchor IDs and character spans inside those anchors.  That lets two
identical sentences live at different anchors and lets two offline cuts in the
same line be aligned when they use different base offsets.
"""
from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

from .document import anchor_index, clone_document, document_anchors_map


def net_patches(base: str, changed: str) -> List[Dict[str, Any]]:
    """Return disjoint changed spans of base, each replaced by text."""
    matcher = SequenceMatcher(None, base, changed, autojunk=False)
    patches: List[Dict[str, Any]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        patches.append(
            {
                "base_start": i1,
                "base_end": i2,
                "text": changed[j1:j2],
            }
        )
    return patches


def _strict_overlap(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    s1, e1 = a["base_start"], a["base_end"]
    s2, e2 = b["base_start"], b["base_end"]

    # Point insertion versus a non-empty replacement: only a strictly interior
    # point is ambiguous.  Inserts exactly at a boundary can be ordered.
    if s1 == e1:
        if s2 < s1 < e2:
            return True
        if s1 == s2 == e2:
            return False
    if s2 == e2:
        if s1 < s2 < e1:
            return True
        if s2 == s1 == e1:
            return False

    left = max(s1, s2)
    right = min(e1, e2)
    if left < right:
        # Exact same replacement is idempotent, not a conflict.
        if s1 == s2 and e1 == e2 and a.get("text") == b.get("text"):
            return False
        return True

    # Two inserts at the same point.  Identical inserts coalesce; different
    # concurrent text at one cursor is a real public ambiguity.
    if s1 == e1 and s2 == e2 and s1 == s2:
        return a.get("text") != b.get("text")

    return False


def _same_patch(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    return (
        a["base_start"] == b["base_start"]
        and a["base_end"] == b["base_end"]
        and a.get("text") == b.get("text")
    )


def merge_anchor_text(
    base: str,
    local: str,
    remote: str,
    anchor_id: str,
    *,
    local_name: str = "local",
    remote_name: str = "remote",
) -> Dict[str, Any]:
    if local == remote:
        patches = net_patches(base, local)
        hunks = [
            {
                "anchor": anchor_id,
                "base_start": p["base_start"],
                "base_end": p["base_end"],
                "start": p["base_start"],
                "end": p["base_start"] + len(p["text"]),
                "before": base[p["base_start"] : p["base_end"]],
                "after": p["text"],
                "sources": [local_name, remote_name] if local != base else [local_name],
            }
            for p in patches
        ]
        return {"anchor": anchor_id, "status": "clean", "text": local, "hunks": hunks, "conflicts": []}

    lp = [dict(p, source=local_name) for p in net_patches(base, local)]
    rp = [dict(p, source=remote_name) for p in net_patches(base, remote)]

    conflicts: List[Dict[str, Any]] = []
    used_remote = set()
    for li, a in enumerate(lp):
        for ri, b in enumerate(rp):
            if _strict_overlap(a, b):
                # Identical overlap is coalesced later.
                if _same_patch(a, b):
                    used_remote.add(ri)
                    continue
                lo = min(a["base_start"], b["base_start"])
                hi = max(a["base_end"], b["base_end"])
                conflicts.append(
                    {
                        "anchor": anchor_id,
                        "base_start": lo,
                        "base_end": hi,
                        "base_excerpt": base[lo:hi],
                        "local_excerpt": local,
                        "remote_excerpt": remote,
                        "local_span": {
                            "base_start": a["base_start"],
                            "base_end": a["base_end"],
                            "replacement": a["text"],
                        },
                        "remote_span": {
                            "base_start": b["base_start"],
                            "base_end": b["base_end"],
                            "replacement": b["text"],
                        },
                        "message": (
                            f"concurrent public changes overlap at anchor {anchor_id} "
                            f"characters {lo}..{hi}"
                        ),
                    }
                )

    if conflicts:
        return {
            "anchor": anchor_id,
            "status": "conflict",
            "text": None,
            "hunks": [],
            "conflicts": conflicts,
        }

    # Coalesce exact duplicates; retain one patch with both sources.
    combined: List[Dict[str, Any]] = []
    for p in lp + rp:
        duplicate = None
        for c in combined:
            if _same_patch(p, c):
                duplicate = c
                break
        if duplicate is not None:
            sources = set(duplicate.get("sources", []))
            sources.add(p["source"])
            duplicate["sources"] = sorted(sources)
        else:
            combined.append(dict(p, sources=[p["source"]]))

    def sort_key(p: Dict[str, Any]) -> Tuple[int, int, int]:
        # At the same boundary, a point insert precedes a replacement.
        is_point = 0 if p["base_start"] == p["base_end"] else 1
        return (p["base_start"], is_point, p["base_end"])

    combined.sort(key=sort_key)

    out: List[str] = []
    pos = 0
    hunks: List[Dict[str, Any]] = []
    for p in combined:
        s, e, text = p["base_start"], p["base_end"], p["text"]
        out.append(base[pos:s])
        merged_start = len("".join(out))
        out.append(text)
        merged_end = merged_start + len(text)
        hunks.append(
            {
                "anchor": anchor_id,
                "base_start": s,
                "base_end": e,
                "start": merged_start,
                "end": merged_end,
                "before": base[s:e],
                "after": text,
                "sources": p.get("sources", []),
            }
        )
        pos = e
    out.append(base[pos:])
    merged_text = "".join(out)

    return {
        "anchor": anchor_id,
        "status": "clean",
        "text": merged_text,
        "hunks": hunks,
        "conflicts": [],
    }


def merge_documents(
    base_doc: Dict[str, Any],
    local_doc: Dict[str, Any],
    remote_doc: Dict[str, Any],
    *,
    local_name: str = "local",
    remote_name: str = "remote",
) -> Dict[str, Any]:
    base_map = document_anchors_map(base_doc)
    local_map = document_anchors_map(local_doc)
    remote_map = document_anchors_map(remote_doc)

    anchor_ids = [a["id"] for a in base_doc["anchors"]]
    for doc in (local_doc, remote_doc):
        for anchor in doc["anchors"]:
            if anchor["id"] not in base_map and anchor["id"] not in anchor_ids:
                anchor_ids.append(anchor["id"])

    results = []
    conflicts: List[Dict[str, Any]] = []
    affected: List[Dict[str, Any]] = []

    for anchor_id in anchor_ids:
        if anchor_id not in base_map:
            item = {
                "anchor": anchor_id,
                "base_start": 0,
                "base_end": 0,
                "message": f"anchor {anchor_id} has no common public base",
            }
            results.append({"anchor": anchor_id, "status": "conflict"})
            conflicts.append(item)
            continue
        if anchor_id not in local_map or anchor_id not in remote_map:
            item = {
                "anchor": anchor_id,
                "base_start": 0,
                "base_end": len(base_map[anchor_id]),
                "message": f"anchor {anchor_id} is missing on one side",
            }
            results.append({"anchor": anchor_id, "status": "conflict"})
            conflicts.append(item)
            continue

        result = merge_anchor_text(
            base_map[anchor_id],
            local_map[anchor_id],
            remote_map[anchor_id],
            anchor_id,
            local_name=local_name,
            remote_name=remote_name,
        )
        results.append(result)
        if result["conflicts"]:
            conflicts.extend(result["conflicts"])
        for hunk in result["hunks"]:
            # A hunk is affected only if it changes the common base text.
            if hunk["before"] != hunk["after"] or hunk["base_start"] != hunk["base_end"]:
                affected.append(hunk)

    clean = not conflicts
    merged_doc = None
    if clean:
        merged_doc = clone_document(base_doc)
        by_id = anchor_index(merged_doc)
        for result in results:
            if result["status"] == "clean" and result["anchor"] in by_id:
                by_id[result["anchor"]]["text"] = result["text"]

    return {
        "status": "merged" if clean else "conflict",
        "doc": merged_doc,
        "anchor_results": results,
        "affected_locations": affected,
        "conflicts": conflicts,
    }
