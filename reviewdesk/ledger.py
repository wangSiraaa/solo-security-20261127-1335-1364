from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .hashing import canonical_hash

ROOT = Path(__file__).resolve().parents[1]
LEDGER_PATH = ROOT / "review-desk" / "ledger.jsonl"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def append_record(kind: str, payload: dict[str, Any], *, case_id: str | None = None) -> dict[str, Any]:
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    previous_hash = "0" * 64
    seq = 1
    if LEDGER_PATH.exists() and LEDGER_PATH.stat().st_size:
        lines = LEDGER_PATH.read_text(encoding="utf-8").splitlines()
        last = json.loads(lines[-1])
        previous_hash = last.get("record_hash", previous_hash)
        seq = int(last.get("seq", len(lines))) + 1
    record = {
        "seq": seq,
        "at": utc_now(),
        "case_id": case_id or payload.get("case_id") or "UNSCOPED",
        "kind": kind,
        "payload": payload,
        "prev_hash": previous_hash,
    }
    record["record_hash"] = canonical_hash({k: v for k, v in record.items() if k != "record_hash"})
    with LEDGER_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return record


def load_records() -> list[dict[str, Any]]:
    if not LEDGER_PATH.exists():
        return []
    return [json.loads(line) for line in LEDGER_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]


def verify_chain() -> dict[str, Any]:
    records = load_records()
    previous = "0" * 64
    for record in records:
        expected_prev = record.get("prev_hash")
        if expected_prev != previous:
            return {"ok": False, "reason": f"prev hash mismatch at seq {record.get('seq')}"}
        stored = record.pop("record_hash")
        actual = canonical_hash(record)
        record["record_hash"] = stored
        if stored != actual:
            return {"ok": False, "reason": f"record hash mismatch at seq {record.get('seq')}"}
        previous = stored
    return {"ok": True, "records": len(records), "head": previous}
