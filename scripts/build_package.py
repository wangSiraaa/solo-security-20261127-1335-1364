#!/usr/bin/env python3
"""Create the small fictional generation-6 publisher package and approvals."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from museum_update.security import canonical_json, set_digest, sha256_bytes, sha256_file  # noqa: E402

PACKAGE = ROOT / "packages" / "gen6"
PAYLOAD = PACKAGE / "files"

FILES: dict[str, bytes] = {
    "system/core.bin": (
        "Museum terminal core, generation 6\n"
        "secure dual-slot update agent\n"
        "field-operation-contract: field-ops-v6\n"
    ).encode("utf-8"),
    "system/selftest.bin": (
        "simulated self-test descriptor\n"
        "checks=hash-tree,peripheral-probe,archive-service\n"
    ).encode("utf-8"),
    "components/reader.pack": "gen6 reader component; approved companion\n".encode("utf-8"),
    "components/index.pack": "gen6 archive index component; approved companion\n".encode("utf-8"),
    "components/fonts.pack": "gen6 font component; approved companion\n".encode("utf-8"),
}

COMPONENTS = [
    {"name": "core", "path": "system/core.bin", "role": "system"},
    {"name": "selftest", "path": "system/selftest.bin", "role": "system"},
    {"name": "reader", "path": "components/reader.pack", "role": "companion"},
    {"name": "archive-index", "path": "components/index.pack", "role": "companion"},
    {"name": "fonts", "path": "components/fonts.pack", "role": "companion"},
]


def main() -> int:
    for rel_path, data in FILES.items():
        path = PAYLOAD / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    file_digests = {
        rel_path: "sha256:" + __import__("hashlib").sha256(data).hexdigest()
        for rel_path, data in FILES.items()
    }
    manifest = {
        "schema": "museum-update-manifest/v1",
        "order_id": "ORD-G6-001",
        "generation": 6,
        "payload_root": "files",
        "components": COMPONENTS,
        "files": file_digests,
    }
    manifest_path = PACKAGE / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    component_set = [
        {
            "name": c["name"],
            "path": c["path"],
            "role": c["role"],
            "digest": file_digests[c["path"]],
        }
        for c in COMPONENTS
    ]
    approval = {
        "schema": "museum-update-approval/v1",
        "order_id": "ORD-G6-001",
        "generation": 6,
        "manifest_digest": sha256_file(manifest_path),
        "file_digests": file_digests,
        "components": COMPONENTS,
        "component_set_digest": sha256_bytes(canonical_json(component_set)),
        "approved_set_digest": set_digest(file_digests),
        "note": "Fictional publisher approval. Digests, not filenames alone, authorize bytes.",
    }
    (PACKAGE / "approval.json").write_text(
        json.dumps(approval, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"built {PACKAGE} with {len(FILES)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
