from __future__ import annotations

import json
import shutil
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from .bundle import build_bundle
from .cabinet import read_events
from .gitutil import head_commit
from .hashing import canonical_hash, hash_python_tree, sha256_file
from .ledger import append_record, verify_chain
from .probe import audit_cabinets, cabinet_counts, inspect_entrypoint, run_lookup
from .seals import PUBLIC_KEY, ensure_keypair, seal_payload

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from bootstrap_branches import main as bootstrap_branches_main  # noqa: E402

WORKTREES = ROOT / ".worktrees"
RUNS = ROOT / ".runs"
CLASSROOMS = ROOT / "classrooms"
REPORTS = ROOT / "review-desk" / "reports"
ENDORSEMENTS = ROOT / "review-desk" / "endorsements"
CABINET_DIR = ROOT / "review-desk" / "cabinets"

EXPECTED_OLD_IDS = {
    "1001": "安装",
    "1002": "图像",
    "1003": "订正",
}


def wordlist_artifact(root: Path, rel: str) -> dict[str, Any]:
    path = root / rel
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        "path": rel,
        "sha256": sha256_file(path),
        "list_id": payload.get("list_id"),
        "version": str(payload.get("version")),
        "format": payload.get("format"),
        "promised": bool(payload.get("promised", False)),
    }


class CabinetService:
    def __init__(self, state_path: Path, cabinet_id: str):
        from .server import make_handler

        self.state_path = state_path
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state_path, cabinet_id))
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.url = f"http://127.0.0.1:{self.port}"

    def __enter__(self):
        self.thread.start()
        with urlopen(f"{self.url}/health", timeout=2) as response:
            assert response.status == 200
        return self

    def __exit__(self, exc_type, exc, tb):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _copy_tree(source: Path, target: Path) -> Path:
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git"))
    return target


def _check_old_id_cases(
    root: Path,
    entrypoint: str | None = None,
    *,
    wordlists: list[str] | None = None,
    env_extra: dict[str, str] | None = None,
    offline: bool = True,
) -> list[dict[str, Any]]:
    observations = []
    for word_id, expected in EXPECTED_OLD_IDS.items():
        run = run_lookup(
            root,
            word_id,
            mode="old-id",
            offline=offline,
            entrypoint=entrypoint,
            wordlists=wordlists,
            env_extra=env_extra,
        )
        parsed = run.get("json") or {}
        observations.append(
            {
                "word_id": word_id,
                "expected_canonical": expected,
                "returncode": run["returncode"],
                "status": parsed.get("status"),
                "observed_canonical": parsed.get("canonical"),
                "egress_reported_by_result": parsed.get("egress_performed"),
                "stderr": run["stderr"],
                "pass": run["returncode"] == 0 and parsed.get("status") == "ok" and parsed.get("canonical") == expected,
                "result": parsed,
            }
        )
    return observations


def ensure_branches() -> None:
    from .gitutil import branch_exists

    required = [
        ("maint/qianzi-native", WORKTREES / "qianzi" / "branch-manifest.json"),
        ("maint/bihe-sealed-component", WORKTREES / "bihe" / "branch-manifest.json"),
        ("trial/swapped-called-impl", WORKTREES / "swapped" / "candidate-manifest.json"),
    ]
    if any((not branch_exists(name)) or (not marker.exists()) for name, marker in required):
        bootstrap_branches_main()


def record_mainline() -> dict[str, Any]:
    ensure_keypair()
    commit = head_commit("main")
    entrypoint = "mojiao.reviewed_resolver:resolve"
    call_path = inspect_entrypoint(ROOT, entrypoint)
    artifacts = [
        {"path": "mojiao/reviewed_resolver.py", "sha256": sha256_file(ROOT / "mojiao/reviewed_resolver.py")},
        wordlist_artifact(ROOT, "wordlists/school-promised-modern-2.1.0.json"),
        {"path": "contracts/caller-modern-v2.json", "sha256": sha256_file(ROOT / "contracts/caller-modern-v2.json")},
    ]
    observations = []
    for value, mode, expected in [("W-1001", "modern-id", "安装"), ("1002", "old-id", "图像"), ("按装", "text", "安装")]:
        run = run_lookup(ROOT, value, mode=mode, wordlists=["wordlists/school-promised-modern-2.1.0.json"])
        parsed = run.get("json") or {}
        observations.append(
            {
                "input": value,
                "mode": mode,
                "expected": expected,
                "returncode": run["returncode"],
                "observed": parsed,
                "pass": run["returncode"] == 0 and parsed.get("canonical") == expected and parsed.get("egress_performed") is False,
            }
        )
    payload = {
        "case_id": "MAINLINE-1396",
        "branch": "main",
        "git_commit": commit,
        "patch_name": "fix(offline): resolve promised entries without external query",
        "actually_used_entrypoint": entrypoint,
        "call_path": call_path,
        "artifacts": artifacts,
        "caller_contract": json.loads((ROOT / "contracts/caller-modern-v2.json").read_text(encoding="utf-8")),
        "observations": observations,
        "observed_behavior": "Promised entries resolve locally by modern id, legacy id alias, and query text; result reports no egress.",
    }
    append_record("mainline_review", payload, case_id="MAINLINE-1396")
    endorsement = seal_payload(
        {
            "endorsement_id": "END-MAIN-1396",
            "case_id": "MAINLINE-1396",
            "issued_for": "main reviewed resolver and its reviewed dictionary",
            "git_commit": commit,
            "covers": artifacts,
            "claims": [
                "reviewed-source-matches-hash",
                "promised-local-resolution",
                "no-transport-in-reviewed-module",
            ],
            "does_not_cover": [
                "any native reimplementation",
                "old-id compatibility adapter",
                "modified or future word lists",
                "classroom packaging or installation",
            ],
        }
    )
    _write_json(ENDORSEMENTS / "END-MAIN-1396.json", endorsement)
    return {"review": payload, "endorsement": endorsement}


def evaluate_qianzi() -> dict[str, Any]:
    branch = "maint/qianzi-native"
    source = WORKTREES / "qianzi"
    commit = head_commit(branch)
    manifest = json.loads((source / "branch-manifest.json").read_text(encoding="utf-8"))
    entrypoint = manifest["entrypoint"]
    call_path = inspect_entrypoint(source, entrypoint)
    artifacts = [
        {"path": "mojiao/native_legacy.py", "sha256": sha256_file(source / "mojiao/native_legacy.py")},
        wordlist_artifact(source, "wordlists/school-promised-legacy-1.4.0.json"),
        {"path": "contracts/caller-legacy-wordid-v1.json", "sha256": sha256_file(source / "contracts/caller-legacy-wordid-v1.json")},
    ]
    python_tree = hash_python_tree(source)
    observations = _check_old_id_cases(source, entrypoint=entrypoint, wordlists=["wordlists/school-promised-legacy-1.4.0.json"])
    contract_error = run_lookup(source, "W-NEW-017", mode="old-id", wordlists=["wordlists/school-promised-legacy-1.4.0.json"])
    all_pass = all(item["pass"] for item in observations)
    payload = {
        "case_id": "QIANZI-BRANCH-1396",
        "branch": branch,
        "git_commit": commit,
        "merge_and_patch_name": manifest["patch_name"],
        "name_is_not_identity": True,
        "branch_manifest": manifest,
        "actually_used_entrypoint": entrypoint,
        "call_path": call_path,
        "artifacts": artifacts,
        "all_mojiao_python_files": python_tree,
        "caller_contract": json.loads((source / "contracts/caller-legacy-wordid-v1.json").read_text(encoding="utf-8")),
        "observations": observations,
        "outside_contract_modern_id_probe": {"returncode": contract_error["returncode"], "observed": contract_error.get("json")},
        "functional_pass": all_pass,
    }
    append_record("branch_revalidation", payload, case_id="QIANZI-BRANCH-1396")
    endorsement = seal_payload(
        {
            "endorsement_id": "END-QIANZI-BRANCH-1396",
            "case_id": "QIANZI-BRANCH-1396",
            "scope": "branch-only: maint/qianzi-native at the recorded commit",
            "git_commit": commit,
            "entrypoint": entrypoint,
            "covered_artifacts": artifacts,
            "covered_python_tree": python_tree,
            "caller_contract_id": "caller/legacy-wordid-v1",
            "claims": [
                "old-four-digit-ids-resolve",
                "all-school-promised-ids-pass-offline-probe",
                "native-implementation-revalidated",
            ],
            "does_not_cover": [
                "mainline END-MAIN-1396 source identity",
                "other branches",
                "future compatibility changes",
                "future word-list contents unless separately accepted",
            ],
            "main_endorsement_reused": "NONE_BECAUSE_IMPLEMENTATION_IS_NATIVE",
        }
    )
    _write_json(ENDORSEMENTS / "END-QIANZI-BRANCH-1396.json", endorsement)
    return {"review": payload, "endorsement": endorsement}


def evaluate_bihe(main_info: dict[str, Any]) -> dict[str, Any]:
    branch = "maint/bihe-sealed-component"
    source = WORKTREES / "bihe"
    commit = head_commit(branch)
    manifest = json.loads((source / "branch-manifest.json").read_text(encoding="utf-8"))
    main_resolver_hash = next(a["sha256"] for a in main_info["review"]["artifacts"] if a["path"] == "mojiao/reviewed_resolver.py")
    main_wordlist_hash = next(a["sha256"] for a in main_info["review"]["artifacts"] if "modern-2.1.0" in a["path"])
    branch_resolver_hash = sha256_file(source / "mojiao/reviewed_resolver.py")
    branch_wordlist_hash = sha256_file(source / "wordlists/school-promised-modern-2.1.0.json")
    verbatim_match = main_resolver_hash == branch_resolver_hash and main_wordlist_hash == branch_wordlist_hash
    entrypoint = manifest["entrypoint"]
    call_path = inspect_entrypoint(source, entrypoint)
    observations = _check_old_id_cases(source, entrypoint=entrypoint, wordlists=["wordlists/school-promised-modern-2.1.0.json"])
    compat_hash = sha256_file(source / "mojiao/compat_oldid.py")
    all_pass = verbatim_match and all(item["pass"] for item in observations)

    carry = seal_payload(
        {
            "endorsement_id": "CARRY-BIHE-VERBATIM-1396",
            "case_id": "BIHE-BRANCH-1396",
            "decision": "END-MAIN-1396 remains usable only for the byte-identical resolver and reviewed word list below.",
            "branch": branch,
            "git_commit": commit,
            "verbatim_match": verbatim_match,
            "inherited_endorsement_id": "END-MAIN-1396",
            "inherited_subject": [
                {"path": "mojiao/reviewed_resolver.py", "sha256": branch_resolver_hash},
                {"path": "wordlists/school-promised-modern-2.1.0.json", "sha256": branch_wordlist_hash},
            ],
            "not_inherited_by": ["mojiao/compat_oldid.py", "classroom packaging", "changed word lists"],
        }
    )
    composition = seal_payload(
        {
            "endorsement_id": "END-BIHE-COMPOSITION-1396",
            "case_id": "BIHE-BRANCH-1396",
            "scope": "branch-only composition at recorded commit; reviewed component inheritance is separately recorded",
            "git_commit": commit,
            "entrypoint": entrypoint,
            "compat_sha256": compat_hash,
            "claims": ["old-id-boundary-functional", "promised-entries-local"],
            "re_review_triggers": [
                "mojiao/compat_oldid.py hash changes",
                "caller contract changes",
                "word-list version/hash changes",
                "observed cabinet/egress behavior changes",
            ],
            "functional_pass": all_pass,
        }
    )
    _write_json(ENDORSEMENTS / "CARRY-BIHE-VERBATIM-1396.json", carry)
    _write_json(ENDORSEMENTS / "END-BIHE-COMPOSITION-1396.json", composition)
    payload = {
        "case_id": "BIHE-BRANCH-1396",
        "branch": branch,
        "git_commit": commit,
        "patch_name": manifest["patch_name"],
        "verbatim_match": verbatim_match,
        "actually_used_entrypoint": entrypoint,
        "call_path": call_path,
        "compat_sha256": compat_hash,
        "observations": observations,
        "functional_pass": all_pass,
        "endorsements": [carry["endorsement_id"], composition["endorsement_id"]],
    }
    append_record("branch_review", payload, case_id="BIHE-BRANCH-1396")
    return {"review": payload, "carry": carry, "composition": composition}


def swapped_impl_drill(qianzi: dict[str, Any]) -> dict[str, Any]:
    branch = "trial/swapped-called-impl"
    source = WORKTREES / "swapped"
    commit = head_commit(branch)
    entrypoint = "mojiao.other_native:resolve"
    call_path = inspect_entrypoint(source, entrypoint)
    observations = _check_old_id_cases(source, entrypoint=entrypoint)
    endorsed_native_hash = next(a["sha256"] for a in qianzi["review"]["artifacts"] if a["path"] == "mojiao/native_legacy.py")
    actual_module_hash = call_path.get("module_source_file_sha256")
    mismatch = actual_module_hash != endorsed_native_hash
    decision = {
        "case_id": "SWAPPED-IMPL-1396",
        "branch": branch,
        "git_commit": commit,
        "same_patch_name": True,
        "actually_used_entrypoint": entrypoint,
        "call_path": call_path,
        "endorsed_native_sha256": endorsed_native_hash,
        "actual_module_sha256": actual_module_hash,
        "hash_mismatch": mismatch,
        "functional_pass": all(x["pass"] for x in observations),
        "old_endorsement_transferable": False,
        "verdict": "REFUSE_OLD_ENDORSEMENT_SIGN_AS_IS",
        "required_next_step": "If this implementation ships, review it as a new actual call path and issue a new scoped statement.",
        "observations": observations,
    }
    append_record("negative_control", decision, case_id="SWAPPED-IMPL-1396")
    _write_json(REPORTS / "swapped-impl-refusal.json", decision)
    return decision


def build_and_install_school(qianzi: dict[str, Any]) -> dict[str, Any]:
    classroom = {
        "classroom_id": "XIAN-GE-ROOM-01",
        "display_name": "虚构·弦歌小学一号机房",
        "caller_contract": "caller/legacy-wordid-v1",
        "entrypoint": "mojiao.native_legacy:resolve",
        "network_policy": "deny",
        "external_lookup_enabled": False,
        "wordlist_globs": ["wordlists/*.json"],
        "promised_word_ids": list(EXPECTED_OLD_IDS),
    }
    bundle = build_bundle(
        source_root=WORKTREES / "qianzi",
        bundle_id="qianzi-school-1.0.0",
        classroom=classroom,
        endorsements=[qianzi["endorsement"]],
        include_wordlists=["wordlists/school-promised-legacy-1.4.0.json"],
    )
    install_dir = CLASSROOMS / "xian-ge-room-01"
    if install_dir.exists():
        shutil.rmtree(install_dir)
    shutil.copytree(bundle["directory"], install_dir, ignore=shutil.ignore_patterns("__pycache__"))
    return {"bundle": bundle, "install_dir": str(install_dir), "classroom": classroom}


def school_acceptance(install: dict[str, Any], update: dict[str, Any] | None = None) -> dict[str, Any]:
    install_dir = Path(install["install_dir"])
    cabinet_state = CABINET_DIR / "school-delivery.jsonl"
    if cabinet_state.exists():
        cabinet_state.unlink()
    counts = cabinet_counts([cabinet_state])

    # Start the mock receiver/service, prove it is up, then disconnect before use.
    with CabinetService(cabinet_state, "mock-school-cabinet") as service:
        service_up_url = service.url
    disconnected_url = service_up_url  # port is now closed after context exit

    observations = _check_old_id_cases(
        install_dir,
        env_extra={"MOJIAO_CABINET_URL": disconnected_url, "MOJIAO_MOCK_SERVICE_URL": disconnected_url},
    )
    cabinets = audit_cabinets([cabinet_state], counts)
    verify_proc = _run_verify(install_dir)
    passed = all(x["pass"] for x in observations) and cabinets["no_egress_observed"] and verify_proc["ok"]
    receipt = seal_payload(
        {
            "receipt_id": "DELIVERY-XIANGE-1396",
            "case_id": "SCHOOL-DELIVERY-1396",
            "classroom_id": install["classroom"]["classroom_id"],
            "installed_path": str(install_dir),
            "archive_sha256": install["bundle"]["archive_sha256"],
            "post_delivery_wordlist_update_sha256": (update or {}).get("record", {}).get("sha256"),
            "mock_service_started_then_disconnected": True,
            "disconnected_endpoint": disconnected_url,
            "required_word_ids": list(EXPECTED_OLD_IDS),
            "observations": observations,
            "cabinet_audit": cabinets,
            "bundle_verify": {k: verify_proc.get(k) for k in ("ok", "stdout", "returncode")},
            "verdict": "PASS_OFFLINE_ALL_PROMISED_LISTS" if passed else "FAIL",
        }
    )
    _write_json(REPORTS / "school-delivery-receipt.json", receipt)
    append_record("school_delivery", {"receipt_id": receipt["receipt_id"], "passed": passed, "cabinet": cabinets}, case_id="SCHOOL-DELIVERY-1396")
    return {"passed": passed, "receipt": receipt, "observations": observations, "cabinet_audit": cabinets, "verify": verify_proc}


def _run_verify(root: Path) -> dict[str, Any]:
    import subprocess
    import sys

    proc = subprocess.run([sys.executable, "verify.py"], cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        parsed = json.loads(proc.stdout)
    except json.JSONDecodeError:
        parsed = None
    return {"returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr, "json": parsed, "ok": proc.returncode == 0 and bool(parsed and parsed.get("ok"))}


def dictionary_update(install: dict[str, Any]) -> dict[str, Any]:
    install_dir = Path(install["install_dir"])
    update_source = ROOT / "updates/school-promised-modern-2.2.0.json"
    target = install_dir / "wordlists/school-promised-modern-2.2.0.json"
    shutil.copy2(update_source, target)
    # Remove the old 2.1 file if a prior demo copied it; delivered package had legacy only.
    old_modern = install_dir / "wordlists/school-promised-modern-2.1.0.json"
    if old_modern.exists():
        old_modern.unlink()

    versions = run_lookup  # placeholder to keep imports obvious
    list_proc = _module_cli(install_dir, ["wordlists"])
    existing = _check_old_id_cases(install_dir)
    new_old_id = run_lookup(install_dir, "1004", mode="old-id")
    modern_only = run_lookup(install_dir, "W-NEW-017", mode="old-id")
    code_hashes = [
        {"path": p.relative_to(install_dir).as_posix(), "sha256": sha256_file(p)}
        for p in sorted((install_dir / "mojiao").glob("*.py"))
    ]
    update_record = seal_payload(
        {
            "update_id": "WORDLIST-UPDATE-220-1396",
            "case_id": "INDEPENDENT-WORDLIST-UPDATE",
            "installed_dictionary": "school-promised-modern-2.2.0.json",
            "sha256": sha256_file(target),
            "loader_code_unchanged_hashes": code_hashes,
            "old_ids_still_pass": all(x["pass"] for x in existing),
            "new_old_id_1004": (new_old_id.get("json") or {}),
            "modern_only_id_under_legacy_contract": (modern_only.get("json") or {}),
            "promise_boundary": "Independent data update can add entries; only ids recognized by caller/legacy-wordid-v1 are addressable by the old classroom.",
        }
    )
    _write_json(REPORTS / "wordlist-update-220.json", update_record)
    append_record("wordlist_update", {"update_id": update_record["update_id"], "sha256": update_record["sha256"]}, case_id="INDEPENDENT-WORDLIST-UPDATE")
    return {
        "record": update_record,
        "wordlists_stdout": list_proc["stdout"],
        "existing_observations": existing,
        "new_old_id_observation": new_old_id.get("json"),
        "modern_only_observation": modern_only.get("json"),
    }


def _module_cli(root: Path, args: list[str]) -> dict[str, Any]:
    import os
    import subprocess
    import sys

    env = dict(os.environ)
    env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run([sys.executable, "-m", "mojiao.cli", "--bundle-root", str(root), *args], cwd=root, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return {"returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}


def missing_wordlist_negative(install: dict[str, Any]) -> dict[str, Any]:
    negative_dir = CLASSROOMS / "negative-missing-target-wordlist"
    if negative_dir.exists():
        shutil.rmtree(negative_dir)
    shutil.copytree(install["bundle"]["directory"], negative_dir, ignore=shutil.ignore_patterns("__pycache__"))

    # Explicitly replay the two-step temptation: swap the called file, restore
    # the endorsed implementation byte-for-byte, then remove only the word list.
    called_native = negative_dir / "mojiao" / "native_legacy.py"
    restored_bytes = called_native.read_bytes()
    restored_hash = sha256_file(called_native)
    shutil.copy2(ROOT / "lab/overlays/swapped/mojiao/other_native.py", called_native)
    swapped_in_place_hash = sha256_file(called_native)
    called_native.write_bytes(restored_bytes)
    final_implementation_hash = sha256_file(called_native)

    removed = negative_dir / "wordlists/school-promised-legacy-1.4.0.json"
    removed.unlink()
    source_verify = _run_verify(Path(install["bundle"]["directory"]))
    verify = _run_verify(negative_dir)
    observations = _check_old_id_cases(negative_dir)
    original_archive_verify = {
        "archive": install["bundle"]["archive"],
        "archive_sha256": install["bundle"]["archive_sha256"],
        "publisher_signature_process": "MANIFEST signature remains verifiable inside the untouched source bundle",
    }
    decision = {
        "case_id": "MISSING-WORDLIST-1396",
        "sequence": "Swapped the actually called native_legacy.py, refused that old endorsement, restored the endorsed bytes, then removed only the target classroom's recognizable word list.",
        "negative_dir": str(negative_dir),
        "swapped_in_place_hash": swapped_in_place_hash,
        "restored_implementation_hash": final_implementation_hash,
        "implementation_restored_to_endorsed_hash": final_implementation_hash == restored_hash,
        "original_archive_provenance": "AUTHENTIC" if source_verify.get("ok") else "CHECK_FAILED",
        "original_archive": original_archive_verify,
        "untouched_source_bundle_verify": {"returncode": source_verify["returncode"], "ok": source_verify.get("ok"), "report": source_verify.get("json")},
        "installed_copy_verify": {"returncode": verify["returncode"], "report": verify["json"]},
        "observations": observations,
        "provenance_alone_passes_delivery": False,
        "verdict": "FAIL_OFFLINE_DELIVERY_OLD_ID_COVERAGE",
    }
    _write_json(REPORTS / "missing-wordlist-negative.json", decision)
    append_record("negative_control", {"case_id": decision["case_id"], "verdict": decision["verdict"]}, case_id=decision["case_id"])
    return decision


def leaky_drill() -> dict[str, Any]:
    source = RUNS / "leaky-compat"
    _copy_tree(WORKTREES / "bihe", source)
    shutil.copy2(ROOT / "lab/overlays/leaky/mojiao/leaky_compat.py", source / "mojiao/leaky_compat.py")
    state = CABINET_DIR / "leaky-drill.jsonl"
    if state.exists():
        state.unlink()
    counts = cabinet_counts([state])
    with CabinetService(state, "mock-leaky-receiving-cabinet") as service:
        observations = _check_old_id_cases(
            source,
            entrypoint="mojiao.leaky_compat:resolve",
            wordlists=["wordlists/school-promised-modern-2.1.0.json"],
            env_extra={"MOJIAO_CABINET_URL": service.url},
            offline=False,
        )
    audit = audit_cabinets([state], counts)
    decision = {
        "case_id": "LEAKY-COMPAT-1396",
        "actually_used_entrypoint": "mojiao.leaky_compat:resolve",
        "local_outputs_all_correct": all(x["pass"] for x in observations),
        "all_results_self_report_no_egress": all(x["egress_reported_by_result"] is False for x in observations),
        "observations": observations,
        "cabinet_audit": audit,
        "no_exfiltration_statement_allowed": False,
        "verdict": "FUNCTIONAL_PASS_BUT_REFUSE_NO_EGRESS_OR_NON_DISCLOSURE_ENDORSEMENT",
    }
    _write_json(REPORTS / "leaky-compat-refusal.json", decision)
    append_record("negative_control", decision, case_id="LEAKY-COMPAT-1396")
    return decision


def write_cabinet_registry() -> Path:
    registry = {
        "note": "Every mock receiving cabinet used by the exercise is listed here and audited by reviewdesk cabinet query --all.",
        "cabinets": [
            {"cabinet_id": "mock-school-cabinet", "state": str(CABINET_DIR / "school-delivery.jsonl")},
            {"cabinet_id": "mock-leaky-receiving-cabinet", "state": str(CABINET_DIR / "leaky-drill.jsonl")},
        ],
    }
    path = CABINET_DIR / "registry.json"
    _write_json(path, registry)
    return path


def run_all() -> dict[str, Any]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    ENDORSEMENTS.mkdir(parents=True, exist_ok=True)
    CABINET_DIR.mkdir(parents=True, exist_ok=True)
    ensure_branches()
    main_info = record_mainline()
    qianzi = evaluate_qianzi()
    bihe = evaluate_bihe(main_info)
    swapped = swapped_impl_drill(qianzi)
    install = build_and_install_school(qianzi)
    update = dictionary_update(install)
    acceptance = school_acceptance(install, update)
    missing = missing_wordlist_negative(install)
    leaky = leaky_drill()
    registry = write_cabinet_registry()
    chain = verify_chain()

    report = {
        "exercise": "maintenance-port-endorsement-and-review-desk",
        "decision": {
            "question": "维护分支可以沿用哪一张发布背书？",
            "answer": "只能沿用其明确覆盖、且实际调用路径的模块/词表/契约哈希完全一致的背书；同名补丁或合并记录不转移信任。",
            "qianzi": "千字社走原生重写：不能照签 END-MAIN-1396；重验后只签发仅覆盖 maint/qianzi-native 指定提交的 END-QIANZI-BRANCH-1396。",
            "bihe": "碧荷社走原样封存：END-MAIN-1396 仅能通过 CARRY-BIHE-VERBATIM-1396 沿用于哈希一致的 reviewed_resolver.py 与 2.1.0 词表；compat 边界和组合包由 END-BIHE-COMPOSITION-1396 覆盖，边界改版须重审。",
            "school_choice": "XIAN-GE-ROOM-01 安装千字社原生分支包，因为它保住四位旧词号，并允许词表独立更新。",
        },
        "mainline_observation_pass": all(o["pass"] for o in main_info["review"]["observations"]),
        "qianzi_pass": qianzi["review"]["functional_pass"],
        "bihe_pass": bihe["review"]["functional_pass"],
        "swapped_verdict": swapped["verdict"],
        "school_acceptance_pass": acceptance["passed"],
        "wordlist_update": {
            "sha256": update["record"]["sha256"],
            "old_ids_still_pass": update["record"]["old_ids_still_pass"],
            "new_old_id_status": (update["new_old_id_observation"] or {}).get("status"),
            "modern_only_status_under_legacy_contract": (update["modern_only_observation"] or {}).get("status"),
        },
        "missing_wordlist_verdict": missing["verdict"],
        "leaky_verdict": leaky["verdict"],
        "leaky_cabinet_events": leaky["cabinet_audit"]["total_new_events"],
        "ledger_chain": chain,
        "cabinet_registry": str(registry),
        "artifacts": {
            "school_bundle": install["bundle"],
            "install_dir": install["install_dir"],
            "reports": str(REPORTS),
            "endorsements": str(ENDORSEMENTS),
        },
    }
    _write_json(REPORTS / "final-report.json", report)
    markdown = render_markdown(report, acceptance, leaky, missing, swapped, update)
    (REPORTS / "final-report.md").write_text(markdown, encoding="utf-8")
    append_record("final_report", {"report_hash": canonical_hash(report), "school_pass": acceptance["passed"]}, case_id="FINAL-1396")
    return report


def render_markdown(report, acceptance, leaky, missing, swapped, update) -> str:
    chain = report["ledger_chain"]
    chain_text = f"ok={chain.get('ok')}, records={chain.get('records')}, head={chain.get('head')}"
    return f"""# 维护分支移植与复核台报告

## 结论

{report['decision']['answer']}

- {report['decision']['qianzi']}
- {report['decision']['bihe']}
- 学校交付选择：{report['decision']['school_choice']}

## 正向交付

- 教室：`XIAN-GE-ROOM-01`（虚构·弦歌小学一号机房）
- 包：`{report['artifacts']['school_bundle']['directory']}`
- 压缩包：`{report['artifacts']['school_bundle']['archive']}`
- 压缩包 SHA-256：`{report['artifacts']['school_bundle']['archive_sha256']}`
- 断服务离线验收：`{'通过' if report['school_acceptance_pass'] else '未通过'}`
- 接收柜新事件数：`{acceptance['cabinet_audit']['total_new_events']}`

复核者可运行：

```sh
cd {report['artifacts']['install_dir']}
python3 verify.py --human
./ww lookup 1001 --human
```

## 交付后独立词表更新

- 新版词表 SHA-256：`{report['wordlist_update']['sha256']}`
- 旧词号 1001-1003 仍通过：`{report['wordlist_update']['old_ids_still_pass']}`
- 新增旧词号 1004 状态：`{report['wordlist_update']['new_old_id_status']}`
- 新版专用 `W-NEW-017` 在旧词号契约下状态：`{report['wordlist_update']['modern_only_status_under_legacy_contract']}`

这显示“加载器/旧词号契约”与“新版独立词表内容”是两类不同承诺；未来词表内容不被旧背书自动覆盖，须记录哈希并重新跑行为探针。

## 反向演练

1. **偷换实际入口实现**：{swapped['verdict']}
   - 功能探针通过：`{swapped['functional_pass']}`；但实际模块哈希与背书哈希不一致，旧背书不能照签。
2. **来历真实但撤走目标词表**：{missing['verdict']}
   - 原始压缩包来历仍真实，但安装副本的旧词号探针失败；来源证明不能代替离线覆盖验收。
3. **本地答案正确、兼容层投递接收柜**：{leaky['verdict']}
   - 本地输出全部正确：`{leaky['local_outputs_all_correct']}`；返回值还都自称无外传：`{leaky['all_results_self_report_no_egress']}`
   - 接收柜新增事件：`{leaky['cabinet_audit']['total_new_events']}`
   - 因此不能签署“不外传”。

## 账本

- 哈希链校验：`{chain_text}`
- 柜登记：`{report['cabinet_registry']}`
- 单独查柜：`python3 -m reviewdesk.cli cabinet query --all`
"""
