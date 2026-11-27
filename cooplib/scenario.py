"""Build the fictional manuscripts, simulated witnesses, and real deliverables.

Running ``build_all`` creates:

* a mode-0700 private "cabinet" with simulated witness signing keys;
* public Ed25519 keys and a signed policy under ``deliverables/trust``;
* self-contained cross-library evidence bundles;
* lightweight registry renewal receipts;
* negative/late/tamper variants used by the independent review.

The witness receipts are simulation receipts, as permitted by the exercise.
They are nevertheless asymmetrically signed and independently verifiable.
"""
from __future__ import annotations

import os
import difflib
import shutil
import stat
from pathlib import Path
from typing import Any

from .common import (
    BUNDLE_DIR,
    CASES_DIR,
    DELIVERABLES,
    PRIVATE,
    READER_DIR,
    TRUST_DIR,
    export_public_key,
    generate_ed25519_key,
    parse_iso,
    read_json,
    sha256_file,
    write_json,
    write_signed_json,
)

SCENARIO_NOW = "2026-11-27T08:00:00Z"
OLD_WITNESS_END = "2026-11-20T23:59:59Z"
NEW_WITNESS_FROM = "2026-09-01T00:00:00Z"
SUMMER_CUTOFF = "2026-06-30T23:59:59Z"

AUTHORITIES: dict[str, dict[str, Any]] = {
    "coop-governance-2026": {
        "role": "policy-governance-key",
        "valid_from": "2026-01-01T00:00:00Z",
        "trust_not_after": None,
    },
    "old-witness-2023": {
        "role": "original-timestamp-witness",
        "valid_from": "2023-01-01T00:00:00Z",
        "trust_not_after": OLD_WITNESS_END,
        "replacement": "new-witness-2026",
    },
    "new-witness-2026": {
        "role": "successor-long-term-witness",
        "valid_from": NEW_WITNESS_FROM,
        "trust_not_after": None,
    },
    "registry-2026": {
        "role": "continuous-commitment-registry",
        "valid_from": "2025-01-01T00:00:00Z",
        "trust_not_after": None,
    },
    "clerk-li-2025": {
        "role": "contemporaneous-checking-clerk",
        "valid_from": "2025-01-01T00:00:00Z",
        "trust_not_after": None,
    },
    "owner-qixia": {
        "role": "manuscript-owner-custody-key",
        "valid_from": "2025-01-01T00:00:00Z",
        "trust_not_after": None,
    },
}

QIXIA_TEXT = """\
栖霞考·卷三（虚构档案）

县图旧在治平寺壁，岁久漫漶。甲辰春，得故纸于书簏，
山川道里与壁间残墨相合，因录为卷三，以存一时代之迹。

栖霞渡在县东十二里，渡北有碑，文曰“永安”，字半缺。
西岗旧有烽燧，今惟土阜；父老犹能指其处。

编者谨识。
"""

DENG_YING_A = """\
登瀛录·诗稿一页（虚构）

闲居少邻并，草径入荒园。
鸟宿池边树，僧推月下门。
过桥分野色，移石动云根。
暂去还来此，幽期不负言。
"""

DENG_YING_B = DENG_YING_A.replace("僧推月下门", "僧敲月下门")


# ------------------------------- setup/keys --------------------------------

def clean_generated() -> None:
    """Remove generated public cases/bundles while preserving directories."""
    for d in [BUNDLE_DIR, CASES_DIR, READER_DIR, TRUST_DIR]:
        if d.exists():
            for child in d.iterdir():
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
    keys = PRIVATE / "keys"
    if keys.exists():
        shutil.rmtree(keys)
    keys.mkdir(parents=True, exist_ok=True)
    for sub in ("witness-log", "registry"):
        d = PRIVATE / sub
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True, exist_ok=True)
    for name in ("clock.txt", "admin-token", "server-endpoint.json",
                 "online-flow-endpoint.json", "online-flow-result.json",
                 "online-flow-server.log"):
        p = PRIVATE / name
        if p.exists():
            p.unlink()


def ensure_authorities(force: bool = False) -> None:
    TRUST_DIR.mkdir(parents=True, exist_ok=True)
    PRIVATE.mkdir(parents=True, exist_ok=True)
    key_dir = PRIVATE / "keys"
    key_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(PRIVATE, 0o700)
    os.chmod(key_dir, 0o700)
    for sub in ("originals", "registry", "witness-log"):
        d = PRIVATE / sub
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(d, 0o700)
    key_map: dict[str, str] = {}
    anchors: dict[str, Any] = {}

    for key_id in AUTHORITIES:
        private = key_dir / f"{key_id}.pem"
        public = TRUST_DIR / f"{key_id}.pub.pem"
        if force or not private.exists():
            generate_ed25519_key(private)
        if force or not public.exists():
            export_public_key(private, public)
        key_map[key_id] = public.name
        anchors[key_id] = {
            "public_key": public.name,
            "sha256": sha256_file(public),
        }

    write_json(TRUST_DIR / "keys.json", key_map)
    write_json(TRUST_DIR / "anchors.json", anchors)
    fingerprint_lines = [
        "# Archive Cooperative public-key fingerprints",
        "# Distribute this file through a second/out-of-band channel in a real deployment.",
        "",
    ]
    for key_id, anchor in anchors.items():
        fingerprint_lines.append(f"{anchor['sha256']}  {anchor['public_key']}  # {key_id}")
    (TRUST_DIR / "FINGERPRINTS.txt").write_text("\n".join(fingerprint_lines) + "\n", encoding="utf-8")


def private_key(key_id: str) -> Path:
    return PRIVATE / "keys" / f"{key_id}.pem"


def build_policy() -> dict[str, Any]:
    data = {
        "type": "policy",
        "schema": "archive-coop-policy-data/1",
        "policy_id": "ACP-LT-2026-11",
        "generated_at": SCENARIO_NOW,
        "simulated_witness": True,
        "global_transition_cutoff": OLD_WITNESS_END,
        "example_existence_cutoff": SUMMER_CUTOFF,
        "authorities": AUTHORITIES,
        "rules": {
            "post_trust_stamp_cannot_backdate": True,
            "cross_library_requires_original_time_credentials": True,
            "registry_current_statement_alone_is_insufficient": True,
            "hash_algorithm": "sha-256",
        },
    }
    write_signed_json(DELIVERABLES / "policy.json", data,
                      "coop-governance-2026", private_key("coop-governance-2026"))
    return data


# ------------------------------ bundle helpers -----------------------------

def install_common_bundle_files(bundle: Path) -> None:
    bundle.mkdir(parents=True, exist_ok=True)
    shutil.copytree(TRUST_DIR, bundle / "trust", dirs_exist_ok=True)
    shutil.copy2(DELIVERABLES / "policy.json", bundle / "policy.json")
    verifier_src = DELIVERABLES / "verify_independent.py"
    shutil.copy2(verifier_src, bundle / "verify_independent.py")
    (bundle / "verify_independent.py").chmod(0o755)


def put_artifact(bundle: Path, rel: str, data: dict[str, Any], key_id: str) -> tuple[Path, str]:
    path = bundle / rel
    write_signed_json(path, data, key_id, private_key(key_id))
    return path, sha256_file(path)


def write_manifest(bundle: Path, manifest: dict[str, Any]) -> None:
    write_json(bundle / "manifest.json", manifest)


def zip_bundle(bundle: Path) -> Path:
    base = BUNDLE_DIR / bundle.name
    zip_path = Path(shutil.make_archive(str(base), "zip", root_dir=bundle.parent, base_dir=bundle.name))
    return zip_path


def artifact_entry(aid: str, rel: str, required: bool = True) -> dict[str, Any]:
    return {"id": aid, "path": rel, "required": required}


# --------------------------- cross-library case ----------------------------

def build_qixia_case(*, late: bool = False, post_trust: bool = False) -> Path:
    suffix = "-late-delivery" if late else ("-post-trust-stamp" if post_trust else "-crosslibrary")
    bundle = BUNDLE_DIR / f"qixia{suffix}"
    if bundle.exists():
        shutil.rmtree(bundle)
    install_common_bundle_files(bundle)

    manuscript_id = "QIXIA-KAO"
    edition = "v3"
    original_rel = f"originals/{manuscript_id}-{edition}.txt"
    original_path = bundle / original_rel
    original_path.parent.mkdir(parents=True, exist_ok=True)
    original_path.write_text(QIXIA_TEXT, encoding="utf-8")
    digest = sha256_file(original_path)

    # The private cabinet retains the cooperative's sealed working copy.
    private_original = PRIVATE / "originals" / f"{manuscript_id}-{edition}.txt"
    private_original.parent.mkdir(parents=True, exist_ok=True)
    private_original.write_text(QIXIA_TEXT, encoding="utf-8")

    old_rel = "artifacts/01-old-timestamp-receipt.json"
    old_data = {
        "type": "timestamp-receipt",
        "receipt_id": "OLD-QIXIA-20250314-001",
        "issued_at": "2025-03-14T09:20:00Z",
        "authority": "old-witness-2023",
        "manuscript_id": manuscript_id,
        "edition": edition,
        "whole_item_digested": True,
        "hash_algorithm": "sha-256",
        "digest": digest,
        "simulated_public_receipt": True,
    }
    _, old_hash = put_artifact(bundle, old_rel, old_data, "old-witness-2023")

    check_rel = "artifacts/02-contemporaneous-crosscheck.json"
    check_data = {
        "type": "contemporaneous-crosscheck",
        "check_id": "XCHECK-QIXIA-20250314-001",
        "issued_at": "2025-03-14T09:35:00Z",
        "clerk": "clerk-li-2025",
        "manuscript_id": manuscript_id,
        "edition": edition,
        "hash_algorithm": "sha-256",
        "digest": digest,
        "digest_recomputed": True,
        "old_receipt_signature_checked_with_public_key": True,
        "result": "match",
        "old_receipt": {"artifact_id": "old-receipt", "sha256": old_hash},
        "materials": [
            {"kind": "whole-manuscript-bytes", "path": original_rel, "sha256": digest},
            {"kind": "old-receipt-envelope", "path": old_rel, "sha256": old_hash},
        ],
    }
    _, check_hash = put_artifact(bundle, check_rel, check_data, "clerk-li-2025")

    if late:
        handover_time = "2026-11-21T09:15:00Z"
        migration_time = "2026-11-21T10:30:00Z"
        on_time = False
        variant = "owner-one-day-late"
    elif post_trust:
        handover_time = SCENARIO_NOW
        migration_time = SCENARIO_NOW
        on_time = False
        variant = "stamp-after-old-witness-untrusted"
    else:
        handover_time = "2026-11-18T14:00:00Z"
        migration_time = "2026-11-19T10:30:00Z"
        on_time = True
        variant = "timely-complete"

    handover_rel = "artifacts/03-owner-delivery.json"
    handover_data = {
        "type": "custody-handover",
        "handover_id": f"HANDOVER-QIXIA-{variant}",
        "issued_at": handover_time,
        "owner_key": "owner-qixia",
        "recipient": "new-witness-2026",
        "promised_material_deadline": OLD_WITNESS_END,
        "on_time": on_time,
        "manuscript_id": manuscript_id,
        "edition": edition,
        "digest": digest,
        "transferred_materials": ["whole manuscript bytes", "old timestamp receipt",
                                  "contemporaneous cross-check"],
        "custody_choice": "self-contained-migration-bundle",
        "owner_may_retain_original": True,
    }
    _, handover_hash = put_artifact(bundle, handover_rel, handover_data, "owner-qixia")

    migration_rel = "artifacts/04-successor-migration-attestation.json"
    migration_data = {
        "type": "migration-attestation",
        "migration_id": f"MIG-QIXIA-{variant}",
        "issued_at": migration_time,
        "successor_witness": "new-witness-2026",
        "relied_authority": "old-witness-2023",
        "manuscript_id": manuscript_id,
        "edition": edition,
        "digest": digest,
        "verified": {
            "reader_or_manuscript_digest_matches": True,
            "old_receipt_ed25519_signature_valid": True,
            "crosscheck_matches": True,
            "performed_before_old_trust_end": on_time,
        },
        "old_receipt": {"artifact_id": "old-receipt", "sha256": old_hash},
        "crosschecks": [{"artifact_id": "crosscheck-2025", "sha256": check_hash}],
        "handover": {"artifact_id": "owner-delivery", "sha256": handover_hash},
        "statement": (
            "Successor witness preserves the exact digest and original-time materials; "
            "this attestation is not a backdated claim that the successor saw the manuscript in 2025."
        ),
    }
    _, migration_hash = put_artifact(bundle, migration_rel, migration_data, "new-witness-2026")

    manifest = {
        "schema": "archive-coop-manifest/1",
        "case_id": f"QIXIA-KAO-{edition}-{variant}",
        "title": "Cross-library self-contained witness migration",
        "claim": "self-contained-bridge",
        "cutoff": OLD_WITNESS_END,
        "simulated": True,
        "custody_summary": {
            "owner": "may retain the original; delivered exact bytes before the bridge deadline in the happy path",
            "cooperative_and_new_witness": "holds signed migration record and sealed working copy",
            "foreign_library_or_stranger": "receives this complete bundle and can verify offline",
        },
        "original": {
            "path": original_rel,
            "required": True,
            "sha256": digest,
            "manuscript_id": manuscript_id,
            "edition": edition,
        },
        "policy": "policy.json",
        "trust_dir": "trust",
        "artifacts": [
            artifact_entry("old-receipt", old_rel),
            artifact_entry("crosscheck-2025", check_rel),
            artifact_entry("owner-delivery", handover_rel),
            artifact_entry("migration-2026", migration_rel),
        ],
        "artifact_hashes_for_operator": {"migration": migration_hash},
    }
    write_manifest(bundle, manifest)
    zip_bundle(bundle)
    return bundle


# ------------------------- one-character registry case ---------------------

def build_dengying_case() -> tuple[Path, Path]:
    READER_DIR.mkdir(parents=True, exist_ok=True)
    a_path = READER_DIR / "DENG-YING-v1-tui.txt"
    b_path = READER_DIR / "DENG-YING-v2-qiao.txt"
    a_path.write_text(DENG_YING_A, encoding="utf-8")
    b_path.write_text(DENG_YING_B, encoding="utf-8")

    # Private owner copies model owners who do not hand the original to readers.
    private_dir = PRIVATE / "originals"
    private_dir.mkdir(parents=True, exist_ok=True)
    (private_dir / a_path.name).write_text(DENG_YING_A, encoding="utf-8")
    (private_dir / b_path.name).write_text(DENG_YING_B, encoding="utf-8")

    diff = list(difflib.ndiff(DENG_YING_A.splitlines(), DENG_YING_B.splitlines()))
    changed = [line for line in diff if line.startswith("+ ") or line.startswith("- ")]
    write_json(READER_DIR / "ONE_CHARACTER_DIFF.json", {
        "description": "The two reader originals differ in exactly one Chinese character.",
        "changed_lines": changed,
        "sha256_v1_tui": sha256_file(a_path),
        "sha256_v2_qiao": sha256_file(b_path),
    })

    bundle = BUNDLE_DIR / "dengying-registry-timely"
    if bundle.exists():
        shutil.rmtree(bundle)
    install_common_bundle_files(bundle)

    manuscript_id = "DENG-YING-LU"
    edition = "v1-tui"
    digest_a = sha256_file(a_path)

    old_rel = "artifacts/01-old-receipt.json"
    old_data = {
        "type": "timestamp-receipt",
        "receipt_id": "OLD-DENGYING-20250510-007",
        "issued_at": "2025-05-10T08:00:00Z",
        "manuscript_id": manuscript_id,
        "edition": edition,
        "whole_item_digested": True,
        "hash_algorithm": "sha-256",
        "digest": digest_a,
    }
    _, old_hash = put_artifact(bundle, old_rel, old_data, "old-witness-2023")

    check_rel = "artifacts/02-crosscheck.json"
    check_data = {
        "type": "contemporaneous-crosscheck",
        "check_id": "XCHECK-DENGYING-20250510-007",
        "issued_at": "2025-05-10T08:20:00Z",
        "manuscript_id": manuscript_id,
        "edition": edition,
        "digest": digest_a,
        "result": "match",
        "old_receipt": {"artifact_id": "old-receipt", "sha256": old_hash},
    }
    _, check_hash = put_artifact(bundle, check_rel, check_data, "clerk-li-2025")

    schedule = [
        ("genesis", 0, "2025-05-12T09:00:00Z", "2025-11-30T23:59:59Z", "artifacts/03-registry-genesis.json"),
        ("renewal-1", 1, "2025-08-10T09:00:00Z", "2026-02-28T23:59:59Z", "artifacts/04-renewal-1.json"),
        ("renewal-2", 2, "2025-11-10T09:00:00Z", "2026-05-31T23:59:59Z", "artifacts/05-renewal-2.json"),
        ("renewal-3", 3, "2026-02-10T09:00:00Z", "2026-08-31T23:59:59Z", "artifacts/06-renewal-3.json"),
        ("renewal-4", 4, "2026-05-10T09:00:00Z", "2026-11-30T23:59:59Z", "artifacts/07-renewal-4-last.json"),
    ]
    artifact_ids = ["old-receipt", "crosscheck-2025"]
    chain_ids: list[str] = []
    prev_id: str | None = None
    prev_hash = "GENESIS"
    for aid, seq, issued, through, rel in schedule:
        data: dict[str, Any] = {
            "type": "renewal-receipt",
            "receipt_id": f"REG-DENGYING-{seq:03d}",
            "seq": seq,
            "issued_at": issued,
            "valid_through": through,
            "registry": "registry-2026",
            "manuscript_id": manuscript_id,
            "edition": edition,
            "hash_algorithm": "sha-256",
            "digest": digest_a,
            "commitment": f"sha256:{digest_a}",
            "previous_artifact_id": prev_id,
            "previous_envelope_sha256": prev_hash,
        }
        if seq == 0:
            data["genesis_of_continuous_registration"] = True
            data["old_receipt"] = {"artifact_id": "old-receipt", "sha256": old_hash}
        _, this_hash = put_artifact(bundle, rel, data, "registry-2026")
        artifact_ids.append(aid)
        chain_ids.append(aid)
        prev_id = aid
        prev_hash = this_hash

    manifest = {
        "schema": "archive-coop-manifest/1",
        "case_id": "DENG-YING-LU-v1-one-character-deadline",
        "title": "Lightweight continuous registry renewal",
        "claim": "renewal-chain",
        "cutoff": SUMMER_CUTOFF,
        "simulated": True,
        "custody_summary": {
            "owner": "keeps the manuscript; only hash commitments and lightweight receipts leave the owner's cabinet",
            "registry": "holds the continuous commitment record while in service",
            "reader": "must supply their own copy to bind the hash; may check the operating registry online",
        },
        "original": {
            "path": None,
            "required": True,
            "sha256": digest_a,
            "manuscript_id": manuscript_id,
            "edition": edition,
        },
        "policy": "policy.json",
        "trust_dir": "trust",
        "artifacts": [
            artifact_entry("old-receipt", old_rel),
            artifact_entry("crosscheck-2025", check_rel),
            *[artifact_entry(aid, rel) for (aid, _seq, _issued, _through, rel) in schedule],
        ],
        "renewal_chains": [{
            "id": "continuous-commitment",
            "artifacts": chain_ids,
            "deadline_anchor": "renewal-4",
            "must_cover_cutoff": True,
        }],
    }
    write_manifest(bundle, manifest)
    zip_bundle(bundle)
    return a_path, b_path


def build_late_renewal_mutation() -> Path:
    src = BUNDLE_DIR / "dengying-registry-timely"
    dst = BUNDLE_DIR / "dengying-late-renewal-after-deadline"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)

    last_rel = "artifacts/07-renewal-4-last.json"
    last_path = dst / last_rel
    env = read_json(last_path)
    data = env["data"]
    # This models asking the honest registry to issue/reissue the "same last
    # step" with the clock after the deadline.  Only issued_at changes in the
    # signed payload; the signature is necessarily newly produced.  Merely
    # editing the old signed date without re-signing is modeled separately.
    data["issued_at"] = "2026-07-02T09:00:00Z"
    write_signed_json(last_path, data, "registry-2026", private_key("registry-2026"))
    zip_bundle(dst)
    return dst


def build_tampered_renewal_date_variant() -> Path:
    """Naive date edit without re-signing: verifier must reject signature."""
    src = BUNDLE_DIR / "dengying-registry-timely"
    dst = BUNDLE_DIR / "dengying-renewal-date-edited-no-resign"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)

    last_path = dst / "artifacts/07-renewal-4-last.json"
    env = read_json(last_path)
    env["data"]["issued_at"] = "2026-07-02T09:00:00Z"
    env["tamper_note"] = "date changed after signing; signature_b64 intentionally left unchanged"
    write_json(last_path, env)
    zip_bundle(dst)
    return dst


def build_registry_closed_variant() -> Path:
    src = BUNDLE_DIR / "dengying-registry-timely"
    dst = BUNDLE_DIR / "dengying-registry-closed"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)

    closure_rel = "artifacts/08-registry-closure.json"
    closure_data = {
        "type": "registry-closure",
        "closure_id": "CLOSURE-REG-2026-1015",
        "issued_at": "2026-10-15T18:00:00Z",
        "effective_at": "2026-10-15T23:59:59Z",
        "registry": "registry-2026",
        "statement": "Registry ceased service; no new online statements or renewals after effective_at.",
        "historical_receipts_remain_verifiable_with_public_key": True,
    }
    _, closure_hash = put_artifact(dst, closure_rel, closure_data, "registry-2026")

    manifest = read_json(dst / "manifest.json")
    manifest["case_id"] = "DENG-YING-LU-v1-registry-closed"
    manifest["registry_online"] = False
    manifest["artifacts"].append(artifact_entry("registry-closure", closure_rel))
    write_manifest(dst, manifest)
    zip_bundle(dst)
    return dst


def build_qixia_registry_closed_variant() -> Path:
    """The same cross-library manuscript after the optional registry closes."""
    src = BUNDLE_DIR / "qixia-crosslibrary"
    dst = BUNDLE_DIR / "qixia-crosslibrary-registry-closed"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)

    closure_rel = "artifacts/05-registry-closure.json"
    closure_data = {
        "type": "registry-closure",
        "closure_id": "CLOSURE-REG-2026-1015-QIXIA",
        "issued_at": "2026-10-15T18:00:00Z",
        "effective_at": "2026-10-15T23:59:59Z",
        "registry": "registry-2026",
        "manuscript_id": "QIXIA-KAO",
        "statement": "Registry closed; the self-contained migration bundle remains independently verifiable.",
    }
    put_artifact(dst, closure_rel, closure_data, "registry-2026")
    manifest = read_json(dst / "manifest.json")
    manifest["case_id"] = "QIXIA-KAO-v3-crosslibrary-registry-closed"
    manifest["registry_online"] = False
    manifest["artifacts"].append(artifact_entry("registry-closure", closure_rel))
    write_manifest(dst, manifest)
    zip_bundle(dst)
    return dst


def build_registry_statement_only() -> Path:
    bundle = BUNDLE_DIR / "registry-statement-only"
    if bundle.exists():
        shutil.rmtree(bundle)
    install_common_bundle_files(bundle)
    digest_a = sha256_file(READER_DIR / "DENG-YING-v1-tui.txt")
    rel = "artifacts/only-today-statement.json"
    data = {
        "type": "registry-statement",
        "issued_at": SCENARIO_NOW,
        "as_of": SCENARIO_NOW,
        "registry": "registry-2026",
        "manuscript_id": "DENG-YING-LU",
        "edition": "v1-tui",
        "digest": digest_a,
        "historical_receipts_included": False,
        "statement": "Current registry screen says the commitment is valid, but history was not exported.",
    }
    put_artifact(bundle, rel, data, "registry-2026")
    manifest = {
        "schema": "archive-coop-manifest/1",
        "case_id": "registry-today-statement-only-insufficient",
        "title": "A current registry statement cannot replace point-in-time receipts",
        "claim": "registry-statement-only",
        "cutoff": SUMMER_CUTOFF,
        "original": {"path": None, "required": False, "sha256": digest_a},
        "policy": "policy.json",
        "trust_dir": "trust",
        "artifacts": [artifact_entry("registry-current-statement", rel)],
    }
    write_manifest(bundle, manifest)
    zip_bundle(bundle)
    return bundle


def publish_permissions() -> None:
    """Make the public delivery tree traversable/readable by another process."""

    def chmod_if_needed(path: Path, mode: int) -> None:
        try:
            os.chmod(path, mode)
        except PermissionError:
            # Some virtiofs/host mappings reject a redundant chmod.  If the
            # existing mode already contains every requested bit, that is fine.
            current = stat.S_IMODE(path.stat().st_mode)
            if (current & mode) != mode:
                raise

    for root, dirs, files in os.walk(DELIVERABLES):
        chmod_if_needed(Path(root), 0o755)
        for d in dirs:
            chmod_if_needed(Path(root) / d, 0o755)
        for name in files:
            p = Path(root) / name
            if name == "verify_independent.py" or p.suffix == ".sh":
                chmod_if_needed(p, 0o755)
            else:
                chmod_if_needed(p, 0o644)


def build_all(clean: bool = True) -> dict[str, Any]:
    if clean:
        clean_generated()
    ensure_authorities()
    build_policy()
    qixia_ok = build_qixia_case()
    qixia_late = build_qixia_case(late=True)
    qixia_post = build_qixia_case(post_trust=True)
    a, b = build_dengying_case()
    late_renewal = build_late_renewal_mutation()
    tampered_date = build_tampered_renewal_date_variant()
    closed = build_registry_closed_variant()
    qixia_closed = build_qixia_registry_closed_variant()
    statement_only = build_registry_statement_only()
    publish_permissions()
    return {
        "qixia_timely": qixia_ok,
        "qixia_late_delivery": qixia_late,
        "qixia_post_trust_stamp": qixia_post,
        "dengying_timely": BUNDLE_DIR / "dengying-registry-timely",
        "dengying_a": a,
        "dengying_b": b,
        "late_renewal": late_renewal,
        "tampered_renewal_date": tampered_date,
        "registry_closed": closed,
        "qixia_registry_closed": qixia_closed,
        "statement_only": statement_only,
    }
