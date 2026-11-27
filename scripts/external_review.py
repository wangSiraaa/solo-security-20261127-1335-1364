#!/usr/bin/env python3
"""Independent local reviewer.

This program models the stranger/library process: it copies only published ZIP
deliverables and a reader-supplied original into a fresh temporary directory,
extracts them, and runs the verifier contained in the ZIP.  It never opens
``cabinet/private`` and never imports the cooperative builder.

Expected negative cases are part of the test: a late renewal or swapped edition
must produce verifier exit status 2 and a precise break point.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

os.umask(0o022)

WORKSPACE = Path(__file__).resolve().parent.parent
DELIVERABLES = WORKSPACE / "deliverables"
BUNDLES = DELIVERABLES / "bundles"
READER = DELIVERABLES / "reader-originals"
REVIEWS = DELIVERABLES / "reviews"


@dataclass(frozen=True)
class Case:
    name: str
    zip_name: str
    original_name: str | None = None
    expect_rc: int = 0
    expect_ok: bool = True
    expect_break_id: str | None = None
    expect_warning_id: str | None = None
    reader_path_outside_reader_dir: Path | None = None


def cases() -> list[Case]:
    return [
        Case("01-qixia-crosslibrary-happy", "qixia-crosslibrary.zip", expect_rc=0, expect_ok=True),
        Case("01b-qixia-same-book-registry-closed", "qixia-crosslibrary-registry-closed.zip",
             expect_rc=0, expect_ok=True, expect_warning_id="registry-online-unavailable"),
        Case("02-online-http-complete", "online-complete-http-flow.zip", expect_rc=0, expect_ok=True),
        Case("03-dengying-v1-tui-happy", "dengying-registry-timely.zip",
             "DENG-YING-v1-tui.txt", 0, True),
        Case("04-dengying-last-renewal-late", "dengying-late-renewal-after-deadline.zip",
             "DENG-YING-v1-tui.txt", 2, False, "renewal-4"),
        Case("04b-dengying-date-edit-without-resign", "dengying-renewal-date-edited-no-resign.zip",
             "DENG-YING-v1-tui.txt", 2, False, "renewal-4"),
        Case("05-dengying-swap-reader-to-v2-qiao", "dengying-registry-timely.zip",
             "DENG-YING-v2-qiao.txt", 2, False, "reader-original"),
        Case("06-qixia-owner-one-day-late", "qixia-late-delivery.zip",
             expect_rc=2, expect_ok=False, expect_break_id="migration-2026"),
        Case("07-qixia-stamp-after-trust-end", "qixia-post-trust-stamp.zip",
             expect_rc=2, expect_ok=False, expect_break_id="migration-2026"),
        Case("08-registry-closed-history-still-verifies", "dengying-registry-closed.zip",
             "DENG-YING-v1-tui.txt", 0, True,
             expect_warning_id="registry-online-unavailable"),
        Case("09-registry-today-statement-only", "registry-statement-only.zip",
             expect_rc=2, expect_ok=False, expect_break_id="registry-current-statement"),
    ]


def find_bundle_root(stage: Path) -> Path:
    manifests = list(stage.glob("**/manifest.json"))
    if len(manifests) != 1:
        raise RuntimeError(f"expected one manifest.json in staged zip, found {len(manifests)}")
    return manifests[0].parent


def run_case(case: Case) -> dict[str, Any]:
    zip_path = BUNDLES / case.zip_name
    if not zip_path.is_file():
        raise FileNotFoundError(f"missing actual deliverable zip: {zip_path}")

    with tempfile.TemporaryDirectory(prefix=f"external-review-{case.name}-") as td:
        stage = Path(td)
        # The reviewer takes the actual ZIP, not the cabinet's working tree.
        staged_zip = stage / zip_path.name
        shutil.copy2(zip_path, staged_zip)
        with zipfile.ZipFile(staged_zip) as zf:
            zf.extractall(stage)

        bundle = find_bundle_root(stage)
        verifier = bundle / "verify_independent.py"
        if not verifier.is_file():
            raise RuntimeError("deliverable zip does not contain verify_independent.py")

        cmd = [sys.executable, str(verifier), str(bundle), "--json",
               "--now", "2026-11-27T09:00:00Z"]
        staged_original: Path | None = None
        if case.original_name:
            src = READER / case.original_name
            if not src.is_file():
                raise FileNotFoundError(src)
            reader_dir = stage / "reader-brought-by-foreign-process"
            reader_dir.mkdir()
            staged_original = reader_dir / case.original_name
            shutil.copy2(src, staged_original)
            cmd += ["--original", str(staged_original)]

        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(stage),
            "PYTHONDONTWRITEBYTECODE": "1",
            "LANG": os.environ.get("LANG", "C.UTF-8"),
        }
        proc = subprocess.run(cmd, cwd=stage, env=env, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            report = json.loads(proc.stdout)
        except json.JSONDecodeError:
            report = {"ok": False, "summary": "verifier did not return JSON",
                      "stdout": proc.stdout, "stderr": proc.stderr}

        result: dict[str, Any] = {
            "case": case.name,
            "zip": case.zip_name,
            "reader_original": case.original_name,
            "exit_code": proc.returncode,
            "expected_exit_code": case.expect_rc,
            "report": report,
            "stderr": proc.stderr,
        }

        expectation_failures: list[str] = []
        if proc.returncode != case.expect_rc:
            expectation_failures.append(f"exit code {proc.returncode} != expected {case.expect_rc}")
        if report.get("ok") != case.expect_ok:
            expectation_failures.append(f"ok={report.get('ok')!r} != expected {case.expect_ok!r}")
        break_id = ((report.get("break_at") or {}).get("artifact_id"))
        if case.expect_break_id and break_id != case.expect_break_id:
            expectation_failures.append(f"break id {break_id!r} != expected {case.expect_break_id!r}")
        if case.expect_warning_id:
            ids = {c.get("id") for c in report.get("checks", []) if c.get("status") == "warn"}
            if case.expect_warning_id not in ids:
                expectation_failures.append(f"missing expected warning {case.expect_warning_id}; got {sorted(ids)}")
        result["expectation_failures"] = expectation_failures
        result["passed"] = not expectation_failures
        return result


def write_summary(results: list[dict[str, Any]]) -> None:
    REVIEWS.mkdir(parents=True, exist_ok=True)
    for r in results:
        safe = r["case"]
        (REVIEWS / f"{safe}.json").write_text(json.dumps(r, ensure_ascii=False, indent=2) + "\n",
                                              encoding="utf-8")

    passed = sum(1 for r in results if r["passed"])
    lines = [
        "# External local-process review",
        "",
        f"Passed expectations: **{passed}/{len(results)}**",
        "",
        "Each case was staged from a published ZIP into a fresh temporary directory and",
        "verified by a new Python process using only files inside that staging directory.",
        "",
        "| Case | Exit | Expected | Result | Break / warning |",
        "|---|---:|---:|---|---|",
    ]
    for r in results:
        report = r.get("report") or {}
        bw = (report.get("break_at") or {}).get("artifact_id") if report else None
        warns = [c.get("id") for c in report.get("checks", []) if c.get("status") == "warn"] if report else []
        extra = bw or ", ".join(warns)
        status = "PASS EXPECTATION" if r["passed"] else "REVIEW FAILURE"
        lines.append(f"| {r['case']} | {r.get('exit_code', 'ERR')} | {r.get('expected_exit_code', '-')} | {status} | {extra} |")
        if not r["passed"]:
            for f in r.get("expectation_failures", []):
                lines.append(f"| | | | ↳ {f} | |")
    lines += ["", "## Verifier summaries", ""]
    for r in results:
        report = r.get("report") or {}
        lines.append(f"### {r['case']}")
        lines.append("")
        lines.append(f"- expectation: {'passed' if r['passed'] else 'FAILED'}")
        lines.append(f"- summary: {report.get('summary', 'reviewer exception; see expectation failures')}")
        if r.get("stderr"):
            lines.append(f"- stderr: `{r['stderr'][:300].replace(chr(10), ' ')}`")
        if r["expectation_failures"]:
            lines.append(f"- failures: {json.dumps(r['expectation_failures'], ensure_ascii=False)}")
        lines.append("")
    (REVIEWS / "EXTERNAL_REVIEW.md").write_text("\n".join(lines), encoding="utf-8")
    (REVIEWS / "external-review-summary.json").write_text(
        json.dumps({"passed": passed, "total": len(results), "results": results},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    results = []
    unexpected = False
    for case in cases():
        try:
            result = run_case(case)
        except Exception as exc:  # noqa: BLE001 - report and continue all cases
            result = {"case": case.name, "passed": False,
                      "expectation_failures": [f"reviewer exception: {type(exc).__name__}: {exc}"]}
        results.append(result)
        if result["passed"]:
            print(f"PASS {case.name}")
        else:
            unexpected = True
            print(f"FAIL {case.name}: {result.get('expectation_failures')}")
    write_summary(results)
    print(f"\nWrote {REVIEWS / 'EXTERNAL_REVIEW.md'}")
    return 1 if unexpected else 0


if __name__ == "__main__":
    raise SystemExit(main())
