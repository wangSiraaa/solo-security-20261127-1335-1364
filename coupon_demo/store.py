"""SQLite persistence.

Persistence matters here: restarting the local process must not forget a
redemption and thereby permit a welfare benefit to be issued twice.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS materials (
  lot_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  quantity INTEGER NOT NULL CHECK(quantity >= 0),
  consumed INTEGER NOT NULL DEFAULT 0 CHECK(consumed >= 0),
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS backups (
  backup_id TEXT PRIMARY KEY,
  segment_id TEXT NOT NULL,
  state_secret TEXT NOT NULL,
  envelope_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS segments (
  segment_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  backup_id TEXT,
  status TEXT NOT NULL,
  witness_scope TEXT NOT NULL DEFAULT 'unresolved',
  observed_machines_json TEXT NOT NULL DEFAULT '[]',
  reprint_confirmed INTEGER NOT NULL DEFAULT 0,
  print_frozen INTEGER NOT NULL DEFAULT 0,
  witness_note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
  source_id TEXT PRIMARY KEY,
  machine_id TEXT NOT NULL,
  segment_id TEXT NOT NULL REFERENCES segments(segment_id),
  backup_id TEXT,
  kind TEXT NOT NULL,
  state_secret TEXT NOT NULL,
  epoch INTEGER NOT NULL DEFAULT 1,
  next_seq INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'active',
  checkout_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS witnesses (
  witness_id TEXT PRIMARY KEY,
  segment_id TEXT NOT NULL REFERENCES segments(segment_id),
  source_id TEXT NOT NULL,
  machine_id TEXT NOT NULL,
  from_seq INTEGER NOT NULL,
  to_seq INTEGER NOT NULL,
  chain_hash TEXT NOT NULL,
  envelope_json TEXT NOT NULL,
  valid_signature INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stacks (
  stack_id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  purpose TEXT NOT NULL DEFAULT 'ordinary',
  machine_id TEXT NOT NULL,
  source_id TEXT NOT NULL REFERENCES sources(source_id),
  segment_id TEXT NOT NULL REFERENCES segments(segment_id),
  material_lot_id TEXT REFERENCES materials(lot_id),
  planned_count INTEGER NOT NULL DEFAULT 0,
  print_count INTEGER NOT NULL DEFAULT 0,
  seal_id TEXT,
  seal_status TEXT NOT NULL DEFAULT 'none',
  provenance_status TEXT NOT NULL,
  custody_status TEXT NOT NULL,
  active_receipt_id TEXT,
  details_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS coupons (
  ticket_no TEXT PRIMARY KEY,
  stack_id TEXT NOT NULL REFERENCES stacks(stack_id),
  seq INTEGER NOT NULL,
  secret_hash TEXT NOT NULL,
  holder_name TEXT NOT NULL DEFAULT '',
  real_name INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL,
  correction_state TEXT NOT NULL DEFAULT 'none',
  correction_note TEXT NOT NULL DEFAULT '',
  replacement_ticket_no TEXT,
  claim_id TEXT,
  claimed_at TEXT,
  redeemed_at TEXT,
  redemption_id TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(stack_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_coupons_secret_hash ON coupons(secret_hash);
CREATE INDEX IF NOT EXISTS idx_coupons_stack ON coupons(stack_id);
CREATE INDEX IF NOT EXISTS idx_coupons_status ON coupons(status);

CREATE TABLE IF NOT EXISTS handoffs (
  handoff_id TEXT PRIMARY KEY,
  stack_id TEXT NOT NULL REFERENCES stacks(stack_id),
  from_party TEXT NOT NULL,
  to_party TEXT NOT NULL,
  complete INTEGER NOT NULL DEFAULT 0,
  documents_json TEXT NOT NULL DEFAULT '[]',
  missing_json TEXT NOT NULL DEFAULT '[]',
  signed_by TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  completed_at TEXT
);

CREATE TABLE IF NOT EXISTS counter_receipts (
  receipt_id TEXT PRIMARY KEY,
  counter_id TEXT NOT NULL,
  stack_id TEXT NOT NULL REFERENCES stacks(stack_id),
  signed_by TEXT NOT NULL,
  received_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  original_stack_id TEXT,
  retarget_reason TEXT NOT NULL DEFAULT '',
  correction_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_receipts_stack_status
  ON counter_receipts(stack_id, status);

CREATE TABLE IF NOT EXISTS claims (
  claim_id TEXT PRIMARY KEY,
  ticket_no TEXT NOT NULL REFERENCES coupons(ticket_no),
  holder_name TEXT NOT NULL,
  id_reference TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS redemptions (
  redemption_id TEXT PRIMARY KEY,
  ticket_no TEXT NOT NULL REFERENCES coupons(ticket_no),
  counter_id TEXT NOT NULL,
  secret_hash TEXT NOT NULL UNIQUE,
  benefit TEXT NOT NULL DEFAULT '虚构福利一份',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS corrections (
  correction_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  aggregate_type TEXT NOT NULL,
  aggregate_id TEXT NOT NULL,
  summary TEXT NOT NULL,
  before_json TEXT NOT NULL DEFAULT '{}',
  after_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS replacement_orders (
  replacement_id TEXT PRIMARY KEY,
  original_ticket_no TEXT NOT NULL REFERENCES coupons(ticket_no),
  correction_id TEXT NOT NULL REFERENCES corrections(correction_id),
  reason TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'pending_independent_source',
  new_stack_id TEXT,
  new_ticket_no TEXT,
  created_at TEXT NOT NULL,
  completed_at TEXT
);

CREATE TABLE IF NOT EXISTS audit_events (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_type TEXT NOT NULL,
  aggregate_type TEXT NOT NULL DEFAULT '',
  aggregate_id TEXT NOT NULL DEFAULT '',
  detail_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: str = ":memory:") -> None:
        if path != ":memory:":
            db_path = Path(path)
            db_path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
