"""Shared helpers for the fictional archive cooperative exercise.

The simulation uses signed JSON envelopes.  Signatures are Ed25519 produced
and verified through the system OpenSSL executable, so the public receipts can
be checked by a standalone program with no Python packages.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

# Published evidence must be readable by a stranger's process.  Private key
# files/directories are explicitly chmod'ed to 0600/0700 after creation.
os.umask(0o022)

WORKSPACE = Path(__file__).resolve().parent.parent
CABINET = WORKSPACE / "cabinet"
PRIVATE = CABINET / "private"
DELIVERABLES = WORKSPACE / "deliverables"
TRUST_DIR = DELIVERABLES / "trust"
CASES_DIR = DELIVERABLES / "cases"
BUNDLE_DIR = DELIVERABLES / "bundles"
READER_DIR = DELIVERABLES / "reader-originals"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def canonical_bytes(obj: Any) -> bytes:
    return json.dumps(
        obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def canonical_sha256(obj: Any) -> str:
    return hashlib.sha256(canonical_bytes(obj)).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: str | Path, obj: Any, *, mode: int | None = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if mode is not None:
        os.chmod(path, mode)
    return path


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def run(cmd: Iterable[str], *, input_bytes: bytes | None = None, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        [str(x) for x in cmd],
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"command failed ({proc.returncode}): {' '.join(map(str, cmd))}\n"
            f"stdout={proc.stdout.decode(errors='replace')}\n"
            f"stderr={proc.stderr.decode(errors='replace')}"
        )
    return proc


def require_openssl() -> str:
    exe = shutil.which("openssl")
    if not exe:
        raise RuntimeError("openssl is required for public-receipt signatures")
    return exe


def generate_ed25519_key(key_path: str | Path) -> None:
    openssl = require_openssl()
    key_path = Path(key_path)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    run([openssl, "genpkey", "-algorithm", "ED25519", "-out", key_path])
    os.chmod(key_path, 0o600)


def export_public_key(key_path: str | Path, pub_path: str | Path) -> Path:
    openssl = require_openssl()
    pub_path = Path(pub_path)
    pub_path.parent.mkdir(parents=True, exist_ok=True)
    run([openssl, "pkey", "-in", key_path, "-pubout", "-out", pub_path])
    os.chmod(pub_path, 0o644)
    return pub_path


def sign_canonical(data: Any, key_path: str | Path) -> str:
    openssl = require_openssl()
    with tempfile.TemporaryDirectory(prefix="coop-sign-") as td:
        inp = Path(td) / "data"
        sig = Path(td) / "sig"
        inp.write_bytes(canonical_bytes(data))
        run([openssl, "pkeyutl", "-sign", "-inkey", key_path, "-rawin", "-in", inp, "-out", sig])
        return base64.b64encode(sig.read_bytes()).decode("ascii")


def verify_signature(data: Any, signature_b64: str, pub_key_path: str | Path) -> tuple[bool, str]:
    openssl = require_openssl()
    if not Path(pub_key_path).exists():
        return False, f"public key not found: {pub_key_path}"
    try:
        sig_bytes = base64.b64decode(signature_b64, validate=True)
    except Exception as exc:  # noqa: BLE001 - report as verification failure
        return False, f"signature is not valid base64: {exc}"
    with tempfile.TemporaryDirectory(prefix="coop-verify-") as td:
        inp = Path(td) / "data"
        sig = Path(td) / "sig"
        inp.write_bytes(canonical_bytes(data))
        sig.write_bytes(sig_bytes)
        proc = run(
            [openssl, "pkeyutl", "-verify", "-pubin", "-inkey", pub_key_path,
             "-rawin", "-in", inp, "-sigfile", sig],
            check=False,
        )
        if proc.returncode == 0:
            return True, "Ed25519 signature valid"
        detail = (proc.stderr or proc.stdout).decode(errors="replace").strip()
        return False, f"Ed25519 verification failed: {detail}"


def make_envelope(data: dict[str, Any], key_id: str, key_path: str | Path) -> dict[str, Any]:
    return {
        "schema": "archive-coop-envelope/1",
        "algorithm": "Ed25519",
        "key_id": key_id,
        "data": data,
        "signature_b64": sign_canonical(data, key_path),
    }


def write_signed_json(path: str | Path, data: dict[str, Any], key_id: str, key_path: str | Path) -> Path:
    return write_json(path, make_envelope(data, key_id, key_path))


def file_sha256(path: str | Path) -> str:
    return sha256_file(path)


def safe_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
