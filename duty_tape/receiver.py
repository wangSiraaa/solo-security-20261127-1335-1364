"""Recovery-side durable state, verification, mounting, and audit log."""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Sequence

from .canonical import canonical_bytes, sha256_bytes
from .protocol import (
    Dependency,
    ProtocolError,
    verify_artifact,
)
from .sealer import ReceiptError, TestSealer


class RecoveryError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, sort_keys=True, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


class RecoveryTerminal:
    """Local duty terminal state.

    Committed facts live in ``store/`` and the append-only ``ledger.jsonl``.
    Unverified tape fragments live only in ``tmp/`` and may be deleted at any
    time without changing the mounted catalog.
    """

    CATALOG_SCHEMA = "duty-tape.catalog/v1"

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        sealer: TestSealer | None = None,
        clock: Callable[[], str] = utc_now,
    ) -> None:
        self.root = Path(root)
        self.clock = clock
        self.sealer = sealer or TestSealer()
        self.tmp = self.root / "tmp"
        self.staging = self.tmp / "staging"
        self.buffer_path = self.tmp / "feed-buffer.bin"
        self.store = self.root / "store" / "artifacts"
        self.catalogs = self.root / "catalogs"
        self.current = self.root / "current"
        self.ledger_path = self.root / "ledger.jsonl"
        self.pins_path = self.root / "pins.json"
        self.revocations_path = self.root / "revocations.jsonl"

        for d in (self.root, self.tmp, self.staging, self.store, self.catalogs):
            d.mkdir(parents=True, exist_ok=True)
        if not self.pins_path.exists():
            _atomic_write_json(self.pins_path, {})
        if not self.ledger_path.exists():
            self.ledger_path.touch()
        if not self.current.exists() and not self.current.is_symlink():
            self._materialize({}, reason="initialize", emit_event=True)

        # Reconstruct the safe active view from committed receipts after an
        # upgrade/restart or loss of the regenerable current symlink.  This is
        # idempotent and does not rewrite the append-only ledger on no-change.
        self.activate(reason="startup")

    # ------------------------------------------------------------------ logs

    def _event(self, event_type: str, **fields: Any) -> Dict[str, Any]:
        seq = 1
        prev_hash = "sha256:GENESIS"
        if self.ledger_path.exists():
            with self.ledger_path.open("r", encoding="utf-8") as f:
                lines = [line for line in f if line.strip()]
            seq = len(lines) + 1
            if lines:
                prev_hash = json.loads(lines[-1]).get("event_hash", prev_hash)
        event = {
            "seq": seq,
            "time": self.clock(),
            "event": event_type,
            "prev_hash": prev_hash,
        }
        event.update(fields)
        event["event_hash"] = sha256_bytes(canonical_bytes(event))
        with self.ledger_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())
        return event

    def verify_history(self) -> Dict[str, Any]:
        """Verify the append-only hash chain; returns event count and tip."""
        prev_hash = "sha256:GENESIS"
        count = 0
        if not self.ledger_path.exists():
            return {"events": 0, "tip": prev_hash, "valid": True}
        with self.ledger_path.open("r", encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                if not line.strip():
                    continue
                event = json.loads(line)
                expected_prev = event.get("prev_hash")
                if expected_prev != prev_hash:
                    raise RecoveryError(f"ledger prev_hash mismatch at line {lineno}")
                claimed = event.pop("event_hash", None)
                actual = sha256_bytes(canonical_bytes(event))
                event["event_hash"] = claimed
                if claimed != actual:
                    raise RecoveryError(f"ledger event hash mismatch at line {lineno}")
                prev_hash = claimed
                count += 1
        return {"events": count, "tip": prev_hash, "valid": True}

    def history(self, event_types: Iterable[str] | None = None) -> List[Dict[str, Any]]:
        wanted = set(event_types) if event_types is not None else None
        out: List[Dict[str, Any]] = []
        if not self.ledger_path.exists():
            return out
        with self.ledger_path.open("r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                event = json.loads(line)
                if wanted is None or event.get("event") in wanted:
                    out.append(event)
        return out

    # ------------------------------------------------------------- low level

    def _safe_digest_name(self, digest: str) -> str:
        return digest.replace(":", "_").replace("/", "_")

    def _staging_path(self, digest: str) -> Path:
        return self.staging / (self._safe_digest_name(digest) + ".json")

    def _store_path(self, digest: str) -> Path:
        return self.store / (self._safe_digest_name(digest) + ".json")

    def _save_staging(self, digest: str, state: Dict[str, Any]) -> None:
        _atomic_write_json(self._staging_path(digest), state)

    def _load_staging(self, digest: str) -> Dict[str, Any] | None:
        p = self._staging_path(digest)
        return _load_json(p, None) if p.exists() else None

    def get_artifact(self, digest: str) -> Dict[str, Any] | None:
        p = self._store_path(digest)
        if not p.exists():
            return None
        art = _load_json(p, None)
        if art is not None:
            verify_artifact(art, self.sealer)
        return art

    def verified_artifacts(self) -> List[Dict[str, Any]]:
        arts: List[Dict[str, Any]] = []
        for p in sorted(self.store.glob("*.json")):
            art = _load_json(p, None)
            if not isinstance(art, dict):
                raise RecoveryError(f"stored artifact is corrupt: {p}")
            verify_artifact(art, self.sealer)
            arts.append(art)
        return arts

    # ---------------------------------------------------------------- feed

    def feed_file(
        self,
        path: str | os.PathLike[str],
        *,
        expected_batch: str | None = None,
        strict: bool = False,
    ) -> Dict[str, Any]:
        return self.feed_bytes(
            Path(path).read_bytes(),
            source=str(path),
            expected_batch=expected_batch,
            strict=strict,
        )

    def feed_bytes(
        self,
        data: bytes,
        *,
        source: str = "<bytes>",
        expected_batch: str | None = None,
        strict: bool = False,
    ) -> Dict[str, Any]:
        # A clean external `rm -rf tmp` is allowed at any time.  Recreate the
        # directories; unverified fragments simply have to be re-supplied.
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.staging.mkdir(parents=True, exist_ok=True)
        previous = self.buffer_path.read_bytes() if self.buffer_path.exists() else b""
        blob = previous + data
        if b"\n" in blob:
            complete, remainder = blob.rsplit(b"\n", 1)
            # rsplit keeps all complete lines in ``complete``; the final piece
            # after the last newline is either an empty string or partial line.
            lines = complete.split(b"\n")
        else:
            lines = []
            remainder = blob
        self.buffer_path.write_bytes(remainder)

        result: Dict[str, Any] = {
            "source": source,
            "records": 0,
            "verified": [],
            "already_verified": [],
            "rejected": [],
            "activation": None,
            "pending_bytes": len(remainder),
        }

        for raw in lines:
            if not raw.strip():
                continue
            result["records"] += 1
            try:
                record = json.loads(raw.decode("utf-8"))
                if not isinstance(record, dict):
                    raise ProtocolError("tape record is not an object")
                one = self.feed_record(record, expected_batch=expected_batch)
                if one.get("status") == "verified":
                    result["verified"].append(one["digest"])
                elif one.get("status") == "already_verified":
                    result["already_verified"].append(one["digest"])
            except (ProtocolError, ReceiptError, RecoveryError, KeyError, ValueError) as e:
                rejection = {"error": str(e)}
                result["rejected"].append(rejection)
                self._event("tape_record_rejected", source=source, error=str(e))
                if strict:
                    self._event("feed_stopped_strict", source=source)
                    raise RecoveryError(str(e)) from e

        if result["verified"]:
            result["activation"] = self.activate(reason="after_feed")
        return result

    def feed_record(
        self,
        record: Dict[str, Any],
        *,
        expected_batch: str | None = None,
    ) -> Dict[str, Any]:
        kind = record.get("record")
        digest = record.get("digest")
        if not isinstance(digest, str) or not digest.startswith("sha256:"):
            raise ProtocolError("tape record lacks a valid digest")

        if kind == "head":
            header = record.get("header")
            if not isinstance(header, dict):
                raise ProtocolError("head record lacks header")
            if expected_batch and header.get("batch") != expected_batch:
                raise ProtocolError(
                    f"foreign batch {header.get('batch')!r}; expected {expected_batch!r}"
                )
            if self._store_path(digest).exists():
                return {"status": "already_verified", "digest": digest}
            self._save_staging(digest, {"head": record, "lines": {}})
            return {"status": "head", "digest": digest}

        if kind == "line":
            if self._store_path(digest).exists():
                return {"status": "already_verified", "digest": digest}
            state = self._load_staging(digest)
            if state is None:
                raise ProtocolError("line record has no staged head")
            index = record.get("index")
            text = record.get("text")
            if not isinstance(index, int) or index < 0 or not isinstance(text, str):
                raise ProtocolError("invalid line record")
            state["lines"][str(index)] = {"text": text}
            self._save_staging(digest, state)
            return {"status": "line", "digest": digest, "index": index}

        if kind == "receipt":
            if self._store_path(digest).exists():
                return {"status": "already_verified", "digest": digest}
            state = self._load_staging(digest)
            if state is None:
                raise ProtocolError("receipt has no staged artifact")
            try:
                artifact = self._reconstruct(state, record.get("receipt"))
                verify_artifact(artifact, self.sealer)
                header = artifact["header"]
                if expected_batch and header.get("batch") != expected_batch:
                    raise ProtocolError(
                        f"foreign batch {header.get('batch')!r}; expected {expected_batch!r}"
                    )
            except Exception as e:
                self._event(
                    "artifact_rejected",
                    digest=digest,
                    error=str(e),
                )
                raise

            _atomic_write_json(self._store_path(digest), artifact)
            staging = self._staging_path(digest)
            if staging.exists():
                staging.unlink()
            self._event(
                "artifact_verified",
                digest=digest,
                artifact_id=artifact["artifact_id"],
                kind=header["kind"],
                generation=header["generation"],
                batch=header["batch"],
            )
            readiness = self.readiness_for(artifact)
            if not readiness["ready"]:
                self._event(
                    "activation_blocked",
                    digest=digest,
                    artifact_id=artifact["artifact_id"],
                    reasons=readiness["reasons"],
                )
            return {"status": "verified", "digest": digest, "artifact_id": artifact["artifact_id"]}

        raise ProtocolError(f"unknown tape record type: {kind!r}")

    def _reconstruct(self, state: Dict[str, Any], receipt: Any) -> Dict[str, Any]:
        head = state["head"]
        header = head["header"]
        digest = head["digest"]
        line_count = int(header.get("line_count", -1))
        lines = state.get("lines", {})
        texts: List[str] = []
        missing: List[int] = []
        for i in range(line_count):
            item = lines.get(str(i))
            if item is None:
                missing.append(i)
            else:
                texts.append(item["text"])
        if missing:
            shown = ",".join(map(str, missing[:8]))
            more = "..." if len(missing) > 8 else ""
            raise ProtocolError(f"artifact is incomplete; missing line(s) {shown}{more}")
        extra = sorted(int(k) for k in lines if int(k) >= line_count)
        if extra:
            raise ProtocolError(f"unexpected line index {extra[0]}")
        body = "\n".join(texts) + ("\n" if line_count else "")
        return {
            "schema": header.get("schema"),
            "header": header,
            "body": body,
            "digest": digest,
            "artifact_id": head.get("artifact_id"),
            "receipt": receipt,
        }

    # ------------------------------------------------------------- pins/rev

    def pins(self) -> Dict[str, Any]:
        return _load_json(self.pins_path, {})

    def pin_kind(self, kind: str, digest: str) -> Dict[str, Any]:
        art = self.get_artifact(digest)
        if art is None:
            raise RecoveryError("cannot pin an artifact that has not been verified")
        if art["header"]["kind"] != kind:
            raise RecoveryError(f"digest is a {art['header']['kind']}, not {kind}")
        pins = self.pins()
        pins[kind] = {
            "digest": digest,
            "artifact_id": art["artifact_id"],
            "generation": art["header"]["generation"],
            "pinned_at": self.clock(),
        }
        _atomic_write_json(self.pins_path, pins)
        self._event("generation_pinned", kind=kind, digest=digest)
        self.activate(reason="after_pin")
        return pins

    def unpin_kind(self, kind: str) -> None:
        pins = self.pins()
        old = pins.pop(kind, None)
        _atomic_write_json(self.pins_path, pins)
        self._event("pin_removed", kind=kind, old=old)
        self.activate(reason="after_unpin")

    def revocation_targets(self) -> List[Dict[str, Any]]:
        if not self.revocations_path.exists():
            return []
        out: List[Dict[str, Any]] = []
        with self.revocations_path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    out.append(json.loads(line))
        return out

    def is_revoked(self, artifact: Dict[str, Any]) -> bool:
        digest = artifact["digest"]
        batch = artifact["header"]["batch"]
        for rev in self.revocation_targets():
            target = rev.get("target", {})
            if target.get("type") == "artifact" and target.get("digest") == digest:
                return True
            if target.get("type") == "batch" and target.get("batch") == batch:
                return True
        return False

    def revoke(self, revocation: Dict[str, Any]) -> Dict[str, Any]:
        payload = self.sealer.verify(revocation)
        if payload.get("schema") != "duty-tape.revocation/v1":
            raise ReceiptError("not a revocation receipt")
        with self.revocations_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(revocation, ensure_ascii=False, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())
        self._event(
            "revocation_received",
            target=payload.get("target"),
            reason=payload.get("reason"),
            issued_at=payload.get("issued_at"),
        )
        activation = self.activate(reason="after_revocation")
        return {"revocation": payload, "activation": activation}

    # ------------------------------------------------------------ catalogs

    def current_manifest(self) -> Dict[str, Any]:
        manifest_path = self.current / "manifest.json"
        if not manifest_path.exists():
            return self._materialize({}, reason="repair_missing_current")
        manifest = _load_json(manifest_path, None)
        if not isinstance(manifest, dict):
            raise RecoveryError("current manifest is corrupt")
        return manifest

    def _next_catalog_version(self) -> int:
        versions = []
        for p in self.catalogs.glob("cat-*"):
            try:
                versions.append(int(p.name.split("-", 1)[1]))
            except (IndexError, ValueError):
                continue
        return (max(versions) + 1) if versions else 0

    def _materialize(
        self,
        selection: Dict[str, Dict[str, Any]],
        *,
        reason: str,
        emit_event: bool = True,
    ) -> Dict[str, Any]:
        version = self._next_catalog_version()
        cat_name = f"cat-{version:06d}"
        final_dir = self.catalogs / cat_name
        tmp_dir = self.catalogs / (cat_name + ".tmp")
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        (tmp_dir / "receipts").mkdir(parents=True)

        active: Dict[str, Any] = {}
        for kind, art in sorted(selection.items()):
            filename = f"{kind}.csv"
            (tmp_dir / filename).write_text(art["body"], encoding="utf-8")
            _atomic_write_json(tmp_dir / "receipts" / f"{kind}.receipt.json", art["receipt"])
            active[kind] = {
                "file": filename,
                "artifact_id": art["artifact_id"],
                "digest": art["digest"],
                "batch": art["header"]["batch"],
                "generation": art["header"]["generation"],
                "issued_at": art["header"]["issued_at"],
                "body_sha256": art["header"]["body_sha256"],
                "dependencies": art["header"].get("dependencies", []),
            }

        manifest = {
            "schema": self.CATALOG_SCHEMA,
            "version": version,
            "created_at": self.clock(),
            "reason": reason,
            "artifacts": active,
        }
        _atomic_write_json(tmp_dir / "manifest.json", manifest)
        os.rename(tmp_dir, final_dir)

        link_tmp = self.root / "current.next"
        if link_tmp.exists() or link_tmp.is_symlink():
            link_tmp.unlink()
        link_tmp.symlink_to(Path("catalogs") / cat_name, target_is_directory=True)
        os.replace(link_tmp, self.current)

        if emit_event:
            self._event(
                "catalog_switched",
                catalog=cat_name,
                version=version,
                reason=reason,
                active={k: v["digest"] for k, v in active.items()},
            )
        return manifest

    def _artifact_index(self) -> Dict[str, Dict[str, Any]]:
        return {a["digest"]: a for a in self.verified_artifacts() if not self.is_revoked(a)}

    def _choose_contacts(
        self,
        by_digest: Dict[str, Dict[str, Any]],
        current: Dict[str, Any],
        pins: Dict[str, Any],
    ) -> Dict[str, Any] | None:
        candidates = [a for a in by_digest.values() if a["header"]["kind"] == "contacts"]
        if "contacts" in pins:
            wanted = pins["contacts"]["digest"]
            return by_digest.get(wanted)
        current_digest = current.get("artifacts", {}).get("contacts", {}).get("digest")

        def key(a: Dict[str, Any]) -> tuple:
            h = a["header"]
            return (
                int(h["generation"]),
                1 if a["digest"] == current_digest else 0,
                h.get("issued_at", ""),
                a["digest"],
            )

        return max(candidates, key=key) if candidates else None

    def readiness_for(self, duty: Dict[str, Any]) -> Dict[str, Any]:
        """Check a verified duty table against exact local dependencies/pins."""
        by_digest = self._artifact_index()
        pins = self.pins()
        reasons: List[str] = []
        missing: List[Dict[str, Any]] = []
        dep_states: List[Dict[str, Any]] = []

        for dep_obj in duty["header"].get("dependencies", []):
            dep = Dependency.from_header(dep_obj)
            local = by_digest.get(dep.digest)
            state = {
                "kind": dep.kind,
                "artifact_id": dep.artifact_id,
                "digest": dep.digest,
                "present": local is not None,
                "pin_locked_old_generation": False,
            }
            if local is None:
                if self.get_artifact(dep.digest) is not None:
                    reasons.append(f"{dep.kind} generation {dep.generation} is revoked")
                else:
                    reasons.append(f"missing sealed {dep.kind} {dep.artifact_id}")
                missing.append(dep_obj)
            if dep.kind in pins and pins[dep.kind]["digest"] != dep.digest:
                state["pin_locked_old_generation"] = True
                state["pinned_digest"] = pins[dep.kind]["digest"]
                reasons.append(
                    f"{dep.kind} directory is pinned to old generation "
                    f"{pins[dep.kind].get('artifact_id')}; cannot use {dep.artifact_id}"
                )
            dep_states.append(state)

        return {
            "duty_digest": duty["digest"],
            "artifact_id": duty["artifact_id"],
            "generation": duty["header"]["generation"],
            "batch": duty["header"]["batch"],
            "ready": not reasons,
            "reasons": reasons,
            "missing": missing,
            "dependencies": dep_states,
        }

    def activate(self, *, reason: str = "activate") -> Dict[str, Any]:
        by_digest = self._artifact_index()
        pins = self.pins()
        current = self.current_manifest()
        current_arts = current.get("artifacts", {})

        selection: Dict[str, Dict[str, Any]] = {}
        contacts = self._choose_contacts(by_digest, current, pins)
        if contacts is not None:
            selection["contacts"] = contacts

        duties = [a for a in by_digest.values() if a["header"]["kind"] == "duty"]
        current_duty_digest = current_arts.get("duty", {}).get("digest")

        def duty_key(a: Dict[str, Any]) -> tuple:
            h = a["header"]
            return (
                int(h["generation"]),
                1 if a["digest"] == current_duty_digest else 0,
                h.get("issued_at", ""),
                a["digest"],
            )

        selected_duty: Dict[str, Any] | None = None
        blocked: List[Dict[str, Any]] = []
        for duty in sorted(duties, key=duty_key, reverse=True):
            readiness = self.readiness_for(duty)
            if readiness["ready"]:
                selected_duty = duty
                for dep_obj in duty["header"].get("dependencies", []):
                    dep = Dependency.from_header(dep_obj)
                    art = by_digest[dep.digest]
                    selection[dep.kind] = art
                selection["duty"] = duty
                break
            blocked.append(readiness)

        if selected_duty is None:
            # Preserve the previously mounted pair only while it remains
            # complete, unrevoked, and exactly pinned.  Revoking its locker
            # table must not leave a mounted duty table that looks usable.
            current_duty_digest = current_arts.get("duty", {}).get("digest")
            current_duty = by_digest.get(current_duty_digest) if current_duty_digest else None
            if current_duty is not None and self.readiness_for(current_duty)["ready"]:
                selection["duty"] = current_duty
                for dep_obj in current_duty["header"].get("dependencies", []):
                    dep = Dependency.from_header(dep_obj)
                    selection[dep.kind] = by_digest[dep.digest]

        # If there is no safe duty pair, a standalone verified locker catalog
        # may still be exposed.  With a retained duty, never replace its pin.
        if "duty" not in selection:
            lockers = [a for a in by_digest.values() if a["header"]["kind"] == "lockers"]
            if "lockers" in pins:
                locked = by_digest.get(pins["lockers"]["digest"])
                if locked is not None:
                    selection["lockers"] = locked
            elif lockers:
                current_lockers_digest = current_arts.get("lockers", {}).get("digest")
                selection["lockers"] = max(
                    lockers,
                    key=lambda a: (
                        int(a["header"]["generation"]),
                        1 if a["digest"] == current_lockers_digest else 0,
                        a["header"].get("issued_at", ""),
                        a["digest"],
                    ),
                )

        old_digests = {k: v.get("digest") for k, v in current_arts.items()}
        new_digests = {k: a["digest"] for k, a in selection.items()}
        switched = old_digests != new_digests
        manifest = current
        if switched:
            manifest = self._materialize(selection, reason=reason)

        return {
            "switched": switched,
            "catalog_version": manifest.get("version"),
            "active": {k: v.get("digest") for k, v in manifest.get("artifacts", {}).items()},
            "blocked": blocked,
        }

    # ------------------------------------------------------------ copies/tmp

    def export_active(
        self,
        kind: str,
        destination: str | os.PathLike[str],
        *,
        copied_by: str = "other-duty-desk",
    ) -> Dict[str, Any]:
        manifest = self.current_manifest()
        entry = manifest.get("artifacts", {}).get(kind)
        if entry is None:
            raise RecoveryError(f"no active {kind} table to copy")
        source = self.current / entry["file"]
        if not source.exists():
            raise RecoveryError(f"active file is missing: {source}")

        dest = Path(destination)
        if dest.is_dir():
            dest = dest / source.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        copied_bytes = dest.read_bytes()
        dest_digest = sha256_bytes(copied_bytes)
        event = self._event(
            "table_copied",
            kind=kind,
            artifact_id=entry["artifact_id"],
            digest=entry["digest"],
            destination=str(dest),
            destination_digest=dest_digest,
            copied_by=copied_by,
        )
        return {"destination": str(dest), "digest": entry["digest"], "event": event}

    def notice_external_copy(
        self,
        kind: str,
        location: str,
        *,
        digest: str | None = None,
        copied_by: str = "other-duty-desk",
    ) -> Dict[str, Any]:
        if digest is None:
            entry = self.current_manifest().get("artifacts", {}).get(kind)
            if entry is None:
                raise RecoveryError(f"no active {kind} table")
            digest = entry["digest"]
        return self._event(
            "external_copy_noticed",
            kind=kind,
            digest=digest,
            location=location,
            copied_by=copied_by,
        )

    def tail_failed(self, note: str = "tail record unreadable") -> Dict[str, Any]:
        event = self._event("tail_failure_recorded", note=note)
        manifest = self.current_manifest()
        return {"event": event, "active": manifest.get("artifacts", {})}

    def clear_temporary_area(self) -> Dict[str, Any]:
        staged = list(self.staging.glob("*.json")) if self.staging.exists() else []
        pending = self.buffer_path.exists()
        shutil.rmtree(self.tmp, ignore_errors=True)
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.staging.mkdir(parents=True, exist_ok=True)
        result = {"removed_staging_artifacts": len(staged), "removed_partial_buffer": pending}
        self._event("temporary_area_cleared", **result)
        return result

    # --------------------------------------------------------------- status

    def status(self) -> Dict[str, Any]:
        manifest = self.current_manifest()
        arts = self.verified_artifacts()
        readiness = []
        for art in arts:
            if art["header"]["kind"] == "duty":
                r = self.readiness_for(art)
                r["revoked"] = self.is_revoked(art)
                readiness.append(r)
        staged = []
        if self.staging.exists():
            for p in self.staging.glob("*.json"):
                state = _load_json(p, {})
                head = state.get("head", {})
                staged.append(
                    {
                        "digest": head.get("digest"),
                        "artifact_id": head.get("artifact_id"),
                        "received_lines": len(state.get("lines", {})),
                    }
                )
        return {
            "root": str(self.root),
            "catalog_schema": manifest.get("schema"),
            "catalog_version": manifest.get("version"),
            "active": manifest.get("artifacts", {}),
            "verified": [
                {
                    "kind": a["header"]["kind"],
                    "generation": a["header"]["generation"],
                    "batch": a["header"]["batch"],
                    "artifact_id": a["artifact_id"],
                    "digest": a["digest"],
                    "revoked": self.is_revoked(a),
                }
                for a in sorted(
                    arts,
                    key=lambda a: (
                        a["header"]["kind"],
                        int(a["header"]["generation"]),
                        a["digest"],
                    ),
                )
            ],
            "duty_readiness": readiness,
            "staged": staged,
            "pins": self.pins(),
            "revocations": self.revocation_targets(),
            "partial_feed_bytes": self.buffer_path.stat().st_size
            if self.buffer_path.exists()
            else 0,
        }
