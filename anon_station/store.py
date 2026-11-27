"""Durable local-file state with an inter-process lock and atomic replacement."""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Iterator

from .errors import StateError
from .security import load_or_create_key

DEFAULT_CONFIG: Dict[str, Any] = {
    "version": 1,
    # reservation: first accepted question durably holds a slot.
    # onwrite: first question only returns a signed try voucher.
    "policy": "reservation",
    "slots": 1,
    "reservation_ttl_seconds": 3600,
    "partial_ttl_seconds": 86400,
    "witness_ttl_seconds": 600,
}


def write_json_atomic(path: Path, payload: Dict[str, Any], mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def read_json(path: Path) -> Dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise StateError(f"state file not found: {path}", path=str(path)) from exc
    except json.JSONDecodeError as exc:
        raise StateError(f"state file is corrupt: {path}: {exc}", path=str(path)) from exc


class Store:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    @property
    def config_path(self) -> Path:
        return self.root / "config.json"

    @property
    def cabinet_path(self) -> Path:
        return self.root / "cabinet.json"

    @property
    def key_path(self) -> Path:
        return self.root / "gateway.key"

    @property
    def lock_path(self) -> Path:
        return self.root / "state.lock"

    @property
    def deliveries_dir(self) -> Path:
        return self.root / "deliveries"

    @classmethod
    def initialize(
        cls,
        root: str | Path,
        slots: int,
        policy: str = "reservation",
        reservation_ttl_seconds: int = 3600,
        partial_ttl_seconds: int = 86400,
        witness_ttl_seconds: int = 600,
        force: bool = False,
    ) -> "Store":
        if slots < 1:
            raise StateError("a cabinet must have at least one slot", slots=slots)
        if policy not in {"reservation", "onwrite"}:
            raise StateError("unknown policy", policy=policy)
        store = cls(root)
        store.root.mkdir(parents=True, exist_ok=True)
        if store.config_path.exists() or store.cabinet_path.exists():
            if not force:
                raise StateError(
                    "state directory is already initialized; use --force to reset",
                    root=str(store.root),
                )
            store.cabinet_path.unlink(missing_ok=True)
            store.config_path.unlink(missing_ok=True)
        load_or_create_key(store.key_path)
        config = dict(DEFAULT_CONFIG)
        config.update(
            {
                "policy": policy,
                "slots": int(slots),
                "reservation_ttl_seconds": int(reservation_ttl_seconds),
                "partial_ttl_seconds": int(partial_ttl_seconds),
                "witness_ttl_seconds": int(witness_ttl_seconds),
            }
        )
        write_json_atomic(store.config_path, config)
        cabinet = {
            "version": 1,
            "slots": [
                {"index": index, "order_id": None, "state": "empty", "last_order_id": None}
                for index in range(1, int(slots) + 1)
            ],
            "orders": {},
        }
        write_json_atomic(store.cabinet_path, cabinet)
        store.deliveries_dir.mkdir(parents=True, exist_ok=True)
        return store

    def require_initialized(self) -> None:
        if not self.config_path.exists() or not self.cabinet_path.exists():
            raise StateError(
                "state directory is not initialized; run `init` first",
                root=str(self.root),
            )

    def key(self) -> bytes:
        self.require_initialized()
        return load_or_create_key(self.key_path)

    def config(self) -> Dict[str, Any]:
        self.require_initialized()
        return read_json(self.config_path)

    @contextmanager
    def _locked(self, shared: bool = False) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+") as handle:
            flag = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
            fcntl.flock(handle.fileno(), flag)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def read_cabinet(self) -> Dict[str, Any]:
        self.require_initialized()
        with self._locked(shared=True):
            return read_json(self.cabinet_path)

    def update(self, mutator: Callable[[Dict[str, Any]], Any]) -> Any:
        self.require_initialized()
        with self._locked(shared=False):
            cabinet = read_json(self.cabinet_path)
            result = mutator(cabinet)
            write_json_atomic(self.cabinet_path, cabinet)
            return result
