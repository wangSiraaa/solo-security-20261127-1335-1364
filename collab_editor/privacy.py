"""Allowlist sanitization and leakage checks for the external trust boundary."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

PACKET_SCHEMA = "collab-public-packet/v1"
ANSWER_SCHEMA = "collab-merge-answer/v1"

# Every key here is public-text metadata by construction.  Internal mixed-op
# ids, note bodies, museum-only absolute offsets and private counters are never
# copied into a packet by spreading a stored object.
PUBLIC_OP_KEYS = {
    "id",
    "kind",
    "author",
    "created_at",
    "base_head",
    "anchor",
    "start",
    "end",
    "removed",
    "inserted",
    "expected_removed",
    "undo_of",
    "redo_of",
    "corrects",
    "withdraws",
    "merge_from",
    "parents",
    "packet_id",
    "conflict_id",
    "choice",
    "sources",
    "affected_locations",
    "result",
}

SENSITIVE_MARKERS = (
    "private_annotation",
    "private_annotations",
    "annotations.json",
    "full_text_start",
    "full_text_end",
    "private_op_count",
    "pure_private_op_count",
    "private_count",
    "internal_seq",
    "internal_op_id",
    "mixed_op_id",
    "mixed-edit",
    "mixed-public-half",
    "MX-",
    "PV-",
    "〖私注",
)


def _location_view(location: Any) -> Dict[str, Any]:
    if not isinstance(location, dict):
        return {}
    allowed = (
        "anchor",
        "base_start",
        "base_end",
        "start",
        "end",
        "before",
        "after",
        "sources",
    )
    return {key: location[key] for key in allowed if key in location}


def public_op_view(op: Dict[str, Any]) -> Dict[str, Any]:
    """Return a new dict containing only allowlisted public operation fields."""
    view: Dict[str, Any] = {}
    for key in PUBLIC_OP_KEYS:
        if key not in op:
            continue
        value = op[key]
        if key == "affected_locations" and isinstance(value, list):
            value = [_location_view(item) for item in value]
        elif key == "result" and isinstance(value, dict):
            # Merge results contain only anchor spans.  Rebuild the safe
            # subset rather than passing through arbitrary future fields.
            value = {
                "status": value.get("status"),
                "affected_locations": [
                    _location_view(item)
                    for item in value.get("affected_locations", [])
                    if isinstance(item, dict)
                ],
            }
        view[key] = value
    return view


def packet_view(
    *,
    packet_id: str,
    author: str,
    created_at: str,
    base_ref: str,
    base_doc: Dict[str, Any],
    doc: Dict[str, Any],
    head: str,
    changes: Sequence[Dict[str, Any]],
    kind: str = "push",
) -> Dict[str, Any]:
    return {
        "schema": PACKET_SCHEMA,
        "kind": kind,
        "packet_id": packet_id,
        "author": author,
        "created_at": created_at,
        "base_ref": base_ref,
        "base_doc": base_doc,
        "doc": doc,
        "head": head,
        "changes": [public_op_view(op) for op in changes],
    }


def _iter_files(root: Path) -> Iterable[Path]:
    if root.is_file():
        yield root
        return
    for path in sorted(root.rglob("*")):
        if path.is_file():
            yield path


def scan_for_leaks(
    roots: Sequence[str | Path],
    canaries: Sequence[str] = (),
) -> List[Dict[str, str]]:
    """Scan counterpart-readable roots for obvious private markers.

    This is a defense-in-depth test/audit function.  Sanitization is allowlist
    based; the scanner catches accidental serialization or report wording.
    """
    findings: List[Dict[str, str]] = []
    needles = list(SENSITIVE_MARKERS) + list(canaries)
    for root_value in roots:
        root = Path(root_value)
        if not root.exists():
            continue
        for path in _iter_files(root):
            lowered_name = path.name.lower()
            if "private" in lowered_name or "annotation" in lowered_name:
                findings.append({"path": str(path), "reason": "sensitive file name"})
                continue
            # Binary/random files are not expected in these tiny workspaces.
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                data = path.read_bytes()
                for canary in canaries:
                    if canary.encode("utf-8") in data:
                        findings.append(
                            {"path": str(path), "reason": f"canary {canary!r} in binary file"}
                        )
                continue

            for needle in needles:
                if needle and needle in text:
                    findings.append(
                        {"path": str(path), "reason": f"sensitive marker {needle!r}"}
                    )

            # Private/internal ids use prefixes that must never be exported.
            for match in re.findall(r"\b(?:MX|PV)-[A-Za-z0-9_-]+", text):
                findings.append({"path": str(path), "reason": f"internal id {match!r}"})

            # A JSON key explicitly named as private is always suspicious in an
            # external root, even if its value is empty.
            if re.search(r'"[a-z0-9_]*(?:private|annotation)[a-z0-9_]*"\s*:', text):
                findings.append({"path": str(path), "reason": "private JSON key"})
    return findings


def assert_clean(roots: Sequence[str | Path], canaries: Sequence[str] = ()) -> None:
    findings = scan_for_leaks(roots, canaries)
    if findings:
        details = "\n".join(f"- {f['path']}: {f['reason']}" for f in findings)
        raise AssertionError(f"privacy leak in external-readable files:\n{details}")


def safe_json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
