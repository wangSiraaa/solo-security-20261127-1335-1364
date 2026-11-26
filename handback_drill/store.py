"""Durable SQLite storage.

A commit is the drill's stand-in for a persistent receipt.  Restarting the
process or calling ``/api/admin/restart`` closes and reopens this file without
mutating its facts.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=FULL;

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cabinets (
    code TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    old_generation INTEGER NOT NULL,
    new_generation INTEGER NOT NULL,
    lease_through_tick INTEGER NOT NULL,
    internal_online INTEGER NOT NULL DEFAULT 1,
    new_route_online INTEGER NOT NULL DEFAULT 1,
    switch_state TEXT NOT NULL DEFAULT 'NONE',
    switch_origin_tick INTEGER,
    switch_delivered_tick INTEGER,
    deactivation_receipt_id TEXT,
    updated_tick INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS receipts (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    cabinet TEXT,
    station TEXT,
    generation INTEGER,
    tick INTEGER NOT NULL,
    idempotency_key TEXT,
    payload TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    archive_code TEXT NOT NULL,
    title TEXT NOT NULL,
    required_cabinets TEXT NOT NULL,
    delivery_site TEXT NOT NULL,
    material_ready_tick INTEGER NOT NULL,
    deadline_tick INTEGER NOT NULL,
    patience_tick INTEGER,
    flexible_next_shift INTEGER NOT NULL DEFAULT 0,
    priority INTEGER NOT NULL DEFAULT 100,
    status TEXT NOT NULL,
    assigned_station TEXT,
    idempotency_key TEXT NOT NULL,
    created_tick INTEGER NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS vouchers (
    id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL UNIQUE,
    archive_code TEXT NOT NULL,
    station TEXT NOT NULL,
    generation INTEGER NOT NULL,
    status TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    body TEXT NOT NULL,
    created_tick INTEGER NOT NULL,
    updated_tick INTEGER NOT NULL,
    completed_tick INTEGER
);

CREATE TABLE IF NOT EXISTS acceptances (
    id TEXT PRIMARY KEY,
    voucher_id TEXT NOT NULL,
    order_id TEXT NOT NULL,
    cabinet TEXT NOT NULL,
    station TEXT NOT NULL,
    generation INTEGER NOT NULL,
    receipt_id TEXT NOT NULL UNIQUE,
    tick INTEGER NOT NULL,
    UNIQUE(voucher_id, cabinet)
);

CREATE TABLE IF NOT EXISTS todos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT,
    voucher_id TEXT,
    cabinet TEXT,
    kind TEXT NOT NULL,
    detail TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    created_tick INTEGER NOT NULL,
    resolved_tick INTEGER
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tick INTEGER NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS image_snapshots (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    tick INTEGER NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class Store:
    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(
            self.path, check_same_thread=False, isolation_level=None
        )
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self) -> None:
        with self.lock:
            self.conn.executescript(SCHEMA)

    def reopen(self) -> None:
        """Close and reopen the database, simulating a process restart."""
        with self.lock:
            try:
                self.conn.close()
            finally:
                self.conn = sqlite3.connect(
                    self.path, check_same_thread=False, isolation_level=None
                )
                self.conn.row_factory = sqlite3.Row
                self.conn.execute("PRAGMA foreign_keys=ON")
                self._init_schema()

    def close(self) -> None:
        with self.lock:
            self.conn.close()

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self.lock:
            cur = self.conn.execute(sql, tuple(params))
            return cur

    @contextmanager
    def transaction(self):
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield
                self.conn.execute("COMMIT")
            except Exception:
                self.conn.execute("ROLLBACK")
                raise

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        with self.lock:
            return self.conn.execute(sql, tuple(params)).fetchone()

    def query_all(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self.lock:
            return list(self.conn.execute(sql, tuple(params)).fetchall())

    def meta_get(self, key: str, default: Any = None) -> Any:
        row = self.query_one("SELECT value FROM meta WHERE key=?", (key,))
        if row is None:
            return default
        return json.loads(row["value"])

    def meta_set(self, key: str, value: Any) -> None:
        self.execute(
            """
            INSERT INTO meta(key, value) VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (key, json.dumps(value, ensure_ascii=False, sort_keys=True)),
        )

    def event(self, kind: str, payload: dict[str, Any] | None = None, tick: int | None = None) -> None:
        if tick is None:
            tick = int(self.meta_get("tick", 0))
        self.execute(
            "INSERT INTO events(tick, kind, payload) VALUES(?, ?, ?)",
            (tick, kind, json.dumps(payload or {}, ensure_ascii=False, sort_keys=True)),
        )

    def events(self) -> list[dict[str, Any]]:
        rows = self.query_all("SELECT * FROM events ORDER BY id")
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item["payload"])
            result.append(item)
        return result
