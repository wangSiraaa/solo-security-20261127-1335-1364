#!/usr/bin/env python3
"""Offline reviewer verification entrypoint shipped inside every bundle."""
from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
IGNORE_NAMES = {"MANIFEST.json", "MANIFEST.sig", "PUBLISHER.pub", "verify.py", "ww"}
IGNORE_PARTS = {"__pycache__", ".pytest_cache", "usage", "cabinets"}
IGNORE_SUFFIXES = {".pyc"}


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_signature(payload: bytes, signature_b64: str, pubkey: Path) -> bool:
    try:
        signature = base64.b64decode(signature_b64)
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "payload"
            s = Path(td) / "sig"
            p.write_bytes(payload)
            s.write_bytes(signature)
            proc = subprocess.run(
                ["openssl", "pkeyutl", "-verify", "-pubin", "-inkey", str(pubkey), "-rawin", "-in", str(p), "-sigfile", str(s)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        return proc.returncode == 0
    except Exception:
        return False


def should_ignore(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    return (
        path.name in IGNORE_NAMES
        or any(part in IGNORE_PARTS for part in rel.parts)
        or path.suffix in IGNORE_SUFFIXES
    )


def main() -> int:
    manifest_path = ROOT / "MANIFEST.json"
    if not manifest_path.exists():
        print(json.dumps({"ok": False, "reason": "MANIFEST.json missing"}, ensure_ascii=False))
        return 2
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    signature = manifest.pop("signature", None)
    key_id = manifest.get("key_id")
    pubkey = ROOT / "PUBLISHER.pub"
    signature_ok = bool(signature and pubkey.exists() and verify_signature(canonical(manifest), signature, pubkey))

    file_results = []
    hash_ok = True
    for rel, expected in sorted(manifest.get("files", {}).items()):
        path = ROOT / rel
        if not path.exists():
            hash_ok = False
            file_results.append({"path": rel, "ok": False, "reason": "missing"})
            continue
        actual = sha256(path)
        ok = actual == expected
        hash_ok &= ok
        file_results.append({"path": rel, "ok": ok, "sha256": actual})

    actual_files = set()
    extras = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or should_ignore(path):
            continue
        rel = path.relative_to(ROOT).as_posix()
        actual_files.add(rel)
        if rel not in manifest.get("files", {}):
            extras.append(rel)

    endorsement_results = []
    for rel in manifest.get("endorsement_files", []):
        path = ROOT / rel
        if not path.exists():
            endorsement_results.append({"path": rel, "ok": False, "reason": "missing"})
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        sig = doc.pop("signature", None)
        ok = bool(sig and verify_signature(canonical(doc), sig, pubkey))
        endorsement_results.append({"path": rel, "ok": ok, "endorsement_id": doc.get("endorsement_id")})

    ok = signature_ok and hash_ok and all(x["ok"] for x in endorsement_results)
    report = {
        "ok": ok,
        "bundle_id": manifest.get("bundle_id"),
        "key_id": key_id,
        "manifest_signature_ok": signature_ok,
        "file_hashes_ok": hash_ok,
        "endorsements": endorsement_results,
        "missing_or_changed": [x for x in file_results if not x["ok"]],
        "extra_files_not_in_manifest": sorted(extras),
        "hint": "./ww lookup 1001 或 python3 verify.py --human" if "--human" in sys.argv else None,
    }
    if "--human" in sys.argv:
        print(("通过" if ok else "未通过") + f"：{manifest.get('bundle_id')}")
        print(f"清单签名：{'是' if signature_ok else '否'}；文件哈希：{'一致' if hash_ok else '不一致/缺失'}")
        for item in endorsement_results:
            print(f"背书 {item.get('endorsement_id') or item['path']}：{'有效' if item['ok'] else '无效'}")
        if extras:
            print("清单外文件（不影响来源校验，但须确认是否为运行产物）：" + ", ".join(sorted(extras)))
        for item in report["missing_or_changed"]:
            print(f"异常文件 {item['path']}: {item.get('reason', 'hash mismatch')}")
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if ok else 3


if __name__ == "__main__":
    raise SystemExit(main())
