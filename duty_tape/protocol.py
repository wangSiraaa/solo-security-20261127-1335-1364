"""Artifact and tape protocol for independently sealable duty tables."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

from .canonical import canonical_body, canonical_bytes, sha256_bytes, short_id
from .sealer import TestSealer

ARTIFACT_SCHEMA = "duty-tape.artifact/v1"
TAPE_SCHEMA = 1
VALID_KINDS = ("contacts", "lockers", "duty")


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class Dependency:
    kind: str
    artifact_id: str
    digest: str
    generation: int
    batch: str

    def to_header(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "artifact_id": self.artifact_id,
            "digest": self.digest,
            "generation": self.generation,
            "batch": self.batch,
        }

    @classmethod
    def from_header(cls, obj: Dict[str, Any]) -> "Dependency":
        return cls(
            kind=obj["kind"],
            artifact_id=obj["artifact_id"],
            digest=obj["digest"],
            generation=int(obj["generation"]),
            batch=obj["batch"],
        )


def artifact_digest(header: Dict[str, Any], body: str) -> str:
    return sha256_bytes(
        canonical_bytes(header)
        + b"\x00duty-tape-body-v1\x00"
        + body.encode("utf-8")
    )


def build_artifact(
    *,
    batch: str,
    kind: str,
    generation: int,
    body: str,
    issued_at: str,
    dependencies: Sequence[Dependency] = (),
    issuer: str = "issuing-desk",
) -> Dict[str, Any]:
    if kind not in VALID_KINDS:
        raise ProtocolError(f"unknown artifact kind: {kind}")
    if generation < 1:
        raise ProtocolError("generation must be positive")

    clean_body = canonical_body(body)
    deps = sorted((d.to_header() for d in dependencies), key=lambda x: x["kind"])
    header: Dict[str, Any] = {
        "schema": ARTIFACT_SCHEMA,
        "batch": batch,
        "kind": kind,
        "generation": generation,
        "issuer": issuer,
        "issued_at": issued_at,
        "line_count": len(clean_body.splitlines()),
        "body_sha256": sha256_bytes(clean_body.encode("utf-8")),
        "dependencies": deps,
    }
    digest = artifact_digest(header, clean_body)
    return {
        "schema": ARTIFACT_SCHEMA,
        "header": header,
        "body": clean_body,
        "digest": digest,
        "artifact_id": short_id(kind, generation, digest),
        "receipt": None,
    }


def dependency_pin(artifact: Dict[str, Any]) -> Dependency:
    h = artifact["header"]
    return Dependency(
        kind=h["kind"],
        artifact_id=artifact["artifact_id"],
        digest=artifact["digest"],
        generation=int(h["generation"]),
        batch=h["batch"],
    )


def seal_artifact(artifact: Dict[str, Any], sealer: TestSealer) -> Dict[str, Any]:
    header = artifact["header"]
    payload = {
        "schema": "duty-tape.receipt/v1",
        "artifact_id": artifact["artifact_id"],
        "artifact_digest": artifact["digest"],
        "batch": header["batch"],
        "kind": header["kind"],
        "generation": header["generation"],
        "issued_at": header["issued_at"],
    }
    artifact["receipt"] = sealer.seal(payload)
    return artifact


def verify_artifact(artifact: Dict[str, Any], sealer: TestSealer) -> None:
    """Validate structure, content hash, and trusted receipt."""
    if artifact.get("schema") != ARTIFACT_SCHEMA:
        raise ProtocolError("bad artifact schema")
    header = artifact.get("header")
    body = artifact.get("body")
    receipt = artifact.get("receipt")
    if not isinstance(header, dict) or not isinstance(body, str):
        raise ProtocolError("artifact missing header/body")
    if header.get("schema") != ARTIFACT_SCHEMA:
        raise ProtocolError("bad header schema")
    if header.get("kind") not in VALID_KINDS:
        raise ProtocolError("bad artifact kind")

    clean_body = canonical_body(body)
    if clean_body != body and body != "":
        # Stored bodies are canonical.  Reconstructed bodies are canonical too.
        raise ProtocolError("non-canonical body")
    expected_body_hash = sha256_bytes(clean_body.encode("utf-8"))
    if header.get("body_sha256") != expected_body_hash:
        raise ProtocolError("body hash mismatch")
    if header.get("line_count") != len(clean_body.splitlines()):
        raise ProtocolError("line count mismatch")

    expected_digest = artifact_digest(header, clean_body)
    if artifact.get("digest") != expected_digest:
        raise ProtocolError("artifact digest mismatch")

    aid = short_id(header["kind"], int(header["generation"]), expected_digest)
    if artifact.get("artifact_id") != aid:
        raise ProtocolError("artifact id mismatch")

    if not isinstance(receipt, dict):
        raise ProtocolError("artifact has no sealer receipt")
    payload = sealer.verify(receipt)
    if payload.get("artifact_digest") != expected_digest:
        raise ProtocolError("receipt is for a different digest")
    if payload.get("artifact_id") != artifact["artifact_id"]:
        raise ProtocolError("receipt is for a different artifact id")
    if payload.get("batch") != header.get("batch"):
        raise ProtocolError("receipt batch mismatch")
    if payload.get("kind") != header.get("kind"):
        raise ProtocolError("receipt kind mismatch")
    if payload.get("generation") != header.get("generation"):
        raise ProtocolError("receipt generation mismatch")


def artifact_records(artifact: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Serialize one sealed artifact to sequential small tape records."""
    records: List[Dict[str, Any]] = [
        {
            "record": "head",
            "tape_schema": TAPE_SCHEMA,
            "digest": artifact["digest"],
            "artifact_id": artifact["artifact_id"],
            "header": artifact["header"],
        }
    ]
    for i, line in enumerate(artifact["body"].splitlines()):
        records.append(
            {
                "record": "line",
                "tape_schema": TAPE_SCHEMA,
                "digest": artifact["digest"],
                "index": i,
                "text": line,
            }
        )
    records.append(
        {
            "record": "receipt",
            "tape_schema": TAPE_SCHEMA,
            "digest": artifact["digest"],
            "artifact_id": artifact["artifact_id"],
            "receipt": artifact["receipt"],
        }
    )
    return records


def write_tape(path: str | Path, artifacts: Sequence[Dict[str, Any]]) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        for artifact in artifacts:
            for record in artifact_records(artifact):
                f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return p


def write_record_segments(
    paths: Sequence[str | Path],
    artifacts: Sequence[Dict[str, Any]],
) -> List[Path]:
    """Write exactly one artifact-aligned segment per requested path."""
    if len(paths) != len(artifacts):
        raise ValueError("this helper expects one segment path per artifact")
    out: List[Path] = []
    for path, artifact in zip(paths, artifacts):
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", encoding="utf-8") as f:
            for record in artifact_records(artifact):
                f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        out.append(p)
    return out


def read_jsonl_records(path: str | Path) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                raise ProtocolError(f"invalid JSON on line {lineno}: {e}") from e
            if not isinstance(obj, dict):
                raise ProtocolError(f"record on line {lineno} is not an object")
            records.append(obj)
    return records


def split_jsonl_file(
    tape_path: str | Path,
    segment_paths: Iterable[str | Path],
    *,
    record_counts: Sequence[int] | None = None,
) -> List[Path]:
    """Split a JSONL tape into segment files by complete record counts."""
    records = read_jsonl_records(tape_path)
    targets = [Path(p) for p in segment_paths]
    if record_counts is None:
        n = len(targets)
        base = len(records) // n
        record_counts = [base] * (n - 1) + [len(records) - base * (n - 1)]
    if sum(record_counts) != len(records) or len(record_counts) != len(targets):
        raise ValueError("record counts must cover every record exactly once")

    written: List[Path] = []
    pos = 0
    for target, count in zip(targets, record_counts):
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8") as f:
            for rec in records[pos : pos + count]:
                f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
        pos += count
        written.append(target)
    return written
