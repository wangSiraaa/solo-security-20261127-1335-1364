#!/usr/bin/env python3
"""Perform the exact date-move/restore/edition-swap sequence in one lab.

The lab is a fresh temporary directory containing only files extracted from
published ZIPs and reader originals.  It does not read cabinet/private.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

os.umask(0o022)
ROOT = Path(__file__).resolve().parent.parent
DELIVERABLES = ROOT / "deliverables"
BUNDLES = DELIVERABLES / "bundles"
READER = DELIVERABLES / "reader-originals"
REVIEWS = DELIVERABLES / "reviews"
LAST_REL = Path("artifacts/07-renewal-4-last.json")


def extract_zip(zip_path: Path, dest: Path) -> Path:
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(dest)
    manifests = list(dest.glob("**/manifest.json"))
    if len(manifests) != 1:
        raise RuntimeError(f"{zip_path}: expected one manifest, found {len(manifests)}")
    return manifests[0].parent


def verify(bundle: Path, original: Path) -> tuple[int, dict[str, Any]]:
    verifier = bundle / "verify_independent.py"
    proc = subprocess.run(
        [sys.executable, str(verifier), str(bundle),
         "--original", str(original), "--json",
         "--now", "2026-11-27T09:00:00Z"],
        cwd=bundle.parent,
        env={"PATH": os.environ["PATH"], "HOME": str(bundle.parent),
             "PYTHONDONTWRITEBYTECODE": "1"},
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError:
        report = {"ok": False, "summary": "verifier returned non-JSON",
                  "stdout": proc.stdout, "stderr": proc.stderr}
    return proc.returncode, report


def step(name: str, expectation: dict[str, Any], rc: int, report: dict[str, Any]) -> dict[str, Any]:
    break_id = (report.get("break_at") or {}).get("artifact_id")
    failures = [c.get("id") for c in report.get("checks", []) if c.get("status") == "fail"]
    ok = rc == expectation["rc"] and report.get("ok") is expectation["ok"]
    if "break_id" in expectation:
        ok = ok and break_id == expectation["break_id"]
    if "failure_id" in expectation:
        ok = ok and expectation["failure_id"] in failures
    result = {
        "step": name,
        "passed": ok,
        "exit_code": rc,
        "expected_exit_code": expectation["rc"],
        "ok": report.get("ok"),
        "expected_ok": expectation["ok"],
        "break_id": break_id,
        "expected_break_id": expectation.get("break_id"),
        "failure_ids": failures,
        "summary": report.get("summary"),
        "break_at": report.get("break_at"),
    }
    print(f"{'PASS' if ok else 'FAIL'} {name}: rc={rc}, break={break_id}")
    if not ok:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="coop-mutation-sequence-") as td:
        stage = Path(td)
        pristine_zip = BUNDLES / "dengying-registry-timely.zip"
        late_zip = BUNDLES / "dengying-late-renewal-after-deadline.zip"
        if not pristine_zip.is_file() or not late_zip.is_file():
            raise FileNotFoundError("run ./coop build before mutation_sequence.py")

        pristine_src = extract_zip(pristine_zip, stage / "pristine-zip")
        late_src = extract_zip(late_zip, stage / "late-zip")
        work = stage / "work"
        shutil.copytree(pristine_src, work)

        reader = stage / "reader"
        reader.mkdir()
        a = reader / "DENG-YING-v1-tui.txt"
        b = reader / "DENG-YING-v2-qiao.txt"
        shutil.copy2(READER / a.name, a)
        shutil.copy2(READER / b.name, b)

        pristine_last = pristine_src / LAST_REL
        late_last = late_src / LAST_REL
        work_last = work / LAST_REL
        results: list[dict[str, Any]] = []

        rc, report = verify(work, a)
        results.append(step("1-verify-v1-before-mutation", {"rc": 0, "ok": True}, rc, report))

        # Literal date move without access to the registry signing key.
        env = json.loads(work_last.read_text(encoding="utf-8"))
        env["data"]["issued_at"] = "2026-07-02T09:00:00Z"
        work_last.write_text(json.dumps(env, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        rc, report = verify(work, a)
        results.append(step("2a-move-date-only-without-resign",
                           {"rc": 2, "ok": False, "break_id": "renewal-4",
                            "failure_id": "signature-invalid"}, rc, report))

        # Restore the exact signed file, then model an honestly re-signed late
        # receipt produced by the operator/witness after the deadline.
        shutil.copy2(pristine_last, work_last)
        shutil.copy2(late_last, work_last)
        rc, report = verify(work, a)
        results.append(step("2b-honest-late-reissuance-after-deadline",
                           {"rc": 2, "ok": False, "break_id": "renewal-4",
                            "failure_id": "deadline-anchor-late"}, rc, report))

        # Explicitly restore the date/signed artifact.
        shutil.copy2(pristine_last, work_last)
        rc, report = verify(work, a)
        results.append(step("3-restore-date-and-reverify-v1",
                           {"rc": 0, "ok": True}, rc, report))

        # Keep restored evidence fixed; change only the reader's original.
        rc, report = verify(work, b)
        results.append(step("4-swap-only-reader-original-to-v2",
                           {"rc": 2, "ok": False, "break_id": "reader-original",
                            "failure_id": "digest-mismatch"}, rc, report))

    passed = all(r["passed"] for r in results)
    REVIEWS.mkdir(parents=True, exist_ok=True)
    (REVIEWS / "mutation-sequence.json").write_text(
        json.dumps({"passed": passed, "results": results}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")

    lines = [
        "# Mutation sequence review",
        "",
        f"Overall: **{'PASS' if passed else 'FAIL'}**",
        "",
        "All operations ran in a fresh temporary lab using published ZIPs/reader files only.",
        "",
        "| Step | Result | Exit | Break | Key failure | Summary |",
        "|---|---|---:|---|---|---|",
    ]
    for r in results:
        key_failure = ", ".join(x for x in r["failure_ids"]
                                if x in {"signature-invalid", "deadline-anchor-late", "digest-mismatch"})
        lines.append(f"| {r['step']} | {'PASS' if r['passed'] else 'FAIL'} | {r['exit_code']} | "
                     f"{r.get('break_id') or ''} | {key_failure} | {r['summary']} |")
    (REVIEWS / "MUTATION_SEQUENCE.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nWrote {REVIEWS / 'MUTATION_SEQUENCE.md'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
