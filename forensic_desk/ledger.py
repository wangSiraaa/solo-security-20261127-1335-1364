"""Append-only, hash-chained case ledger.

The ledger is the one record source visible to both desks.  There is
intentionally no update or delete method: a changed conclusion must be a new
record, leaving the old conclusion available for review.
"""

from __future__ import annotations

import fcntl
import json
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from .hashing import GENESIS_HASH, object_hash, utc_now_iso


class LedgerError(RuntimeError):
    pass


class Ledger:
    def __init__(self, case_dir: str | Path):
        self.case_dir = Path(case_dir)
        self.case_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.case_dir / "ledger.jsonl"
        self.lock_path = self.case_dir / "ledger.lock"
        self.path.touch(exist_ok=True)

    def _read_unlocked(self) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise LedgerError(f"corrupt ledger line {line_no}: {exc}") from exc
        return records

    def records(self) -> List[Dict[str, Any]]:
        with self.lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_SH)
            try:
                return self._read_unlocked()
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def __iter__(self) -> Iterator[Dict[str, Any]]:
        return iter(self.records())

    def append(
        self,
        record_type: str,
        payload: Dict[str, Any],
        *,
        actor: str = "system",
        created_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Append a tamper-evident record and return the stored record."""
        with self.lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                existing = self._read_unlocked()
                seq = len(existing) + 1
                prev_hash = existing[-1]["hash"] if existing else GENESIS_HASH
                record_id = f"REC-{seq:06d}"
                preliminary = {
                    "seq": seq,
                    "id": record_id,
                    "type": record_type,
                    "actor": actor,
                    "recorded_at": created_at or utc_now_iso(),
                    "payload": payload,
                    "prev_hash": prev_hash,
                }
                record = dict(preliminary)
                record["hash"] = object_hash(preliminary)
                with self.path.open("a", encoding="utf-8") as out:
                    out.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                    out.flush()
                return record
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def get(self, record_id: str) -> Optional[Dict[str, Any]]:
        for record in self.records():
            if record.get("id") == record_id:
                return record
        return None

    def require(self, record_id: str) -> Dict[str, Any]:
        record = self.get(record_id)
        if record is None:
            raise KeyError(f"no such ledger record: {record_id}")
        return record

    def find(self, record_type: Optional[str] = None) -> List[Dict[str, Any]]:
        records = self.records()
        if record_type is None:
            return records
        return [r for r in records if r.get("type") == record_type]

    def latest(self, record_type: str) -> Optional[Dict[str, Any]]:
        found = self.find(record_type)
        return found[-1] if found else None

    def verify_chain(self) -> Dict[str, Any]:
        records = self.records()
        prev_hash = GENESIS_HASH
        for expected_seq, record in enumerate(records, 1):
            if record.get("seq") != expected_seq:
                raise LedgerError(f"sequence gap at record {expected_seq}")
            if record.get("prev_hash") != prev_hash:
                raise LedgerError(f"prev_hash mismatch at {record.get('id')}")
            stored_hash = record.get("hash")
            body = {k: v for k, v in record.items() if k != "hash"}
            if object_hash(body) != stored_hash:
                raise LedgerError(f"hash mismatch at {record.get('id')}")
            prev_hash = stored_hash
        return {"ok": True, "records": len(records), "head_hash": prev_hash}
