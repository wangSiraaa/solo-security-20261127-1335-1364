"""A small append-only, hash-chained JSONL log.

Each line is a JSON object ``{"seq", "prev", "ts", "type", "data", "hash"}``
where ``hash = SHA256(prev || canonical_json_without_hash)``.  This gives a
cheap tamper-evidence property: any edit to a past record breaks the chain and
``verify_chain`` reports the first bad index.  Real systems would sign the head
and replicate it; here we only need "可重新统计、也能核实" (re-countable and
verifiable) for the exercise.

The log is deliberately *dumb* about domains -- each hall and the desk own a
separate log file so a hall clerk can only ever read its own post.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Callable, Dict, Iterator, Optional

GENESIS = "0" * 64


def _canonical(obj: Dict[str, Any]) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _hash_record(prev: str, body: Dict[str, Any]) -> str:
    return hashlib.sha256(prev.encode("ascii") + _canonical(body)).hexdigest()


class Ledger:
    """Append-only log backed by a JSONL file inside ``directory``."""

    def __init__(self, directory: str, name: str = "ledger.jsonl",
                 clock: Optional[Callable[[], int]] = None):
        self.directory = os.path.abspath(directory)
        os.makedirs(self.directory, exist_ok=True)
        self.path = os.path.join(self.directory, name)
        # Materialise the post file so a clerk's inbox exists even when empty.
        if not os.path.exists(self.path):
            open(self.path, "a", encoding="utf-8").close()
        self._clock = clock or (lambda: 0)
        self._head = GENESIS
        self._seq = 0
        self._count = 0
        self._load_tail()

    # -- reading ---------------------------------------------------------

    def _load_tail(self) -> None:
        if not os.path.exists(self.path):
            return
        last = None
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                last = json.loads(line)
                self._count += 1
        if last is not None:
            self._head = last["hash"]
            self._seq = last["seq"] + 1

    def __iter__(self) -> Iterator[Dict[str, Any]]:
        if not os.path.exists(self.path):
            return iter(())
        out = []
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return iter(out)

    def records(self):
        return list(self)

    def __len__(self) -> int:
        return self._count

    # -- writing ---------------------------------------------------------

    def append(self, record_type: str, data: Dict[str, Any],
               ts: Optional[int] = None) -> Dict[str, Any]:
        body = {
            "seq": self._seq,
            "prev": self._head,
            "ts": self._clock() if ts is None else ts,
            "type": record_type,
            "data": data,
        }
        h = _hash_record(self._head, body)
        rec = dict(body)
        rec["hash"] = h
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, sort_keys=True, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        self._head = h
        self._seq += 1
        self._count += 1
        return rec

    # -- verification ----------------------------------------------------

    def verify_chain(self) -> Dict[str, Any]:
        """Return ``{"ok": bool, "count": int, "bad_index": Optional[int]}``."""
        prev = GENESIS
        idx = -1
        for idx, rec in enumerate(self):
            body = {k: rec[k] for k in ("seq", "prev", "ts", "type", "data")}
            if body["prev"] != prev or rec["hash"] != _hash_record(prev, body):
                return {"ok": False, "count": idx + 1, "bad_index": idx}
            prev = rec["hash"]
        return {"ok": True, "count": idx + 1, "bad_index": None}


__all__ = ["Ledger", "GENESIS"]
