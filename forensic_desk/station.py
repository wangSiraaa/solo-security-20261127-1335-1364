"""Independent verification of an exported write-protected station bundle."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from .hashing import object_hash, sha256_bytes


class StationVerificationError(RuntimeError):
    pass


def verify_station_bundle(bundle_dir: str | Path) -> Dict[str, Any]:
    root = Path(bundle_dir)
    handoff_path = root / "handoff.record.json"
    manifest_path = root / "station-manifest.json"
    if not handoff_path.is_file() or not manifest_path.is_file():
        raise StationVerificationError("missing handoff record or station manifest")

    handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
    body = {k: v for k, v in handoff.items() if k != "hash"}
    if object_hash(body) != handoff.get("hash"):
        raise StationVerificationError("handoff record hash is invalid")

    station_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    errors: List[str] = []
    checked: List[Dict[str, Any]] = []

    intake_files = {f["path"]: f for f in handoff["payload"]["manifest"]}
    for expected in station_manifest["files"]:
        path = root / expected["path"]
        if not path.is_file():
            errors.append(f"missing {expected['path']}")
            continue
        data = path.read_bytes()
        actual_hash = sha256_bytes(data)
        intake = intake_files.get(expected["path"])
        ok = actual_hash == expected["sha256"]
        if not ok:
            errors.append(f"station manifest hash mismatch for {expected['path']}")
        if intake is None or intake["sha256"] != actual_hash:
            errors.append(f"file does not match intake manifest: {expected['path']}")
        checked.append({"path": expected["path"], "sha256": actual_hash, "ok": ok})

    for required in ("directory.idx", "operation_log.jsonl"):
        if not (root / required).is_file():
            errors.append(f"physical verification artifact missing: {required}")

    checksums_path = root / "SHA256SUMS"
    if checksums_path.is_file():
        for line_no, line in enumerate(checksums_path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                expected_hash, rel_path = line.split(None, 1)
            except ValueError:
                errors.append(f"SHA256SUMS line {line_no} is malformed")
                continue
            rel_path = rel_path.lstrip("*").strip()
            path = root / rel_path
            if not path.is_file() or sha256_bytes(path.read_bytes()) != expected_hash:
                errors.append(f"SHA256SUMS verification failed for {rel_path}")
    else:
        errors.append("SHA256SUMS missing")

    allowed_paths = {item["path"] for item in station_manifest["files"]} | {
        "handoff.record.json",
        "station-manifest.json",
        "SHA256SUMS",
    }
    actual_paths = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    }
    for extra in sorted(actual_paths - allowed_paths):
        errors.append(f"unexpected file in station bundle: {extra}")

    if errors:
        raise StationVerificationError("; ".join(errors))

    return {
        "ok": True,
        "bundle": str(root),
        "handoff_record_id": handoff["id"],
        "case_id": station_manifest["case_id"],
        "verified_files": checked,
        "third_party_repeatable": True,
        "physical_layout_scope": "only exported files can be re-verified off site",
    }
