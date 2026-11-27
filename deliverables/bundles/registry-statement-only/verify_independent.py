#!/usr/bin/env python3
"""Independent verifier for Archive Cooperative evidence bundles.

This file is intentionally self-contained: it imports only the Python standard
library and invokes OpenSSL for Ed25519 verification.  It never imports the
cooperative's private cabinet code or reads a hard-coded /workspace path.

Typical use:
    python3 verify_independent.py BUNDLE_DIR \
        [--original READER_COPY] [--before ISO_DATE] [--json]

Exit status:
    0  the requested claim is verified
    2  evidence is present but the claim cannot be verified
    3  command/environment error
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ----------------------------- small utilities -----------------------------

def eprint(msg: str) -> None:
    print(msg, file=sys.stderr)


def parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def canonical_bytes(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def openssl_bin() -> str:
    exe = shutil.which("openssl")
    if not exe:
        raise RuntimeError("openssl executable was not found on PATH")
    return exe


def verify_ed25519(data: Any, signature_b64: str, pub_path: Path) -> tuple[bool, str]:
    if not pub_path.is_file():
        return False, f"public key file is missing: {pub_path}"
    try:
        sig = base64.b64decode(signature_b64, validate=True)
    except Exception as exc:  # noqa: BLE001
        return False, f"signature is not valid base64: {exc}"

    with tempfile.TemporaryDirectory(prefix="independent-verify-") as td:
        root = Path(td)
        in_file = root / "canonical.json"
        sig_file = root / "signature.bin"
        in_file.write_bytes(canonical_bytes(data))
        sig_file.write_bytes(sig)
        proc = subprocess.run(
            [openssl_bin(), "pkeyutl", "-verify", "-pubin", "-inkey",
             str(pub_path), "-rawin", "-in", str(in_file),
             "-sigfile", str(sig_file)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if proc.returncode == 0:
            return True, "valid Ed25519 signature"
        detail = (proc.stderr or proc.stdout).decode("utf-8", "replace").strip()
        return False, detail or "OpenSSL rejected signature"


# ------------------------------- report model -------------------------------

class Report:
    def __init__(self, bundle: Path, original: Path | None, now: datetime):
        self.bundle = bundle
        self.original = original
        self.now = now
        self.checks: list[dict[str, Any]] = []
        self.break_at: dict[str, Any] | None = None
        self.earliest_anchor: dict[str, Any] | None = None
        self.ok = False
        self.summary = ""

    def add(self, check_id: str, status: str, message: str, **extra: Any) -> None:
        item = {"id": check_id, "status": status, "message": message}
        item.update(extra)
        self.checks.append(item)

    def fail(self, check_id: str, message: str, *, break_at: dict[str, Any] | None = None,
             **extra: Any) -> None:
        self.add(check_id, "fail", message, **extra)
        if break_at and self.break_at is None:
            self.break_at = break_at

    def warn(self, check_id: str, message: str, **extra: Any) -> None:
        self.add(check_id, "warn", message, **extra)

    def pass_(self, check_id: str, message: str, **extra: Any) -> None:
        self.add(check_id, "pass", message, **extra)

    def to_dict(self) -> dict[str, Any]:
        failures = [c for c in self.checks if c["status"] == "fail"]
        return {
            "ok": self.ok,
            "summary": self.summary,
            "bundle": str(self.bundle),
            "reader_original": str(self.original) if self.original else None,
            "verifier_now": self.now.isoformat().replace("+00:00", "Z"),
            "earliest_admissible_anchor": self.earliest_anchor,
            "break_at": self.break_at,
            "failure_count": len(failures),
            "failures": failures,
            "checks": self.checks,
        }

    def print_text(self) -> None:
        print("=" * 78)
        print("Archive Cooperative independent verification")
        print("=" * 78)
        print(f"Bundle   : {self.bundle}")
        if self.original:
            print(f"Original : {self.original}")
        if self.earliest_anchor:
            a = self.earliest_anchor
            print(f"Earliest : {a.get('time')} via {a.get('artifact_id')} ({a.get('kind')})")
        if self.break_at:
            b = self.break_at
            print(f"BREAK    : {b.get('artifact_id')} {b.get('path', '')}")
            print(f"           {b.get('reason')}")
        print("-" * 78)
        icons = {"pass": "[PASS]", "fail": "[FAIL]", "warn": "[WARN]", "info": "[INFO]"}
        for c in self.checks:
            print(f"{icons.get(c['status'], '[....]')} {c['id']}: {c['message']}")
        print("-" * 78)
        print(self.summary)


# ------------------------------- verification -------------------------------

class Verifier:
    def __init__(self, bundle: Path, args: argparse.Namespace, report: Report):
        self.bundle = bundle.resolve()
        self.args = args
        self.r = report
        self.manifest: dict[str, Any] = {}
        self.policy: dict[str, Any] = {}
        self.policy_envelope: dict[str, Any] = {}
        self.artifacts: dict[str, dict[str, Any]] = {}
        self.envelopes: dict[str, dict[str, Any]] = {}
        self.datas: dict[str, dict[str, Any]] = {}
        self.file_hashes: dict[str, str] = {}
        self.key_map: dict[str, str] = {}
        self.anchors: dict[str, Any] = {}
        self.actual_digest: str | None = None
        self.cutoff: datetime | None = parse_iso(args.before) if args.before else None

    def path(self, rel: str | None) -> Path | None:
        if rel is None:
            return None
        p = Path(rel)
        return p if p.is_absolute() else (self.bundle / p)

    def run(self) -> bool:
        try:
            self.load_inputs()
            if not self.r.checks or not any(c["id"] == "bundle-shape" and c["status"] == "fail" for c in self.r.checks):
                self.verify_trust_anchors()
                self.hash_original()
                self.verify_policy()
                self.verify_all_artifacts()
                self.check_typed_relationships()
                self.evaluate_claim()
        except Exception as exc:  # noqa: BLE001 - CLI should produce a report
            self.r.fail("verifier-exception", f"{type(exc).__name__}: {exc}")
            self.r.summary = "ERROR: verifier could not complete; this is not a valid proof."
            self.r.ok = False
        return self.r.ok

    def load_inputs(self) -> None:
        manifest_path = self.bundle / "manifest.json"
        if not self.bundle.is_dir():
            self.r.fail("bundle-shape", f"bundle directory does not exist: {self.bundle}")
            return
        if not manifest_path.is_file():
            self.r.fail("bundle-shape", "manifest.json is missing; refusing to guess evidence order")
            return
        try:
            self.manifest = load_json(manifest_path)
        except Exception as exc:  # noqa: BLE001
            self.r.fail("bundle-shape", f"manifest.json is invalid JSON: {exc}")
            return

        if self.cutoff is None:
            self.cutoff = parse_iso(self.manifest.get("cutoff"))
        if self.cutoff is None:
            self.cutoff = parse_iso((self.manifest.get("claim_spec") or {}).get("cutoff"))

        trust_dir = self.path(self.manifest.get("trust_dir", "trust"))
        assert trust_dir is not None
        keys_json = trust_dir / "keys.json"
        if keys_json.is_file():
            try:
                self.key_map = load_json(keys_json)
            except Exception as exc:  # noqa: BLE001
                self.r.fail("trust-keys-json", f"trust/keys.json invalid: {exc}")
        anchors_json = trust_dir / "anchors.json"
        if anchors_json.is_file():
            try:
                self.anchors = load_json(anchors_json)
            except Exception as exc:  # noqa: BLE001
                self.r.warn("trust-anchors-json", f"anchors.json invalid: {exc}")

        for entry in self.manifest.get("artifacts", []):
            aid = entry.get("id")
            rel = entry.get("path")
            if not aid or not rel:
                self.r.fail("manifest-artifact", f"artifact entry lacks id/path: {entry!r}")
                continue
            p = self.path(rel)
            assert p is not None
            self.artifacts[aid] = {**entry, "abs_path": p}
            if not p.is_file():
                if entry.get("required", True):
                    self.r.fail("artifact-missing", f"{aid}: missing file {rel}",
                                artifact_id=aid, path=rel)
                continue
            self.file_hashes[aid] = sha256_file(p)
            try:
                env = load_json(p)
                self.envelopes[aid] = env
                data = env.get("data")
                if not isinstance(data, dict):
                    raise ValueError("signed envelope has no object-valued data field")
                self.datas[aid] = data
            except Exception as exc:  # noqa: BLE001
                self.r.fail("artifact-json", f"{aid}: cannot load signed envelope: {exc}",
                            artifact_id=aid, path=rel)

        policy_ref = self.manifest.get("policy", "policy.json")
        pp = self.path(policy_ref)
        assert pp is not None
        if pp.is_file():
            try:
                self.policy_envelope = load_json(pp)
                data = self.policy_envelope.get("data")
                if isinstance(data, dict):
                    self.policy = data
                    self.datas["policy"] = data
                    self.envelopes["policy"] = self.policy_envelope
                    self.file_hashes.setdefault("policy", sha256_file(pp))
                else:
                    self.r.fail("policy-shape", "policy envelope has no object-valued data")
            except Exception as exc:  # noqa: BLE001
                self.r.fail("policy-json", f"cannot load policy: {exc}")
        else:
            self.r.fail("policy-missing", f"policy file not found: {policy_ref}")

        if not any(c["id"] in {"bundle-shape", "artifact-missing", "artifact-json"} and c["status"] == "fail"
                   for c in self.r.checks):
            self.r.pass_("bundle-shape",
                         f"manifest lists {len(self.artifacts)} artifacts and all required files are readable")

    def public_key_path(self, key_id: str) -> Path:
        trust_dir = self.path(self.manifest.get("trust_dir", "trust"))
        assert trust_dir is not None
        rel = self.key_map.get(key_id, f"{key_id}.pub.pem")
        p = Path(rel)
        return p if p.is_absolute() else trust_dir / p

    def verify_trust_anchors(self) -> None:
        if not self.anchors:
            self.r.warn("trust-anchor-distribution",
                        "no anchors.json; public keys are present but their fingerprints were not pinned")
            return
        bad = 0
        checked = 0
        for key_id, anchor in self.anchors.items():
            expected = anchor.get("sha256") if isinstance(anchor, dict) else None
            p = self.public_key_path(key_id)
            if not p.is_file():
                self.r.fail("trust-anchor-key-missing", f"{key_id}: {p}")
                bad += 1
                continue
            actual = sha256_file(p)
            checked += 1
            if expected and actual != expected:
                bad += 1
                self.r.fail("trust-anchor-fingerprint",
                            f"{key_id}: public key fingerprint differs from anchors.json",
                            expected=expected, actual=actual)
        if bad == 0:
            self.r.pass_("trust-anchor-fingerprint",
                         f"{checked} pinned public key(s) match anchors.json; "
                         "the anchor file itself must still be distributed out of band")

    def hash_original(self) -> None:
        original_arg = Path(self.args.original).resolve() if self.args.original else None
        if original_arg:
            self.r.original = original_arg
        elif self.manifest.get("original", {}).get("path"):
            original_arg = self.path(self.manifest["original"]["path"])
            self.r.original = original_arg
        else:
            self.r.original = None

        if original_arg is None:
            self.r.warn("original-not-supplied",
                        "lightweight receipt only: no reader original was supplied, so digest cannot be bound to content")
            return
        if not original_arg.is_file():
            self.r.fail("original-missing", f"reader original does not exist: {original_arg}")
            return
        self.actual_digest = sha256_file(original_arg)
        expected = (self.manifest.get("original") or {}).get("sha256")
        if expected and expected != self.actual_digest:
            self.r.fail("manifest-original-digest",
                        "reader original does not match manifest digest",
                        expected=expected, actual=self.actual_digest,
                        break_at={"artifact_id": "reader-original",
                                  "path": str(original_arg),
                                  "reason": "SHA-256 mismatch with manifest"})
        else:
            self.r.pass_("original-hash", f"SHA-256 {self.actual_digest}")

    def authority(self, key_id: str) -> dict[str, Any] | None:
        return (self.policy.get("authorities") or {}).get(key_id)

    def verify_envelope_signature(self, aid: str, envelope: dict[str, Any]) -> bool:
        key_id = envelope.get("key_id")
        data = envelope.get("data")
        sig = envelope.get("signature_b64")
        if envelope.get("schema") != "archive-coop-envelope/1":
            self.r.fail("envelope-schema", f"{aid}: unsupported envelope schema {envelope.get('schema')!r}",
                        artifact_id=aid)
            return False
        if not key_id or not isinstance(data, dict) or not sig:
            self.r.fail("envelope-shape", f"{aid}: missing key_id/data/signature_b64", artifact_id=aid)
            return False

        pub = self.public_key_path(key_id)
        ok, detail = verify_ed25519(data, sig, pub)
        if not ok:
            path = self.artifacts.get(aid, {}).get("path")
            self.r.fail("signature-invalid", f"{aid}: {detail}", artifact_id=aid, path=path,
                        key_id=key_id,
                        break_at={"artifact_id": aid, "path": path,
                                  "reason": "signature does not verify against trusted public key"})
            return False

        auth = self.authority(key_id)
        if auth is None:
            self.r.fail("authority-unknown",
                        f"{aid}: signed by {key_id}, which is not present in policy",
                        artifact_id=aid, key_id=key_id)
            return False

        issued = parse_iso(data.get("issued_at") or data.get("generated_at") or data.get("effective_at"))
        valid_from = parse_iso(auth.get("valid_from"))
        trust_after = parse_iso(auth.get("trust_not_after"))
        if issued and valid_from and issued < valid_from:
            self.r.fail("authority-not-yet-valid",
                        f"{aid}: {key_id} signature time {issued.isoformat()} precedes validity from {valid_from.isoformat()}",
                        artifact_id=aid)
            return False
        if issued and trust_after and issued > trust_after:
            path = self.artifacts.get(aid, {}).get("path")
            self.r.fail("authority-signature-after-trust-end",
                        f"{aid}: {key_id} statement time {issued.isoformat()} is after its admissibility end "
                        f"{trust_after.isoformat()}; a later signature cannot be treated as a valid old-regime act",
                        artifact_id=aid, path=path,
                        break_at={"artifact_id": aid, "path": path,
                                  "reason": "statement was made after this authority's trust_not_after"})
            return False
        # An old authority may have made a valid receipt during its trusted
        # interval.  Whether it can be relied on *now* is evaluated by the
        # bridge claim, not by pretending the old signature is invalid.
        self.r.pass_("signature-valid", f"{aid}: {detail} by {key_id}", artifact_id=aid, key_id=key_id)
        return True

    def verify_policy(self) -> None:
        if not self.policy:
            return
        env = self.policy_envelope
        key_id = env.get("key_id")
        if key_id:
            ok = self.verify_envelope_signature("policy", env)
            if ok:
                self.r.pass_("policy-signature", f"policy signed by {key_id}")
        else:
            self.r.warn("policy-unsigned", "policy is not signed; only use with an out-of-band trusted copy")

    def verify_all_artifacts(self) -> None:
        for aid, env in list(self.envelopes.items()):
            if aid == "policy":
                continue
            entry = self.artifacts.get(aid, {})
            if entry.get("skip_signature"):
                continue
            self.verify_envelope_signature(aid, env)

    def digest_matches_original(self, aid: str, data: dict[str, Any]) -> bool | None:
        """Return True if statement binds the reader's exact bytes."""
        claimed = data.get("digest") or data.get("sha256") or (data.get("original") or {}).get("sha256")
        if not claimed:
            return None
        if self.actual_digest is None:
            return None
        if claimed == self.actual_digest:
            return True
        path = self.artifacts.get(aid, {}).get("path")
        self.r.fail("digest-mismatch",
                    f"{aid}: statement binds {claimed}, but reader original has {self.actual_digest}. "
                    "The old proof cannot endorse this edition.",
                    artifact_id=aid, path=path, expected=claimed, actual=self.actual_digest,
                    break_at={"artifact_id": "reader-original", "path": str(self.r.original),
                              "reason": f"byte digest differs from {aid}; this is a different edition"})
        return False

    def check_typed_relationships(self) -> None:
        for aid, data in self.datas.items():
            if data.get("type") == "registry-closure":
                effective = parse_iso(data.get("effective_at") or data.get("issued_at"))
                if effective and effective <= self.r.now:
                    self.r.warn(
                        "registry-online-unavailable",
                        f"{aid}: registry closed/ceased service at {effective.isoformat()}; "
                        "a live registry recheck is unavailable. Valid original-time receipts can still "
                        "be checked offline, but a new statement cannot replace missing history.",
                        artifact_id=aid,
                    )

        # Every statement carrying a digest must bind the reader's exact file.
        for aid, data in self.datas.items():
            if aid == "policy":
                continue
            if isinstance(data, dict) and ("digest" in data or "sha256" in data):
                self.digest_matches_original(aid, data)

        # Verify hash references inside statements.  This catches substitution
        # of a signed old receipt while leaving the migration statement intact.
        for aid, data in self.datas.items():
            if aid == "policy" or not isinstance(data, dict):
                continue
            for ref in self.iter_references(data):
                target_id = ref.get("artifact_id")
                expected_hash = ref.get("sha256") or ref.get("envelope_sha256")
                if not target_id or not expected_hash:
                    continue
                actual_hash = self.file_hashes.get(target_id)
                if actual_hash is None:
                    self.r.fail("reference-missing",
                                f"{aid}: references artifact {target_id}, which is absent",
                                artifact_id=aid)
                    continue
                if actual_hash != expected_hash:
                    self.r.fail("reference-hash",
                                f"{aid}: referenced {target_id} hash does not match",
                                artifact_id=aid, expected=expected_hash, actual=actual_hash,
                                break_at={"artifact_id": aid,
                                          "path": self.artifacts.get(aid, {}).get("path"),
                                          "reason": f"hash reference to {target_id} is broken"})
                else:
                    self.r.pass_("reference-hash", f"{aid} -> {target_id} matches")

        self.check_renewal_chains()
        self.check_bridge_times()

    def iter_references(self, obj: Any, path: str = ""):
        if isinstance(obj, dict):
            if "artifact_id" in obj and ("sha256" in obj or "envelope_sha256" in obj):
                yield obj
            for k, v in obj.items():
                yield from self.iter_references(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                yield from self.iter_references(v, f"{path}[{i}]")

    def check_renewal_chains(self) -> None:
        chains = self.manifest.get("renewal_chains") or []
        for chain in chains:
            cid = chain.get("id", "renewal-chain")
            ids = chain.get("artifacts") or []
            prev_id: str | None = None
            prev_time: datetime | None = None
            prev_valid_through: datetime | None = None
            prev_hash: str | None = None
            prev_seq: int | None = None

            for pos, aid in enumerate(ids):
                data = self.datas.get(aid)
                entry = self.artifacts.get(aid, {})
                if data is None:
                    # Already reported.
                    continue
                seq = data.get("seq", pos)
                now_t = parse_iso(data.get("issued_at"))
                valid_through = parse_iso(data.get("valid_through"))
                if not isinstance(seq, int) or seq != pos:
                    self.r.fail("renewal-sequence",
                                f"{cid}/{aid}: expected seq={pos}, found {seq!r}",
                                artifact_id=aid,
                                break_at={"artifact_id": aid, "path": entry.get("path"),
                                          "reason": "renewal sequence number is not contiguous"})
                if prev_id is not None:
                    wanted_prev = data.get("previous_artifact_id")
                    wanted_hash = data.get("previous_envelope_sha256")
                    if wanted_prev != prev_id:
                        self.r.fail("renewal-previous-id",
                                    f"{aid}: previous_artifact_id={wanted_prev!r}, expected {prev_id!r}",
                                    artifact_id=aid,
                                    break_at={"artifact_id": aid, "path": entry.get("path"),
                                              "reason": "renewal does not point to the preceding receipt"})
                    if wanted_hash != prev_hash:
                        self.r.fail("renewal-hash-link",
                                    f"{aid}: previous_envelope_sha256 does not match {prev_id}",
                                    artifact_id=aid, expected=prev_hash, actual=wanted_hash,
                                    break_at={"artifact_id": aid, "path": entry.get("path"),
                                              "reason": "hash chain link to previous renewal is broken"})
                    if now_t and prev_time and now_t < prev_time:
                        self.r.fail("renewal-time-order",
                                    f"{aid}: issued_at precedes previous renewal",
                                    artifact_id=aid,
                                    break_at={"artifact_id": aid, "path": entry.get("path"),
                                              "reason": "renewal dates are out of order"})
                    if now_t and prev_valid_through and now_t > prev_valid_through:
                        self.r.fail("renewal-validity-gap",
                                    f"{aid}: renewed at {now_t.isoformat()}, but previous term ended "
                                    f"{prev_valid_through.isoformat()}; continuity gap",
                                    artifact_id=aid,
                                    break_at={"artifact_id": aid, "path": entry.get("path"),
                                              "reason": "renewal occurred after previous commitment term expired"})
                    else:
                        self.r.pass_("renewal-link", f"{cid}: {prev_id} -> {aid} is unbroken")
                else:
                    if data.get("previous_envelope_sha256") not in (None, "GENESIS"):
                        self.r.fail("renewal-genesis",
                                    f"{aid}: first chain item must have GENESIS previous hash",
                                    artifact_id=aid)
                prev_id = aid
                prev_hash = self.file_hashes.get(aid)
                prev_time = now_t
                prev_valid_through = valid_through
                if isinstance(seq, int):
                    prev_seq = seq

            anchor_id = chain.get("deadline_anchor") or (ids[-1] if ids else None)
            if chain.get("must_cover_cutoff") and self.cutoff and prev_valid_through:
                if prev_valid_through < self.cutoff:
                    self.r.fail(
                        "chain-expired-before-cutoff",
                        f"{cid}: last renewal term ends {prev_valid_through.isoformat()}, before cutoff "
                        f"{self.cutoff.isoformat()}; the continuous commitment does not cover the deadline",
                        artifact_id=prev_id,
                        break_at={"artifact_id": prev_id,
                                  "path": self.artifacts.get(prev_id or "", {}).get("path"),
                                  "reason": "renewal term ended before cutoff and no timely successor exists"},
                    )
            if anchor_id and anchor_id in self.datas and self.cutoff:
                ad = self.datas[anchor_id]
                at = parse_iso(ad.get("issued_at"))
                entry = self.artifacts.get(anchor_id, {})
                if at and at > self.cutoff:
                    self.r.fail("deadline-anchor-late",
                                f"{anchor_id}: last/designated renewal issued {at.isoformat()}, "
                                f"after deadline {self.cutoff.isoformat()}. A later valid receipt cannot backdate "
                                "the missing pre-deadline link.",
                                artifact_id=anchor_id,
                                deadline=self.cutoff.isoformat(), issued_at=at.isoformat(),
                                break_at={"artifact_id": anchor_id, "path": entry.get("path"),
                                          "reason": "designated renewal is after the deadline; chain cannot prove the deadline claim"})
                elif at:
                    self.r.pass_("deadline-anchor-timely",
                                 f"{anchor_id}: issued {at.isoformat()} <= deadline {self.cutoff.isoformat()}")

    def check_bridge_times(self) -> None:
        for aid, data in self.datas.items():
            if data.get("type") != "migration-attestation":
                continue
            issued = parse_iso(data.get("issued_at"))
            old_key = data.get("relied_authority") or data.get("old_authority")
            auth = self.authority(old_key) if old_key else None
            entry = self.artifacts.get(aid, {})
            if not issued:
                self.r.fail("bridge-time-missing", f"{aid}: migration has no usable issued_at", artifact_id=aid)
                continue
            if auth and auth.get("trust_not_after"):
                end = parse_iso(auth["trust_not_after"])
                if end and issued > end:
                    self.r.fail("bridge-too-late",
                                f"{aid}: successor witness acted at {issued.isoformat()}, after old authority "
                                f"{old_key} ceased to be admissible at {end.isoformat()}. Today's stamp cannot backdate.",
                                artifact_id=aid,
                                break_at={"artifact_id": aid, "path": entry.get("path"),
                                          "reason": "trust bridge was created after the old witness's admissibility end"})
                else:
                    self.r.pass_("bridge-timely",
                                 f"{aid}: migration at {issued.isoformat()} is before old authority end {auth['trust_not_after']}")
            # An existence cutoff can be earlier than the trust-bridge date.
            # Such a bridge may still carry forward an old receipt that itself
            # predates the cutoff, so do not compare bridge time to self.cutoff.

    def admissible_anchors(self) -> list[dict[str, Any]]:
        """Find signed statements that currently give an admissible path."""
        anchors: list[dict[str, Any]] = []
        failed_ids = {c.get("artifact_id") for c in self.r.checks if c["status"] == "fail"}
        for aid, data in self.datas.items():
            if aid == "policy" or aid in failed_ids:
                continue
            if self.actual_digest is not None:
                d = data.get("digest") or (data.get("original") or {}).get("sha256")
                if d and d != self.actual_digest:
                    continue
            t = parse_iso(data.get("issued_at") or data.get("generated_at"))
            if not t:
                continue
            env = self.envelopes.get(aid, {})
            key_id = env.get("key_id")
            auth = self.authority(key_id) or {}
            # Old timestamp alone is not a present-day trust anchor after its
            # policy end unless a timely migration/chain bridges it.  The
            # claim evaluator handles bridges; here list direct current keys.
            end = parse_iso(auth.get("trust_not_after"))
            currently_trusted = end is None or end >= self.r.now
            if currently_trusted:
                anchors.append({"artifact_id": aid, "time": t.isoformat().replace("+00:00", "Z"),
                                "kind": data.get("type", "statement"), "key_id": key_id})
        anchors.sort(key=lambda x: x["time"])
        return anchors

    def evaluate_claim(self) -> None:
        claim = self.manifest.get("claim") or "integrity"
        if self.args.claim:
            claim = self.args.claim
        if claim == "existence":
            claim = "existence-by-deadline"
        failures = [c for c in self.r.checks if c["status"] == "fail"]
        anchors = self.admissible_anchors()
        if anchors:
            self.r.earliest_anchor = anchors[0]

        if failures:
            self.r.ok = False
            self.r.summary = (f"NOT VERIFIED ({len(failures)} failure(s)); first break is "
                              f"{self.r.break_at['artifact_id'] if self.r.break_at else 'shown above'}.")
            return

        if claim in {"integrity", "self-contained-bridge", "renewal-chain", "existence-by-deadline"}:
            if self.actual_digest is None and (self.manifest.get("original") or {}).get("required", True):
                self.r.ok = False
                self.r.summary = "NOT VERIFIED: no reader original was supplied to bind the digest."
                return

        if claim == "registry-statement-only":
            self.r.fail(
                "history-required",
                "registry's current statement alone cannot re-prove prior witnessing; "
                "original point-in-time credentials are required.",
                break_at={
                    "artifact_id": "registry-current-statement",
                    "reason": "a new registry statement is not a substitute for original-time receipts",
                },
            )
            self.r.ok = False
            self.r.summary = ("NOT VERIFIED: registry's current statement alone cannot re-prove prior "
                              "witnessing; original point-in-time credentials are required.")
            return

        if claim in {"self-contained-bridge", "existence-by-deadline"}:
            bridges = [aid for aid, d in self.datas.items()
                       if d.get("type") == "migration-attestation" and aid not in
                       {c.get("artifact_id") for c in self.r.checks if c["status"] == "fail"}]
            if claim == "self-contained-bridge" and not bridges:
                self.r.fail("bridge-missing",
                            "no timely successor-witness migration attestation is present")
                self.r.ok = False
                self.r.summary = "NOT VERIFIED: no timely successor-witness bridge is present."
                return
            if claim == "self-contained-bridge" and "old-receipt" not in self.datas:
                self.r.fail("old-receipt-missing",
                            "self-contained bridge claim requires the original point-in-time receipt")
                self.r.ok = False
                self.r.summary = "NOT VERIFIED: original point-in-time receipt is missing."
                return

        if claim == "renewal-chain" and not self.manifest.get("renewal_chains"):
            self.r.fail("renewal-chain-missing", "manifest declares no renewal_chains")
            self.r.ok = False
            self.r.summary = "NOT VERIFIED: no renewal chain is present."
            return

        if claim in {"renewal-chain", "existence-by-deadline"} and self.cutoff:
            # If no hard failure occurred and manifest specified chains, the
            # deadline anchor check has already passed.  Require an anchor at
            # or before cutoff when proving existence by that cutoff.
            before = []
            for a in anchors:
                t = parse_iso(a["time"])
                if t and t <= self.cutoff:
                    before.append(a)
            if not before and not self.r.earliest_anchor:
                self.r.ok = False
                self.r.summary = "NOT VERIFIED: no currently admissible anchor at or before cutoff."
                return

        self.r.ok = True
        if claim == "self-contained-bridge":
            self.r.summary = ("VERIFIED: exact reader bytes match the old receipt; original-time checks and "
                              "a timely successor-witness bridge validate the complete witness history offline.")
        elif claim == "renewal-chain":
            self.r.summary = "VERIFIED: signatures, digest bindings, sequence, and renewal links are intact."
        elif claim == "existence-by-deadline":
            self.r.summary = "VERIFIED: an admissible hash path proves this exact edition existed by the deadline."
        else:
            self.r.summary = "VERIFIED: all supplied signatures, hashes, and declared relationships check."


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Independently verify an Archive Cooperative evidence bundle")
    p.add_argument("bundle", help="evidence bundle directory, manifest.json, or bundle ZIP")
    p.add_argument("--original", "--file", "--manuscript", "--reader-file",
                   dest="original", help="reader-supplied manuscript bytes to bind to the receipts")
    p.add_argument("--before", "--deadline", dest="before",
                   help="ISO date/datetime cutoff for an existence/deadline claim")
    p.add_argument("--claim", help="override manifest claim")
    p.add_argument("--now", help="ISO datetime used as verifier current time (simulation/testing)")
    p.add_argument("--json", action="store_true", help="write machine-readable JSON to stdout")
    p.add_argument("--out", help="also write JSON report to this file")
    return p


def resolve_bundle_input(raw: str) -> tuple[Path, str | None]:
    p = Path(raw).resolve()
    cleanup_dir: str | None = None

    if p.is_file() and p.suffix.lower() == ".zip":
        td = tempfile.mkdtemp(prefix="verify-bundle-zip-")
        cleanup_dir = td
        with zipfile.ZipFile(p) as zf:
            zf.extractall(td)
        p = Path(td)

    if p.is_file() and p.name == "manifest.json":
        p = p.parent

    if p.is_dir() and not (p / "manifest.json").is_file():
        manifests = [q for q in p.glob("**/manifest.json") if q.is_file()]
        # Do not accidentally descend into a copied review tree; prefer the
        # shallowest unique manifest.
        manifests.sort(key=lambda q: len(q.relative_to(p).parts))
        if manifests:
            p = manifests[0].parent

    return p, cleanup_dir


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    cleanup_dir: str | None = None
    try:
        bundle_path, cleanup_dir = resolve_bundle_input(args.bundle)
        now = parse_iso(args.now) or datetime.now(timezone.utc)
        report = Report(bundle_path, Path(args.original).resolve() if args.original else None, now)
        verifier = Verifier(bundle_path, args, report)
        verifier.run()
    except Exception as exc:  # noqa: BLE001
        eprint(f"verifier error: {type(exc).__name__}: {exc}")
        if cleanup_dir:
            shutil.rmtree(cleanup_dir, ignore_errors=True)
        return 3

    try:
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n",
                                      encoding="utf-8")
        if args.json:
            print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        else:
            report.print_text()
        rc = 0 if report.ok else 2
    finally:
        if cleanup_dir:
            shutil.rmtree(cleanup_dir, ignore_errors=True)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
