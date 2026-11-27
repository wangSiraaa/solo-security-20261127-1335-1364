#!/usr/bin/env python3
"""Exercise the online witness service for a complete, timely manuscript.

This is the operator-side happy path.  It starts a separate local HTTP process,
obtains signed simulated receipts over HTTP, and writes a bundle that a later
external process can verify after the server has stopped.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

WORKSPACE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE))

from cooplib.common import BUNDLE_DIR, PRIVATE, sha256_file, write_json  # noqa: E402
from cooplib.scenario import (  # noqa: E402
    OLD_WITNESS_END,
    artifact_entry,
    install_common_bundle_files,
    write_manifest,
    zip_bundle,
)
from cooplib.server import CLOCK_FILE  # noqa: E402

ENDPOINT_FILE = PRIVATE / "online-flow-endpoint.json"
SERVER_LOG = PRIVATE / "online-flow-server.log"

ONLINE_TEXT = """\
在线完整原稿办理测试（虚构）

此稿用于证明：见证服务在线、正文与核对材料完整时，
系统能够实际取得旧回条并在旧见证可采信期内完成替代见证移存。

版本号 ONLINE-OK-1。
"""


def http_json(method: str, url: str, obj: dict[str, Any] | None = None,
              token: str | None = None) -> dict[str, Any]:
    data = None if obj is None else json.dumps(obj).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-Admin-Token", token)
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def wait_for_server() -> dict[str, Any]:
    for _ in range(50):
        if ENDPOINT_FILE.exists():
            endpoint = json.loads(ENDPOINT_FILE.read_text(encoding="utf-8"))
            try:
                health = http_json("GET", f"{endpoint['base_url']}/health")
                if health.get("ok"):
                    endpoint["health"] = health
                    return endpoint
            except (urllib.error.URLError, ConnectionError, OSError):
                pass
        time.sleep(0.1)
    raise RuntimeError("online witness server did not become healthy")


def write_envelope(bundle: Path, rel: str, envelope: dict[str, Any]) -> str:
    path = bundle / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(envelope, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return sha256_file(path)


def main() -> int:
    if ENDPOINT_FILE.exists():
        ENDPOINT_FILE.unlink()
    SERVER_LOG.parent.mkdir(parents=True, exist_ok=True)
    log = SERVER_LOG.open("wb")
    server = subprocess.Popen(
        [sys.executable, str(WORKSPACE / "coop"), "serve",
         "--port", "0", "--write-endpoint", str(ENDPOINT_FILE)],
        cwd=WORKSPACE, stdout=log, stderr=subprocess.STDOUT,
    )
    try:
        endpoint = wait_for_server()
        base = endpoint["base_url"]
        token = Path(endpoint["admin_token_file"]).read_text(encoding="utf-8").strip()
        print(f"online witness process healthy at {base}")

        bundle = BUNDLE_DIR / "online-complete-http-flow"
        if bundle.exists():
            import shutil
            shutil.rmtree(bundle)
        install_common_bundle_files(bundle)

        original_rel = "originals/ONLINE-OK-1.txt"
        original = bundle / original_rel
        original.parent.mkdir(parents=True, exist_ok=True)
        original.write_text(ONLINE_TEXT, encoding="utf-8")
        digest = sha256_file(original)
        private_copy = PRIVATE / "originals" / "ONLINE-OK-1.txt"
        private_copy.parent.mkdir(parents=True, exist_ok=True)
        private_copy.write_text(ONLINE_TEXT, encoding="utf-8")

        http_json("POST", f"{base}/admin/clock", {"now": "2025-04-01T10:00:00Z"}, token)
        old_env = http_json("POST", f"{base}/v1/witness/old-witness-2023/sign", {"data": {
            "type": "timestamp-receipt",
            "receipt_id": "ONLINE-OLD-0001",
            "manuscript_id": "ONLINE-OK",
            "edition": "1",
            "whole_item_digested": True,
            "hash_algorithm": "sha-256",
            "digest": digest,
        }})
        old_rel = "artifacts/01-old-receipt-from-http.json"
        old_hash = write_envelope(bundle, old_rel, old_env)

        check_env = http_json("POST", f"{base}/v1/witness/clerk-li-2025/sign", {"data": {
            "type": "contemporaneous-crosscheck",
            "check_id": "ONLINE-XCHECK-0001",
            "manuscript_id": "ONLINE-OK",
            "edition": "1",
            "digest": digest,
            "result": "match",
            "old_receipt": {"artifact_id": "old-receipt", "sha256": old_hash},
        }})
        check_rel = "artifacts/02-crosscheck-from-http.json"
        check_hash = write_envelope(bundle, check_rel, check_env)

        http_json("POST", f"{base}/admin/clock", {"now": "2026-11-19T15:00:00Z"}, token)
        handover_env = http_json("POST", f"{base}/v1/witness/owner-qixia/sign", {"data": {
            "type": "custody-handover",
            "handover_id": "ONLINE-HANDOVER-0001",
            "manuscript_id": "ONLINE-OK",
            "edition": "1",
            "digest": digest,
            "on_time": True,
            "custody_choice": "self-contained-migration-bundle",
        }})
        handover_rel = "artifacts/03-handover-from-http.json"
        handover_hash = write_envelope(bundle, handover_rel, handover_env)

        migration_env = http_json("POST", f"{base}/v1/witness/new-witness-2026/sign", {"data": {
            "type": "migration-attestation",
            "migration_id": "ONLINE-MIG-0001",
            "relied_authority": "old-witness-2023",
            "manuscript_id": "ONLINE-OK",
            "edition": "1",
            "digest": digest,
            "verified": {"online_complete_materials": True, "signature_checks": True},
            "old_receipt": {"artifact_id": "old-receipt", "sha256": old_hash},
            "crosschecks": [{"artifact_id": "crosscheck", "sha256": check_hash}],
            "handover": {"artifact_id": "handover", "sha256": handover_hash},
        }})
        migration_rel = "artifacts/04-migration-from-http.json"
        write_envelope(bundle, migration_rel, migration_env)

        manifest = {
            "schema": "archive-coop-manifest/1",
            "case_id": "ONLINE-OK-1-http-happy-path",
            "title": "Online normal service with complete materials",
            "claim": "self-contained-bridge",
            "cutoff": OLD_WITNESS_END,
            "original": {"path": original_rel, "required": True, "sha256": digest},
            "policy": "policy.json",
            "trust_dir": "trust",
            "artifacts": [
                artifact_entry("old-receipt", old_rel),
                artifact_entry("crosscheck", check_rel),
                artifact_entry("handover", handover_rel),
                artifact_entry("migration", migration_rel),
            ],
        }
        write_manifest(bundle, manifest)
        zip_path = zip_bundle(bundle)
        write_json(PRIVATE / "online-flow-result.json", {
            "ok": True, "base_url": base, "bundle": str(bundle), "zip": str(zip_path),
            "digest": digest,
        })
        print(f"online flow complete: {bundle}")
        print(f"zip: {zip_path}")
        return 0
    finally:
        server.terminate()
        try:
            server.wait(timeout=3)
        except subprocess.TimeoutExpired:
            server.kill()
        log.close()
        # Return a manually started witness service to its real-time default.
        CLOCK_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
