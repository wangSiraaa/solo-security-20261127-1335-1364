"""Durable JSON-blob store backed by sqlite.

The whole release-service state is one atomically-written snapshot.  That is
enough for a simulation and gives the property the exercise asks for: after the
service is re-opened, the ledger and audit trail (and therefore *who actually
received what, by ticket id*) are still there.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from typing import Optional


class CustodyStore:
    def __init__(self, path: Optional[str] = None):
        self.path = path
        self._lock = threading.RLock()
        if path is not None:
            parent = os.path.dirname(os.path.abspath(path))
            os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(path or ":memory:", check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS state "
            "(id INTEGER PRIMARY KEY CHECK (id = 1), data TEXT NOT NULL, "
            " updated_at TEXT NOT NULL DEFAULT (datetime('now')))"
        )
        self._conn.commit()

    def load(self) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute("SELECT data FROM state WHERE id = 1").fetchone()
        return json.loads(row[0]) if row else None

    def save(self, data: dict) -> None:
        payload = json.dumps(data, sort_keys=True)
        with self._lock:
            self._conn.execute(
                "INSERT INTO state (id, data, updated_at) VALUES (1, ?, datetime('now')) "
                "ON CONFLICT(id) DO UPDATE SET data = excluded.data, "
                "updated_at = excluded.updated_at",
                (payload,),
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "CustodyStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
