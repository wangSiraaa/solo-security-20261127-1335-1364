"""Deterministic replay of the application journal into directory rows."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .disk import DirectoryRow, JournalOp, serialize_directory
from .hashing import object_hash, sha256_text, short_hash


REPLAY_ENGINE_ID = "fictional-legacy-replay-v1"


@dataclass
class ReplayResult:
    rows: List[DirectoryRow]
    output_text: str
    output_sha256: str
    evidence_id: str
    applied: List[Dict[str, Any]] = field(default_factory=list)
    conflicts: List[Dict[str, Any]] = field(default_factory=list)

    def find(
        self, *, name: Optional[str] = None, entry_id: Optional[str] = None
    ) -> List[DirectoryRow]:
        out = []
        for row in self.rows:
            if entry_id is not None and row.entry_id != entry_id:
                continue
            if name is not None and row.name != name:
                continue
            out.append(row)
        return out

    def to_proof(self) -> Dict[str, Any]:
        return {
            "engine_id": REPLAY_ENGINE_ID,
            "evidence_id": self.evidence_id,
            "output_sha256": self.output_sha256,
            "row_count": len(self.rows),
            "applied_transactions": [w["tx_id"] for w in self.applied],
            "conflicts": self.conflicts,
        }


def _index_rows(rows: List[DirectoryRow]) -> Dict[str, DirectoryRow]:
    return {row.entry_id: row for row in rows}


def replay_journal(
    base_rows: List[DirectoryRow],
    ops: List[JournalOp],
    *,
    directory_sha256: Optional[str] = None,
    journal_sha256: Optional[str] = None,
) -> ReplayResult:
    rows = copy.deepcopy(base_rows)
    index = _index_rows(rows)
    witnesses: List[Dict[str, Any]] = []
    conflicts: List[Dict[str, Any]] = []

    for op in ops:
        witness: Dict[str, Any] = {
            "tx_id": op.tx_id,
            "op": op.op,
            "entry_id": op.entry_id,
            "before": None,
            "after": None,
            "status": "applied",
        }

        if op.op == "rename":
            row: Optional[DirectoryRow] = None
            if op.entry_id and op.entry_id in index:
                row = index[op.entry_id]
            elif op.old_name:
                matches = [r for r in rows if r.name == op.old_name]
                if len(matches) == 1:
                    row = matches[0]
                    index[row.entry_id] = row
            if row is None:
                witness["status"] = "conflict_missing_entry"
                conflicts.append({**witness})
                witnesses.append(witness)
                continue
            if op.old_name and row.name != op.old_name:
                witness["status"] = "conflict_old_name_mismatch"
                witness["expected_old_name"] = op.old_name
                witness["actual_name"] = row.name
                conflicts.append(dict(witness))
                # Journal semantics still identify entry_id; a real old
                # application may differ.  Keep conflict visible but apply the
                # committed rename when entry_id was supplied.
            witness["before"] = row.to_dict()
            row.name = op.name or row.name
            witness["after"] = row.to_dict()

        elif op.op in {"add", "put_row", "write_line"}:
            if not op.entry_id or not op.name:
                witness["status"] = "conflict_missing_fields"
                conflicts.append(dict(witness))
                witnesses.append(witness)
                continue
            existing = index.get(op.entry_id)
            if op.op == "add" and existing is not None:
                witness["status"] = "conflict_add_exists"
                witness["before"] = existing.to_dict()
                conflicts.append(dict(witness))
                witnesses.append(witness)
                continue
            if existing is None:
                row = DirectoryRow(
                    entry_id=op.entry_id,
                    name=op.name,
                    block_id=op.block_id,
                    pages=list(op.pages),
                )
                rows.append(row)
                index[row.entry_id] = row
            else:
                witness["before"] = existing.to_dict()
                existing.name = op.name
                existing.block_id = op.block_id or existing.block_id
                existing.pages = list(op.pages) if op.pages else existing.pages
                row = existing
            witness["after"] = row.to_dict()

        witnesses.append(witness)

    output_text = serialize_directory(rows)
    output_sha256 = sha256_text(output_text)
    digest = object_hash(
        {
            "engine_id": REPLAY_ENGINE_ID,
            "directory_sha256": directory_sha256,
            "journal_sha256": journal_sha256,
            "output_sha256": output_sha256,
            "ops": [op.raw for op in ops],
        }
    )
    evidence_id = f"DRV-REPLAY-{short_hash(digest, 16)}"
    return ReplayResult(
        rows=rows,
        output_text=output_text,
        output_sha256=output_sha256,
        evidence_id=evidence_id,
        applied=witnesses,
        conflicts=conflicts,
    )


def find_named(rows: List[DirectoryRow], name: str) -> Optional[DirectoryRow]:
    matches = [row for row in rows if row.name == name]
    return matches[0] if matches else None
