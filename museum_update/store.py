"""Durable JSON state and small slot-file storage."""
from __future__ import annotations

import json
import os
import secrets
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from .security import sha256_bytes, sha256_file


PROJECT_ROOT = Path(__file__).resolve().parent.parent
_state_env = os.environ.get("MUSEUM_STATE_DIR")
DEFAULT_STATE_DIR = Path(_state_env) if _state_env else Path.cwd() / "state"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root or DEFAULT_STATE_DIR).resolve()
        self.state_path = self.root / "controller-state.json"
        self.journal_path = self.root / "journal.jsonl"
        self.slots_root = self.root / "slots"

    def ensure_dirs(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.slots_root.mkdir(parents=True, exist_ok=True)

    def exists(self) -> bool:
        return self.state_path.is_file()

    def load(self) -> Dict[str, Any]:
        if not self.exists():
            raise FileNotFoundError(
                f"尚未初始化: {self.state_path}; 请先运行 `python -m museum_update init`"
            )
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def save(self, state: Dict[str, Any]) -> None:
        self.ensure_dirs()
        state["saved_at"] = utc_now()
        tmp = self.state_path.with_name(self.state_path.name + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.state_path)
        self._fsync_directory(self.root)

    def append_journal(self, event: Dict[str, Any]) -> None:
        self.ensure_dirs()
        line = json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
        with self.journal_path.open("a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        flags = os.O_RDONLY
        try:
            fd = os.open(path, flags)
        except OSError:
            return
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def slot_dir(self, machine_id: str, slot: str) -> Path:
        return self.slots_root / machine_id / slot

    def remove_slot_files(self, machine_id: str, slot: str) -> None:
        target = self.slot_dir(machine_id, slot)
        if target.exists():
            shutil.rmtree(target)

    @staticmethod
    def _safe_slot_rel_path(rel_path: str) -> Path:
        if not isinstance(rel_path, str) or "\\" in rel_path or rel_path.startswith("/") or "\x00" in rel_path:
            raise ValueError(f"unsafe slot path: {rel_path!r}")
        parts = rel_path.split("/")
        if any(part in ("", ".", "..") for part in parts):
            raise ValueError(f"unsafe slot path: {rel_path!r}")
        return Path(*parts)

    def write_bytes_to_slot(
        self, machine_id: str, slot: str, rel_path: str, data: bytes
    ) -> str:
        rel = self._safe_slot_rel_path(rel_path)
        root = self.slot_dir(machine_id, slot).resolve()
        target = (root / rel).resolve()
        if not target.is_relative_to(root):
            raise ValueError(f"slot path escapes slot directory: {rel_path!r}")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        return sha256_bytes(data)

    def copy_verified_payload(
        self, machine_id: str, slot: str, payload_root: Path, rel_paths: list[str]
    ) -> Dict[str, str]:
        self.remove_slot_files(machine_id, slot)
        digests: Dict[str, str] = {}
        root = self.slot_dir(machine_id, slot).resolve()
        for rel_path in rel_paths:
            rel = self._safe_slot_rel_path(rel_path)
            source = (payload_root / rel_path).resolve()
            destination = (root / rel).resolve()
            if not destination.is_relative_to(root):
                raise ValueError(f"slot path escapes slot directory: {rel_path!r}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            with destination.open("rb") as fh:
                os.fsync(fh.fileno())
            digests[rel_path] = sha256_file(destination)
        self._fsync_directory(self.slot_dir(machine_id, slot))
        return digests


def new_device_secret() -> str:
    return secrets.token_hex(32)


def add_event(
    store: Store,
    state: Dict[str, Any],
    kind: str,
    message: str,
    *,
    machine_id: Optional[str] = None,
    **details: Any,
) -> Dict[str, Any]:
    seq = int(state.get("next_event_seq", 1))
    event = {
        "seq": seq,
        "at": utc_now(),
        "shift": int(state.get("clock", {}).get("shift", 0)),
        "kind": kind,
        "machine": machine_id,
        "message": message,
        "details": details,
    }
    state["next_event_seq"] = seq + 1
    state.setdefault("events", []).append(event)
    store.append_journal(event)
    return event
