"""Crash-consistent dual-volume store.

Physical layout produced under ``root``::

    journal.log        append-only commit records (the durable index of tx)
    vol1/wal.log        append-only page writes that actually flushed to vol1
    vol2/wal.log        append-only page writes that actually flushed to vol2
    vol1/pages.json     materialised pages (convenience copy, NOT trusted by recovery)
    vol2/pages.json
    vol1/cache.json     volatile cache snapshot at the kill instant (NOT durable)
    vol2/cache.json
    power.json          the power telemetry the facility actually reported

Only ``journal.log`` + ``vol*/wal.log`` are ground truth for recovery.  The
``cache.json`` files exist so a careless recovery can be shown to resurrect
phantom writes that were never flushed -- the drill's central trap.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from .model import Op


def _append_jsonl(path: str, record: Dict[str, Any]) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def _write_json(path: str, obj: Any) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, sort_keys=True, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _read_jsonl(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    out: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


class Store:
    def __init__(self, root: str) -> None:
        self.root = root
        self.vols = ("vol1", "vol2")
        os.makedirs(root, exist_ok=True)
        for v in self.vols:
            os.makedirs(os.path.join(root, v), exist_ok=True)
        # truncate any prior run artifacts so a re-run is clean
        for name in ("journal.log",):
            self._truncate(os.path.join(root, name))
        for v in self.vols:
            self._truncate(os.path.join(root, v, "wal.log"))
        self._pages: Dict[str, Dict[int, Dict[str, Any]]] = {v: {} for v in self.vols}
        # volatile: accepted but not yet flushed.  Lost on crash.
        self._pending: Dict[str, Op] = {}

    @staticmethod
    def _truncate(path: str) -> None:
        with open(path, "w", encoding="utf-8"):
            pass

    # -- write path -----------------------------------------------------
    def accept(self, op: Op) -> None:
        """Server took the write into its (volatile) cache; nothing durable yet."""
        self._pending[op.op_id] = op

    def flush(self, op: Op, tick: int) -> None:
        """The write point actually landed on a spindle/journal."""
        op.flushed = True
        op.flush_tick = tick
        page = {
            "op_id": op.op_id,
            "txid": op.txid,
            "classroom": op.classroom,
            "assignment": op.assignment,
            "cabinet": op.cabinet,
            "kind": op.kind,
            "vol": op.vol,
            "page_no": op.page_no,
            "content": op.content,
            "tick": tick,
            "seq": op.seq,
        }
        if op.kind == "commit":
            page["deps"] = list(op.deps)
            _append_jsonl(os.path.join(self.root, "journal.log"), page)
        else:
            assert op.vol in self.vols
            _append_jsonl(os.path.join(self.root, op.vol, "wal.log"), page)
            self._pages[op.vol][op.page_no] = {
                "page_no": op.page_no,
                "kind": op.kind,
                "content": op.content,
                "txid": op.txid,
                "tick": tick,
            }
        self._pending.pop(op.op_id, None)
        self._materialise(op.vol)

    def _materialise(self, vol: Optional[str]) -> None:
        if vol in self.vols:
            _write_json(
                os.path.join(self.root, vol, "pages.json"),
                {str(k): v for k, v in sorted(self._pages[vol].items())},
            )

    # -- crash ----------------------------------------------------------
    def freeze_cache(self) -> Dict[str, Any]:
        """Snapshot volatile cache at the kill instant.  Lost, not durable."""
        per: Dict[str, Dict[str, Dict[str, Any]]] = {v: {} for v in self.vols}
        for op in self._pending.values():
            target = op.vol if op.vol in self.vols else "_commit"
            per.setdefault(target, {})[op.op_id] = {
                "op_id": op.op_id,
                "txid": op.txid,
                "classroom": op.classroom,
                "assignment": op.assignment,
                "kind": op.kind,
                "page_no": op.page_no,
                "content": op.content,
            }
        for v in self.vols:
            _write_json(os.path.join(self.root, v, "cache.json"), per.get(v, {}))
        _write_json(
            os.path.join(self.root, "cache_commit.json"), per.get("_commit", {})
        )
        return {"pending_ops": [op.op_id for op in self._pending.values()]}

    def write_power(self, telemetry: Dict[str, Any]) -> None:
        _write_json(os.path.join(self.root, "power.json"), telemetry)

    def accept_count(self) -> int:
        return sum(1 for _ in self._pending)

    @property
    def pending_ids(self) -> List[str]:
        return sorted(self._pending)


# ---------------------------------------------------------------------------
# Recovery reads ONLY the durable logs (+ power.json).  It never imports the
# config, the in-memory engine, or the cache snapshots.
# ---------------------------------------------------------------------------
def load_durable(root: str) -> Dict[str, Any]:
    journal = _read_jsonl(os.path.join(root, "journal.log"))
    wal = {v: _read_jsonl(os.path.join(root, v, "wal.log")) for v in ("vol1", "vol2")}
    power = None
    ppath = os.path.join(root, "power.json")
    if os.path.exists(ppath):
        with open(ppath, "r", encoding="utf-8") as fh:
            power = json.load(fh)
    return {"journal": journal, "wal": wal, "power": power}
