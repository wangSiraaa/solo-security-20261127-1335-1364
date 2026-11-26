#!/usr/bin/env python3
"""Local account-recovery counter.

Standard library only.  The SQLite file is the restart-safe record of
applications, frozen takeover orders, source lineage, rejected attempts and
setting revisions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent
DEFAULT_DB = Path(os.environ.get("DESK_DB", ROOT / "data" / "desk.db"))
SCHEMA_VERSION = 1


class DeskError(Exception):
    """A rule violation whose message is safe to show at the counter."""


def now_iso(at: str | None = None) -> str:
    if at:
        return at
    if os.environ.get("DESK_NOW"):
        return os.environ["DESK_NOW"]
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0).isoformat()


def jdumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def jloads(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    return json.loads(value)


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    account_id TEXT NOT NULL REFERENCES accounts(id),
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (account_id, key)
);

CREATE TABLE IF NOT EXISTS setting_revisions (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL,
    order_id TEXT,
    device_id TEXT,
    key TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT NOT NULL,
    at TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT 'committed'
);

CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    label TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'unassigned',
    sync_root TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    registered_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    kind TEXT NOT NULL,
    label TEXT NOT NULL,
    root_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'valid',
    risk_score INTEGER NOT NULL DEFAULT 0,
    valid_until TEXT,
    note TEXT,
    created_at TEXT NOT NULL
);

-- Explicit source-level relations.  relation is normally synced_copy_of,
-- shares_root, or manually_declared_dependency.
CREATE TABLE IF NOT EXISTS source_edges (
    from_source_id TEXT NOT NULL REFERENCES sources(id),
    to_source_id TEXT NOT NULL REFERENCES sources(id),
    relation TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (from_source_id, to_source_id, relation)
);

CREATE TABLE IF NOT EXISTS credentials (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    source_id TEXT NOT NULL REFERENCES sources(id),
    label TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'valid',
    issued_at TEXT NOT NULL,
    valid_until TEXT
);

-- Every reissue records the old credentials it relied on.
-- dependency_kind='trust' participates in source-independence checks;
-- 'replaces' is only an administrative link for a fresh in-person root.
CREATE TABLE IF NOT EXISTS credential_dependencies (
    credential_id TEXT NOT NULL REFERENCES credentials(id),
    parent_credential_id TEXT NOT NULL REFERENCES credentials(id),
    dependency_kind TEXT NOT NULL DEFAULT 'trust',
    note TEXT,
    PRIMARY KEY (credential_id, parent_credential_id, dependency_kind)
);

CREATE TABLE IF NOT EXISTS confirmations (
    id TEXT PRIMARY KEY,
    application_id TEXT REFERENCES applications(id),
    account_id TEXT NOT NULL REFERENCES accounts(id),
    source_id TEXT NOT NULL REFERENCES sources(id),
    credential_id TEXT REFERENCES credentials(id),
    device_id TEXT REFERENCES devices(id),
    signer TEXT,
    statement TEXT,
    signature TEXT,
    retained INTEGER NOT NULL DEFAULT 1,
    discarded_at TEXT,
    signed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS applications (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    old_device_id TEXT REFERENCES devices(id),
    new_device_id TEXT REFERENCES devices(id),
    state TEXT NOT NULL DEFAULT 'submitted', -- submitted/issued/active/rejected
    deadline TEXT,
    replacement_for_order_id TEXT,
    person_present INTEGER NOT NULL DEFAULT 0,
    note TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    application_id TEXT NOT NULL REFERENCES applications(id),
    account_id TEXT NOT NULL REFERENCES accounts(id),
    new_device_id TEXT NOT NULL REFERENCES devices(id),
    state TEXT NOT NULL DEFAULT 'issued', -- issued/collected/active/paused
    basis_hash TEXT NOT NULL,
    decision_json TEXT,
    issued_at TEXT NOT NULL,
    collected_at TEXT,
    accepted_at TEXT,
    deadline TEXT
);

-- An order freezes exactly the two selected pieces of evidence.
CREATE TABLE IF NOT EXISTS order_evidence (
    order_id TEXT NOT NULL REFERENCES orders(id),
    idx INTEGER NOT NULL CHECK (idx IN (1, 2)),
    confirmation_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    root_id TEXT NOT NULL,
    lineage_json TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    PRIMARY KEY (order_id, idx),
    UNIQUE (order_id, confirmation_id),
    UNIQUE (order_id, source_id)
);

CREATE TABLE IF NOT EXISTS pending_changes (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    device_id TEXT NOT NULL REFERENCES devices(id),
    key TEXT NOT NULL,
    proposed_value TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending', -- pending/committed/rejected
    confirmed_at TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    resolution_note TEXT
);

CREATE TABLE IF NOT EXISTS audit_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    account_id TEXT,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    result TEXT NOT NULL,
    error_code TEXT,
    detail_json TEXT
);

CREATE TABLE IF NOT EXISTS replay_runs (
    id TEXT PRIMARY KEY,
    base_order_id TEXT NOT NULL,
    branch_db TEXT NOT NULL,
    future_event TEXT NOT NULL,
    selected_pair TEXT NOT NULL,
    setting_allowed INTEGER NOT NULL,
    backfill_allowed INTEGER NOT NULL DEFAULT 0,
    remedy_order_id TEXT,
    summary TEXT NOT NULL,
    at TEXT NOT NULL
);
"""


class Desk:
    def __init__(self, db_path: str | Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Desk":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def init_schema(self) -> "Desk":
        self.conn.executescript(SCHEMA)
        self.conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        self.conn.commit()
        return self

    def reset(self) -> "Desk":
        self.close()
        if self.path.exists():
            self.path.unlink()
        for suffix in ("-wal", "-shm"):
            p = Path(str(self.path) + suffix)
            if p.exists():
                p.unlink()
        d = Desk(self.path)
        return d.init_schema()

    # ---------- generic helpers ----------

    def audit(
        self,
        actor: str,
        action: str,
        result: str = "ok",
        *,
        account_id: str | None = None,
        error_code: str | None = None,
        detail: dict[str, Any] | None = None,
        at: str | None = None,
    ) -> None:
        self.conn.execute(
            """INSERT INTO audit_events(at, account_id, actor, action, result,
                                        error_code, detail_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (now_iso(at), account_id, actor, action, result, error_code,
             jdumps(detail or {})),
        )
        self.conn.commit()

    def _get(self, table: str, row_id: str) -> sqlite3.Row:
        row = self.conn.execute(
            f"SELECT * FROM {table} WHERE id=?", (row_id,)
        ).fetchone()
        if row is None:
            raise DeskError(f"{table} 中不存在 {row_id}")
        return row

    def account(self, account_id: str) -> sqlite3.Row:
        return self._get("accounts", account_id)

    def add_account(
        self,
        account_id: str,
        display_name: str,
        *,
        note: str | None = None,
        initial_setting: tuple[str, str] = ("auto_lock_minutes", "30"),
        at: str | None = None,
    ) -> None:
        ts = now_iso(at)
        self.conn.execute(
            "INSERT INTO accounts(id, display_name, note, created_at) VALUES (?,?,?,?)",
            (account_id, display_name, note, ts),
        )
        key, value = initial_setting
        self.conn.execute(
            "INSERT INTO settings(account_id, key, value, updated_at) VALUES (?,?,?,?)",
            (account_id, key, value, ts),
        )
        self.conn.commit()
        self.audit("seed", "account.register", account_id=account_id,
                   detail={"display_name": display_name}, at=ts)

    def get_setting(self, account_id: str, key: str = "auto_lock_minutes") -> str:
        row = self.conn.execute(
            "SELECT value FROM settings WHERE account_id=? AND key=?",
            (account_id, key),
        ).fetchone()
        if row is None:
            raise DeskError(f"账号 {account_id} 没有设置 {key}")
        return row["value"]

    def add_device(
        self,
        device_id: str,
        account_id: str,
        label: str,
        *,
        role: str = "unassigned",
        sync_root: str | None = None,
        at: str | None = None,
    ) -> None:
        self.account(account_id)
        self.conn.execute(
            """INSERT INTO devices(id, account_id, label, role, sync_root, registered_at)
               VALUES (?,?,?,?,?,?)""",
            (device_id, account_id, label, role, sync_root, now_iso(at)),
        )
        self.conn.commit()
        self.audit("seed", "device.register", account_id=account_id,
                   detail={"device_id": device_id, "role": role}, at=at)

    def add_source(
        self,
        source_id: str,
        account_id: str,
        kind: str,
        label: str,
        *,
        root_id: str | None = None,
        risk_score: int = 0,
        valid_until: str | None = None,
        note: str | None = None,
        at: str | None = None,
    ) -> None:
        self.account(account_id)
        self.conn.execute(
            """INSERT INTO sources(id, account_id, kind, label, root_id, status,
                                   risk_score, valid_until, note, created_at)
               VALUES (?,?,?,?,?, 'valid', ?,?,?,?)""",
            (source_id, account_id, kind, label, root_id or source_id,
             risk_score, valid_until, note, now_iso(at)),
        )
        self.conn.commit()

    def add_credential(
        self,
        credential_id: str,
        account_id: str,
        source_id: str,
        label: str,
        *,
        valid_until: str | None = None,
        at: str | None = None,
    ) -> None:
        self._get("sources", source_id)
        self.conn.execute(
            """INSERT INTO credentials(id, account_id, source_id, label,
                                       status, issued_at, valid_until)
               VALUES (?,?,?,?, 'valid', ?,?)""",
            (credential_id, account_id, source_id, label, now_iso(at), valid_until),
        )
        self.conn.commit()

    def reissue_credential(
        self,
        credential_id: str,
        account_id: str,
        source_id: str,
        parent_credential_ids: list[str],
        label: str,
        *,
        dependency_kind: str = "trust",
        note: str | None = None,
        valid_until: str | None = None,
        at: str | None = None,
    ) -> None:
        """Issue a credential and explicitly record its old dependencies."""
        self.add_credential(credential_id, account_id, source_id, label,
                            valid_until=valid_until, at=at)
        for parent_id in parent_credential_ids:
            self._get("credentials", parent_id)
            self.conn.execute(
                """INSERT INTO credential_dependencies(
                       credential_id, parent_credential_id,
                       dependency_kind, note)
                   VALUES (?,?,?,?)""",
                (credential_id, parent_id, dependency_kind, note),
            )
        self.conn.commit()
        self.audit(
            "counter", "credential.reissue", account_id=account_id,
            detail={
                "credential_id": credential_id,
                "parents": parent_credential_ids,
                "dependency_kind": dependency_kind,
            },
            at=at,
        )

    def link_source(
        self,
        from_source_id: str,
        to_source_id: str,
        relation: str,
        *,
        note: str | None = None,
        at: str | None = None,
    ) -> None:
        a = self._get("sources", from_source_id)
        b = self._get("sources", to_source_id)
        self.conn.execute(
            """INSERT OR IGNORE INTO source_edges(
                   from_source_id, to_source_id, relation, note, created_at)
               VALUES (?,?,?,?,?)""",
            (from_source_id, to_source_id, relation, note, now_iso(at)),
        )
        if relation == "synced_copy_of":
            self.conn.execute(
                "UPDATE sources SET root_id=? WHERE id=?",
                (b["root_id"], from_source_id),
            )
        self.conn.commit()

        # A lineage change can invalidate an already-completed order's frozen
        # pair on its next use.  Revalidate eagerly so `status` is not stale.
        paused_orders: list[str] = []
        for row in self.conn.execute(
            "SELECT id FROM orders WHERE account_id=? AND state='active'",
            (a["account_id"],),
        ).fetchall():
            try:
                self.assert_order_usable(row["id"], at=at)
            except DeskError:
                paused_orders.append(row["id"])

        self.audit(
            "counter", "source.link", account_id=a["account_id"],
            detail={"from": from_source_id, "to": to_source_id,
                    "relation": relation, "paused_orders": paused_orders},
            at=at,
        )

    def revoke_source(
        self,
        source_id: str,
        reason: str,
        *,
        actor: str = "desk",
        at: str | None = None,
    ) -> list[str]:
        src = self._get("sources", source_id)
        ts = now_iso(at)
        self.conn.execute(
            "UPDATE sources SET status='revoked', note=COALESCE(note,'') || ? WHERE id=?",
            (f"\n[{ts}] revoked: {reason}", source_id),
        )
        # Lazy security boundary: orders frozen on this source are paused.
        orders = [r["order_id"] for r in self.conn.execute(
            "SELECT DISTINCT order_id FROM order_evidence WHERE source_id=?",
            (source_id,),
        )]
        for oid in orders:
            self.conn.execute(
                "UPDATE orders SET state='paused' WHERE id=? AND state != 'paused'",
                (oid,),
            )
        self.conn.commit()
        self.audit(
            actor, "source.revoke", "ok", account_id=src["account_id"],
            detail={"source_id": source_id, "reason": reason,
                    "paused_orders": orders},
            at=ts,
        )
        return orders

    # ---------- lineage / independence ----------

    def lineage(self, source_id: str) -> set[str]:
        """Return source_id plus every source it directly/transitively relies on."""
        self._get("sources", source_id)
        seen: set[str] = set()
        stack = [source_id]
        while stack:
            sid = stack.pop()
            if sid in seen:
                continue
            seen.add(sid)

            for row in self.conn.execute(
                """SELECT to_source_id FROM source_edges
                   WHERE from_source_id=?
                     AND relation IN ('depends_on','synced_copy_of','shares_root')""",
                (sid,),
            ):
                stack.append(row["to_source_id"])

            # Credential reissue dependencies are the important ledger.
            for row in self.conn.execute(
                """
                SELECT DISTINCT pc.source_id AS source_id
                FROM credential_dependencies cd
                JOIN credentials cc ON cc.id = cd.credential_id
                JOIN credentials pc ON pc.id = cd.parent_credential_id
                WHERE cc.source_id = ?
                  AND COALESCE(cd.dependency_kind, 'trust') = 'trust'
                """,
                (sid,),
            ):
                stack.append(row["source_id"])
        return seen

    def independent_sources(self, source_a: str, source_b: str) -> tuple[bool, str]:
        if source_a == source_b:
            return False, "same source row"
        a = self._get("sources", source_a)
        b = self._get("sources", source_b)
        if a["root_id"] and a["root_id"] == b["root_id"]:
            return False, f"shared root {a['root_id']}"
        la = self.lineage(source_a)
        lb = self.lineage(source_b)
        common = la & lb
        if common:
            return False, "shared lineage: " + ", ".join(sorted(common))
        return True, "disjoint roots and lineage"

    # ---------- confirmations / applications ----------

    def add_confirmation(
        self,
        confirmation_id: str,
        account_id: str,
        source_id: str,
        *,
        application_id: str | None = None,
        credential_id: str | None = None,
        device_id: str | None = None,
        signer: str | None = None,
        statement: str | None = "I confirm this recovery request.",
        at: str | None = None,
    ) -> None:
        self.account(account_id)
        self._get("sources", source_id)
        if application_id:
            self._get("applications", application_id)
        statement = statement or "I confirm this recovery request."
        signature = "sig-" + hashlib.sha256(
            (confirmation_id + source_id + statement).encode()
        ).hexdigest()[:24]
        self.conn.execute(
            """INSERT INTO confirmations(
                   id, application_id, account_id, source_id, credential_id,
                   device_id, signer, statement, signature, retained, signed_at)
               VALUES (?,?,?,?,?,?,?,?,?,1,?)""",
            (confirmation_id, application_id, account_id, source_id,
             credential_id, device_id, signer, statement, signature, now_iso(at)),
        )
        self.conn.commit()

    def submit_application(
        self,
        application_id: str,
        account_id: str,
        *,
        old_device_id: str | None = None,
        new_device_id: str | None = None,
        deadline: str | None = None,
        note: str | None = None,
        replacement_for_order_id: str | None = None,
        person_present: bool = False,
        at: str | None = None,
    ) -> None:
        self.account(account_id)
        for did in (old_device_id, new_device_id):
            if did:
                d = self._get("devices", did)
                if d["account_id"] != account_id:
                    raise DeskError(f"设备 {did} 不属于账号 {account_id}")

        if replacement_for_order_id:
            old_order = self._get("orders", replacement_for_order_id)
            if old_order["account_id"] != account_id:
                raise DeskError("ORDER_ACCOUNT_MISMATCH")
            if not person_present:
                ts = now_iso(at)
                self.audit("user", "application.replacement_submit", "rejected",
                           account_id=account_id,
                           error_code="PERSONAL_APPEARANCE_REQUIRED",
                           detail={"replacement_for_order_id": replacement_for_order_id},
                           at=ts)
                raise DeskError(
                    "PERSONAL_APPEARANCE_REQUIRED: 接管单冻结后改用别的来源，"
                    "必须本人重新到场后另立新申请"
                )

        self.conn.execute(
            """INSERT INTO applications(id, account_id, old_device_id,
                                        new_device_id, state, deadline,
                                        replacement_for_order_id, person_present,
                                        note, created_at)
               VALUES (?,?,?,?, 'submitted', ?,?,?,?,?)""",
            (application_id, account_id, old_device_id, new_device_id,
             deadline, replacement_for_order_id, 1 if person_present else 0,
             note, now_iso(at)),
        )
        self.conn.commit()
        self.audit("user", "application.submit", account_id=account_id,
                   detail={"application_id": application_id,
                           "replacement_for_order_id": replacement_for_order_id,
                           "person_present": person_present}, at=at)

    def _eligible_confirmations(self, application_id: str, at: str) -> list[sqlite3.Row]:
        app = self._get("applications", application_id)
        rows = list(self.conn.execute(
            """
            SELECT c.*, s.status AS source_status, s.valid_until AS source_valid_until,
                   s.risk_score, s.kind AS source_kind, s.root_id,
                   cr.status AS credential_status
            FROM confirmations c
            JOIN sources s ON s.id = c.source_id
            LEFT JOIN credentials cr ON cr.id = c.credential_id
            WHERE c.application_id = ? AND c.retained = 1
            ORDER BY c.id
            """,
            (application_id,),
        ))
        eligible: list[sqlite3.Row] = []
        for r in rows:
            if r["source_status"] != "valid":
                continue
            if r["source_valid_until"] and r["source_valid_until"] < at:
                continue
            if r["credential_status"] and r["credential_status"] != "valid":
                continue
            eligible.append(r)
        return eligible

    def choose_pair(
        self, application_id: str, *, at: str | None = None
    ) -> tuple[list[sqlite3.Row], dict[str, Any]]:
        app = self._get("applications", application_id)
        ts = now_iso(at)
        candidates = self._eligible_confirmations(application_id, ts)
        pairs: list[tuple[tuple, list[sqlite3.Row], dict[str, str]]] = []
        for i, a in enumerate(candidates):
            for b in candidates[i + 1:]:
                ok, why = self.independent_sources(a["source_id"], b["source_id"])
                if not ok:
                    continue
                score = int(a["risk_score"] or 0) + int(b["risk_score"] or 0)
                # Longer validity is preferred, hence negative timestamp proxy
                # is not used heavily; risk then stable ID tie-break is enough.
                key = (score, a["id"], b["id"])
                pairs.append((key, [a, b], {"independence": why, "risk_score": str(score)}))
        if not pairs:
            # Provide a useful reason for the first apparent pair.
            reasons = []
            for i, a in enumerate(candidates):
                for b in candidates[i + 1:]:
                    ok, why = self.independent_sources(a["source_id"], b["source_id"])
                    if not ok:
                        reasons.append(f"{a['source_id']} ~ {b['source_id']}: {why}")
            detail = "; ".join(reasons) or "没有两份有效确认"
            raise DeskError("NO_INDEPENDENT_PAIR: " + detail)
        pairs.sort(key=lambda x: x[0])
        _, chosen, why = pairs[0]
        decision = {
            "at": ts,
            "candidate_count": len(candidates),
            "chosen_confirmation_ids": [r["id"] for r in chosen],
            "chosen_source_ids": [r["source_id"] for r in chosen],
            "policy": "filter valid -> require disjoint lineage/root -> lowest risk -> stable ID",
            "rationale": why,
            "extra_in_person_exchange_required_now": False,
            "extra_exchange_reason": "两份当前证据有效且独立，无需为同一单额外换证",
        }
        return chosen, decision

    @staticmethod
    def _evidence_fingerprint(row: sqlite3.Row, lineage: list[str]) -> str:
        payload = {
            "confirmation_id": row["id"],
            "source_id": row["source_id"],
            "root_id": row["root_id"],
            "signed_at": row["signed_at"],
            "lineage": sorted(lineage),
        }
        return hashlib.sha256(jdumps(payload).encode()).hexdigest()

    def issue_order(
        self,
        application_id: str,
        order_id: str | None = None,
        *,
        at: str | None = None,
    ) -> str:
        app = self._get("applications", application_id)
        if app["state"] not in ("submitted", "rejected"):
            raise DeskError(f"申请 {application_id} 状态为 {app['state']}，不能重复开单")
        ts = now_iso(at)
        chosen, decision = self.choose_pair(application_id, at=ts)
        oid = order_id or f"ord-{application_id}"
        if self.conn.execute("SELECT 1 FROM orders WHERE id=?", (oid,)).fetchone():
            raise DeskError(f"接管单 {oid} 已存在")

        evidence_rows = []
        fingerprints = []
        for idx, row in enumerate(chosen, start=1):
            lineage = sorted(self.lineage(row["source_id"]))
            fp = self._evidence_fingerprint(row, lineage)
            fingerprints.append(fp)
            evidence_rows.append(
                (oid, idx, row["id"], row["source_id"], row["root_id"],
                 jdumps(lineage), fp)
            )
        basis_hash = hashlib.sha256("|".join(sorted(fingerprints)).encode()).hexdigest()

        self.conn.execute(
            """INSERT INTO orders(id, application_id, account_id, new_device_id,
                                  state, basis_hash, decision_json, issued_at, deadline)
               VALUES (?,?,?,?, 'issued', ?,?,?,?)""",
            (oid, application_id, app["account_id"], app["new_device_id"],
             basis_hash, jdumps(decision), ts, app["deadline"]),
        )
        for values in evidence_rows:
            self.conn.execute(
                """INSERT INTO order_evidence(order_id, idx, confirmation_id,
                                              source_id, root_id, lineage_json,
                                              fingerprint)
                   VALUES (?,?,?,?,?,?,?)""",
                values,
            )
        self.conn.execute("UPDATE applications SET state='issued' WHERE id=?",
                          (application_id,))

        # The order keeps exactly the two selected proofs.  Proof material for
        # every other confirmation in this intake round is destroyed; only a
        # revocation tombstone keyed by source remains.
        selected_ids = [r["id"] for r in chosen]
        placeholders = ",".join("?" for _ in selected_ids)
        unselected = self.conn.execute(
            f"""SELECT id FROM confirmations
                WHERE application_id=? AND retained=1
                  AND id NOT IN ({placeholders})""",
            [application_id, *selected_ids],
        ).fetchall()
        for r in unselected:
            self.conn.execute(
                """UPDATE confirmations
                   SET retained=0, statement=NULL, signature=NULL,
                       device_id=NULL, signer=NULL, discarded_at=?
                   WHERE id=?""",
                (ts, r["id"]),
            )
        self.conn.commit()
        self.audit(
            "program", "order.issue", account_id=app["account_id"],
            detail={"order_id": oid, "decision": decision,
                    "discarded_confirmation_ids": [r["id"] for r in unselected]},
            at=ts,
        )
        return oid

    def claim_order(self, order_id: str, device_id: str, *, at: str | None = None) -> None:
        order = self._get("orders", order_id)
        device = self._get("devices", device_id)
        if device["id"] != order["new_device_id"]:
            self.audit(device_id, "order.claim", "rejected",
                       account_id=order["account_id"],
                       error_code="WRONG_DEVICE",
                       detail={"order_id": order_id}, at=at)
            raise DeskError(f"WRONG_DEVICE: 接管单只能由新机 {order['new_device_id']} 领取")
        if order["state"] not in ("issued",):
            if order["state"] == "collected":
                return
            raise DeskError(f"订单状态 {order['state']}，不能领单")
        ts = now_iso(at)
        self.conn.execute(
            "UPDATE orders SET state='collected', collected_at=? WHERE id=?",
            (ts, order_id),
        )
        self.conn.commit()
        self.audit(device_id, "order.claim", account_id=order["account_id"],
                   detail={"order_id": order_id}, at=ts)

    def sign_order_receipt(
        self, order_id: str, device_id: str, *, at: str | None = None
    ) -> None:
        order = self._get("orders", order_id)
        if device_id != order["new_device_id"]:
            self.audit(device_id, "order.sign_receipt", "rejected",
                       account_id=order["account_id"],
                       error_code="WRONG_DEVICE",
                       detail={"order_id": order_id}, at=at)
            raise DeskError("WRONG_DEVICE: 只有领单的新设备能签收接管确认")
        if order["state"] == "active":
            return
        if order["state"] != "collected":
            raise DeskError(
                f"ORDER_NOT_COLLECTED: 当前状态 {order['state']}；必须先在新机领单，尚未签收"
            )
        ts = now_iso(at)
        # Do not accept an order whose frozen basis became invalid meanwhile.
        self.assert_order_usable(order_id, at=ts, for_acceptance=True)
        self.conn.execute(
            "UPDATE orders SET state='active', accepted_at=? WHERE id=?",
            (ts, order_id),
        )
        self.conn.execute(
            "UPDATE applications SET state='active' WHERE id=?",
            (order["application_id"],),
        )
        self.conn.commit()
        self.audit(device_id, "order.sign_receipt",
                   account_id=order["account_id"],
                   detail={"order_id": order_id, "takeover_effective": True}, at=ts)

    def assert_order_usable(
        self, order_id: str, *, at: str | None = None,
        for_acceptance: bool = False,
    ) -> sqlite3.Row:
        order = self._get("orders", order_id)
        ts = now_iso(at)
        if not for_acceptance and order["state"] in ("issued", "collected"):
            if order["state"] == "collected":
                msg = "只有领单、尚未签收；新机尚不能办理设置"
            else:
                msg = "接管单尚未由新机领取和签收"
            self.audit(order["new_device_id"], "order.use", "rejected",
                       account_id=order["account_id"],
                       error_code="ORDER_NOT_EFFECTIVE",
                       detail={"order_id": order_id, "state": order["state"]}, at=ts)
            raise DeskError(f"ORDER_NOT_EFFECTIVE: {msg}")

        evidence = list(self.conn.execute(
            "SELECT * FROM order_evidence WHERE order_id=? ORDER BY idx",
            (order_id,),
        ))
        if len(evidence) != 2:
            raise DeskError("ORDER_EVIDENCE_CORRUPT: 接管单没有恰好两份固化证据")

        current_sources = []
        for ev in evidence:
            src = self._get("sources", ev["source_id"])
            if src["status"] != "valid":
                self._pause_order(order_id, ts)
                self.audit(order["new_device_id"], "order.validate", "rejected",
                           account_id=order["account_id"],
                           error_code="EVIDENCE_REVOKED",
                           detail={"order_id": order_id,
                                   "source_id": src["id"],
                                   "status": src["status"]}, at=ts)
                raise DeskError(
                    f"EVIDENCE_REVOKED: 固化来源 {src['id']} 已{src['status']}；"
                    "该接管单必须暂停"
                )
            if src["valid_until"] and src["valid_until"] < ts:
                self._pause_order(order_id, ts)
                raise DeskError(f"EVIDENCE_EXPIRED: 来源 {src['id']} 已过期")
            current_sources.append(src["id"])

        ok, why = self.independent_sources(current_sources[0], current_sources[1])
        if not ok:
            self._pause_order(order_id, ts)
            self.audit(order["new_device_id"], "order.validate", "rejected",
                       account_id=order["account_id"],
                       error_code="NOT_INDEPENDENT",
                       detail={"order_id": order_id, "why": why}, at=ts)
            raise DeskError(f"NOT_INDEPENDENT: {why}")

        if not for_acceptance and order["state"] == "paused":
            self.audit(order["new_device_id"], "order.use", "rejected",
                       account_id=order["account_id"], error_code="ORDER_PAUSED",
                       detail={"order_id": order_id}, at=ts)
            raise DeskError("ORDER_PAUSED: 接管单处于暂停状态，不能继续办理")
        return order

    def _pause_order(self, order_id: str, ts: str) -> None:
        self.conn.execute(
            "UPDATE orders SET state='paused' WHERE id=? AND state != 'paused'",
            (order_id,),
        )
        self.conn.commit()

    def backfill_evidence(
        self, order_id: str, confirmation_id: str, *, at: str | None = None
    ) -> None:
        """Attempt to use an unselected/discarded proof on an old order."""
        order = self._get("orders", order_id)
        cnf = self.conn.execute(
            "SELECT * FROM confirmations WHERE id=?", (confirmation_id,)
        ).fetchone()
        ts = now_iso(at)
        if cnf is None or not cnf["retained"]:
            code = "THIRD_PROOF_DISCARDED"
            msg = "第三份证明材料已丢弃，不能补签或回填昨天已冻结的接管单"
        else:
            code = "ORDER_FROZEN"
            msg = "接管单只固化本次选用的两份证据；改用来源必须本人重新到场并立新单"
        self.audit("unknown", "order.backfill", "rejected",
                   account_id=order["account_id"], error_code=code,
                   detail={"order_id": order_id,
                           "confirmation_id": confirmation_id}, at=ts)
        raise DeskError(f"{code}: {msg}")

    def decide_in_person_remedy(
        self,
        order_id: str,
        *,
        at: str | None = None,
        person_present: bool = True,
    ) -> dict[str, Any]:
        """Program decision: no ceremony while valid; otherwise only a new order."""
        order = self._get("orders", order_id)
        ts = now_iso(at)
        try:
            self.assert_order_usable(order_id, at=ts)
            decision = {
                "needed": False,
                "new_ceremony_allowed": False,
                "action": "none",
                "reason": "冻结的两份证据仍有效且独立，无需截止前换证",
            }
        except DeskError as exc:
            if not person_present:
                action = "pause_until_person_arrives"
                allowed = False
                reason = "证据失效，但本人尚未重新到场，不能换来源或补签"
            elif order["deadline"] and ts > order["deadline"]:
                action = "pause_past_deadline"
                allowed = False
                reason = f"已超过截止时间 {order['deadline']}，不能在本轮当面换证"
            else:
                action = "open_new_in_person_order"
                allowed = True
                reason = "原单不能补签；可在截止前由本人到场建立新的信任根和新接管单"
            decision = {
                "needed": True,
                "new_ceremony_allowed": allowed,
                "action": action,
                "reason": reason,
                "validation_error": str(exc),
                "deadline": order["deadline"],
            }
        self.audit("program", "remedy.decision",
                   "ok" if decision["new_ceremony_allowed"] or not decision["needed"] else "rejected",
                   account_id=order["account_id"],
                   detail={"order_id": order_id, **decision}, at=ts)
        return decision

    # ---------- settings and old-device drafts ----------

    def change_setting(
        self,
        account_id: str,
        device_id: str,
        new_value: str,
        *,
        key: str = "auto_lock_minutes",
        order_id: str | None = None,
        at: str | None = None,
        actor: str | None = None,
    ) -> int:
        ts = now_iso(at)
        device = self._get("devices", device_id)
        if device["account_id"] != account_id:
            raise DeskError("DEVICE_ACCOUNT_MISMATCH")
        if order_id is None:
            row = self.conn.execute(
                """SELECT id FROM orders
                   WHERE account_id=? AND new_device_id=? AND state='active'
                   ORDER BY accepted_at DESC LIMIT 1""",
                (account_id, device_id),
            ).fetchone()
            if not row:
                self.audit(device_id, "setting.change", "rejected",
                           account_id=account_id, error_code="NO_ACTIVE_TAKEOVER",
                           detail={"key": key}, at=ts)
                raise DeskError("NO_ACTIVE_TAKEOVER: 该设备没有已签收的有效接管单")
            order_id = row["id"]

        order = self.assert_order_usable(order_id, at=ts)
        if order["new_device_id"] != device_id:
            self.audit(device_id, "setting.change", "rejected",
                       account_id=account_id, error_code="WRONG_DEVICE",
                       detail={"order_id": order_id}, at=ts)
            raise DeskError("WRONG_DEVICE: 只有接管单上的新设备能凭单改设置")

        old_value = self.get_setting(account_id, key)
        cur = self.conn.execute(
            """INSERT INTO setting_revisions(account_id, order_id, device_id,
                                             key, old_value, new_value, at)
               VALUES (?,?,?,?,?,?,?)""",
            (account_id, order_id, device_id, key, old_value, str(new_value), ts),
        )
        rev = cur.lastrowid
        self.conn.execute(
            "UPDATE settings SET value=?, updated_at=? WHERE account_id=? AND key=?",
            (str(new_value), ts, account_id, key),
        )
        self.conn.commit()
        self.audit(actor or device_id, "setting.change",
                   account_id=account_id,
                   detail={"revision": rev, "order_id": order_id, "key": key,
                           "old": old_value, "new": str(new_value)}, at=ts)
        return rev

    def start_pending_change(
        self,
        change_id: str,
        account_id: str,
        device_id: str,
        proposed_value: str,
        *,
        key: str = "auto_lock_minutes",
        at: str | None = None,
    ) -> None:
        device = self._get("devices", device_id)
        if device["account_id"] != account_id:
            raise DeskError("DEVICE_ACCOUNT_MISMATCH")
        ts = now_iso(at)
        self.conn.execute(
            """INSERT INTO pending_changes(id, account_id, device_id, key,
                                           proposed_value, status, created_at)
               VALUES (?,?,?,?,?, 'pending', ?)""",
            (change_id, account_id, device_id, key, str(proposed_value), ts),
        )
        self.conn.commit()
        self.audit(device_id, "setting.draft_start", account_id=account_id,
                   detail={"change_id": change_id, "key": key,
                           "proposed": proposed_value}, at=ts)

    def apply_pending_change(
        self,
        change_id: str,
        *,
        device_id: str | None = None,
        via_order_id: str | None = None,
        at: str | None = None,
    ) -> int | None:
        chg = self._get("pending_changes", change_id)
        ts = now_iso(at)
        actor = device_id or chg["device_id"]

        if device_id and device_id != chg["device_id"]:
            raise DeskError("WRONG_DEVICE: 草稿只能由原设备继续办理")

        if via_order_id:
            order = self._get("orders", via_order_id)
            self.audit(actor, "setting.draft_apply_via_takeover", "rejected",
                       account_id=chg["account_id"],
                       error_code="DRAFT_NOT_CARRIED",
                       detail={"change_id": change_id,
                               "order_id": via_order_id}, at=ts)
            raise DeskError(
                "DRAFT_NOT_CARRIED: 原设备上未确认的变更不会随接管单自动落地；"
                "请在原设备按原流程确认"
            )

        if chg["status"] == "committed":
            return None
        if not chg["confirmed_at"]:
            self.audit(actor, "setting.draft_apply", "rejected",
                       account_id=chg["account_id"],
                       error_code="CHANGE_UNCONFIRMED",
                       detail={"change_id": change_id}, at=ts)
            raise DeskError(
                "CHANGE_UNCONFIRMED: 这项旧机变更只有开始记录，尚无必要确认，不能落地"
            )

        old_value = self.get_setting(chg["account_id"], chg["key"])
        cur = self.conn.execute(
            """INSERT INTO setting_revisions(account_id, order_id, device_id,
                                             key, old_value, new_value, at, reason)
               VALUES (?,?,?,?,?,?,?, 'old-device-confirmed')""",
            (chg["account_id"], None, chg["device_id"], chg["key"], old_value,
             chg["proposed_value"], ts),
        )
        rev = cur.lastrowid
        self.conn.execute(
            "UPDATE settings SET value=?, updated_at=? WHERE account_id=? AND key=?",
            (chg["proposed_value"], ts, chg["account_id"], chg["key"]),
        )
        self.conn.execute(
            "UPDATE pending_changes SET status='committed', resolved_at=? WHERE id=?",
            (ts, change_id),
        )
        self.conn.commit()
        return rev

    # ---------- reporting ----------

    def graph_mermaid(self, account_id: str | None = None) -> str:
        accounts = []
        if account_id:
            accounts = [self.account(account_id)]
        else:
            accounts = list(self.conn.execute("SELECT * FROM accounts ORDER BY id"))

        lines = ["flowchart LR"]
        class_defs = [
            "classDef paper fill:#fff4ce,stroke:#8a6d00;",
            "classDef witness fill:#e3f2fd,stroke:#1565c0;",
            "classDef key fill:#e8f5e9,stroke:#2e7d32;",
            "classDef oldkey fill:#ffebee,stroke:#c62828;",
            "classDef person fill:#f3e5f5,stroke:#6a1b9a;",
            "classDef source fill:#f5f5f5,stroke:#666;",
        ]
        lines.extend("  " + v for v in class_defs)
        class_names = {
            "paper": "paper", "paper_booklet": "paper",
            "witness": "witness", "witness_desk": "witness",
            "device_key": "key", "synced_device": "key",
            "old_key": "oldkey",
            "in_person": "person",
        }

        for acct in accounts:
            aid = acct["id"]
            safe = aid.replace("-", "_")
            lines.append(f'  subgraph {safe}["{acct["display_name"]} ({aid})"]')
            srcs = self.conn.execute(
                "SELECT * FROM sources WHERE account_id=? ORDER BY id", (aid,)
            ).fetchall()
            for s in srcs:
                node = s["id"].replace("-", "_")
                status = "" if s["status"] == "valid" else f" [{s['status']}]"
                lines.append(f'    {node}["{s["label"]}{status}"]')
                cls = class_names.get(s["kind"], "source")
                lines.append(f"    class {node} {cls};")
            lines.append("  end")

        # Explicit edges.
        q = """SELECT e.*, s.account_id FROM source_edges e
               JOIN sources s ON s.id=e.from_source_id"""
        params: list[Any] = []
        if account_id:
            q += " WHERE s.account_id=?"
            params.append(account_id)
        for e in self.conn.execute(q + " ORDER BY e.from_source_id", params):
            a = e["from_source_id"].replace("-", "_")
            b = e["to_source_id"].replace("-", "_")
            label = {"synced_copy_of": "同步副本",
                     "depends_on": "依赖",
                     "shares_root": "共同根"}.get(e["relation"], e["relation"])
            style = "-.->" if e["relation"] == "synced_copy_of" else "-->"
            lines.append(f"  {a} {style}|{label}| {b}")

        # Credential dependency edges projected to source nodes.
        q2 = """
            SELECT DISTINCT cs.id AS from_source, ps.id AS to_source,
                   cd.dependency_kind
            FROM credential_dependencies cd
            JOIN credentials cc ON cc.id=cd.credential_id
            JOIN credentials pc ON pc.id=cd.parent_credential_id
            JOIN sources cs ON cs.id=cc.source_id
            JOIN sources ps ON ps.id=pc.source_id
            WHERE cs.id != ps.id
        """
        params2: list[Any] = []
        if account_id:
            q2 += " AND cs.account_id=?"
            params2.append(account_id)
        for e in self.conn.execute(q2, params2):
            a = e["from_source"].replace("-", "_")
            b = e["to_source"].replace("-", "_")
            if e["dependency_kind"] == "replaces":
                lines.append(f"  {a} -.->|行政替换；非信任血缘| {b}")
            else:
                lines.append(f"  {a} -->|补发凭据依赖旧凭据| {b}")

        if len(lines) == 1:
            lines.append('  empty["No sources registered"]')
        return "\n".join(lines) + "\n"

    def history_rows(self) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            """SELECT sr.*, a.display_name
               FROM setting_revisions sr JOIN accounts a ON a.id=sr.account_id
               ORDER BY sr.seq"""
        ))

    def verify(self) -> list[str]:
        problems: list[str] = []
        # Every order has exactly two evidence rows.
        for o in self.conn.execute("SELECT id FROM orders"):
            n = self.conn.execute(
                "SELECT COUNT(*) c FROM order_evidence WHERE order_id=?", (o["id"],)
            ).fetchone()["c"]
            if n != 2:
                problems.append(f"order {o['id']} has {n} evidence rows")
        # No discarded proof material remains.
        bad = self.conn.execute(
            """SELECT id FROM confirmations
               WHERE retained=0 AND (statement IS NOT NULL OR signature IS NOT NULL)"""
        ).fetchall()
        for r in bad:
            problems.append(f"discarded confirmation {r['id']} still has material")
        # Settings equal latest revision value.
        for s in self.conn.execute("SELECT account_id,key,value FROM settings"):
            latest = self.conn.execute(
                """SELECT new_value FROM setting_revisions
                   WHERE account_id=? AND key=? ORDER BY seq DESC LIMIT 1""",
                (s["account_id"], s["key"]),
            ).fetchone()
            if latest and latest["new_value"] != s["value"]:
                problems.append(
                    f"setting {s['account_id']}/{s['key']} does not match latest revision"
                )
        return problems


# Imported after Desk definition so scenario functions can use helpers.
sys.path.insert(0, str(ROOT))
import scenarios  # noqa: E402


def _print_ok(message: str) -> None:
    print(f"OK  {message}")


def _cmd_demo(args: argparse.Namespace) -> int:
    return scenarios.run_demo(args.db)


def _cmd_init(args: argparse.Namespace) -> int:
    with Desk(args.db).init_schema() as d:
        _print_ok(f"initialized {d.path}")
    return 0


def _cmd_reset(args: argparse.Namespace) -> int:
    d = Desk(args.db).reset()
    d.close()
    _print_ok(f"reset empty database at {args.db}")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    with Desk(args.db).init_schema() as d:
        accounts = d.conn.execute("SELECT * FROM accounts ORDER BY id").fetchall()
        if not accounts:
            print("(empty desk; run `./desk demo` or `./desk seed-demo-data`)")
            return 0
        for a in accounts:
            value = d.get_setting(a["id"]) if d.conn.execute(
                "SELECT 1 FROM settings WHERE account_id=? AND key='auto_lock_minutes'",
                (a["id"],),
            ).fetchone() else "(unset)"
            print(f"\n{a['id']}  {a['display_name']}  auto_lock_minutes={value}")
            if a["note"]:
                print(f"  note: {a['note']}")
            for o in d.conn.execute(
                "SELECT * FROM orders WHERE account_id=? ORDER BY issued_at",
                (a["id"],),
            ):
                ev = d.conn.execute(
                    "SELECT source_id FROM order_evidence WHERE order_id=? ORDER BY idx",
                    (o["id"],),
                ).fetchall()
                pair = " + ".join(r["source_id"] for r in ev) or "(none)"
                print(f"  order {o['id']}: {o['state']:<9} pair=[{pair}]")
            pending = d.conn.execute(
                "SELECT id,key,proposed_value,status FROM pending_changes WHERE account_id=?",
                (a["id"],),
            ).fetchall()
            for p in pending:
                print(f"  draft {p['id']}: {p['key']}->{p['proposed_value']} [{p['status']}]")
    return 0


def _cmd_history(args: argparse.Namespace) -> int:
    with Desk(args.db).init_schema() as d:
        rows = d.history_rows()
        if not rows:
            print("(no committed setting revisions)")
        for r in rows:
            print(f"#{r['seq']:>2} {r['at']}  {r['account_id']}  "
                  f"{r['key']}: {r['old_value']} -> {r['new_value']}  "
                  f"device={r['device_id']} order={r['order_id'] or '-'}  "
                  f"reason={r['reason']}")

    if getattr(args, "all_branches", False):
        replay_dir = Path(args.db).resolve().parent / "replay"
        for branch_db in sorted(replay_dir.glob("*.db")):
            print(f"\n-- branch {branch_db.relative_to(ROOT)} --")
            with Desk(branch_db).init_schema() as bd:
                rows = bd.history_rows()
                if not rows:
                    print("(no branch revisions)")
                for r in rows:
                    print(f"#{r['seq']:>2} {r['at']}  {r['account_id']}  "
                          f"{r['key']}: {r['old_value']} -> {r['new_value']}  "
                          f"device={r['device_id']} order={r['order_id'] or '-'}")
    return 0


def _cmd_audit(args: argparse.Namespace) -> int:
    q = "SELECT * FROM audit_events"
    params: list[Any] = []
    if args.account:
        q += " WHERE account_id=?"
        params.append(args.account)
    q += " ORDER BY seq DESC LIMIT ?"
    params.append(args.limit)
    with Desk(args.db).init_schema() as d:
        for r in d.conn.execute(q, params):
            detail = jloads(r["detail_json"], {})
            print(f"{r['seq']:>4} {r['at']} {r['result']:<8} "
                  f"{r['actor']} {r['action']}"
                  + (f" [{r['error_code']}]" if r["error_code"] else "")
                  + (f" {jdumps(detail)}" if detail and args.verbose else ""))
    return 0


def _cmd_graph(args: argparse.Namespace) -> int:
    with Desk(args.db).init_schema() as d:
        if args.format == "mermaid":
            print(d.graph_mermaid(args.account))
        else:
            for a in d.conn.execute("SELECT id, display_name FROM accounts ORDER BY id"):
                if args.account and a["id"] != args.account:
                    continue
                print(f"{a['id']} — {a['display_name']}")
                for s in d.conn.execute(
                    "SELECT id,kind,label,status,root_id FROM sources WHERE account_id=? ORDER BY id",
                    (a["id"],),
                ):
                    print(f"  ({s['kind']}, {s['status']}, root={s['root_id']}) {s['id']} {s['label']}")
                for e in d.conn.execute(
                    """SELECT from_source_id,to_source_id,relation FROM source_edges
                       WHERE from_source_id IN (SELECT id FROM sources WHERE account_id=?)""",
                    (a["id"],),
                ):
                    print(f"    {e['from_source_id']} --{e['relation']}--> {e['to_source_id']}")
                for e in d.conn.execute(
                    """SELECT DISTINCT cs.id AS from_source, ps.id AS to_source,
                              cd.dependency_kind
                       FROM credential_dependencies cd
                       JOIN credentials cc ON cc.id=cd.credential_id
                       JOIN credentials pc ON pc.id=cd.parent_credential_id
                       JOIN sources cs ON cs.id=cc.source_id
                       JOIN sources ps ON ps.id=pc.source_id
                       WHERE cs.account_id=? AND cs.id != ps.id""",
                    (a["id"],),
                ):
                    label = ("replaces(行政替换)" if e["dependency_kind"] == "replaces"
                             else "reissue-depends-on")
                    print(f"    {e['from_source']} --{label}--> {e['to_source']}")
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    with Desk(args.db).init_schema() as d:
        problems = d.verify()
        if problems:
            for p in problems:
                print("PROBLEM", p)
            return 1
        print("OK  every frozen order has exactly two proofs; discarded material is gone; "
              "settings match revisions")
    return 0


def _cmd_account_add(args: argparse.Namespace) -> int:
    with Desk(args.db).init_schema() as d:
        d.add_account(args.id, args.name, note=args.note, at=args.at)
    _print_ok(f"account {args.id}")
    return 0


def _cmd_device_add(args: argparse.Namespace) -> int:
    with Desk(args.db).init_schema() as d:
        d.add_device(args.id, args.account, args.label, role=args.role,
                     sync_root=args.sync_root, at=args.at)
    _print_ok(f"device {args.id}")
    return 0


def _cmd_source_add(args: argparse.Namespace) -> int:
    with Desk(args.db).init_schema() as d:
        d.add_source(args.id, args.account, args.kind, args.label,
                     root_id=args.root_id, risk_score=args.risk,
                     valid_until=args.valid_until, note=args.note, at=args.at)
    _print_ok(f"source {args.id}")
    return 0


def _cmd_credential_add(args: argparse.Namespace) -> int:
    with Desk(args.db).init_schema() as d:
        d.add_credential(args.id, args.account, args.source, args.label,
                         valid_until=args.valid_until, at=args.at)
    _print_ok(f"credential {args.id}")
    return 0


def _cmd_reissue(args: argparse.Namespace) -> int:
    with Desk(args.db).init_schema() as d:
        d.reissue_credential(args.id, args.account, args.source, args.parent,
                             args.label, dependency_kind=args.kind,
                             note=args.note, valid_until=args.valid_until, at=args.at)
    _print_ok(f"credential {args.id} reissued with parents {args.parent}")
    return 0


def _cmd_confirm(args: argparse.Namespace) -> int:
    with Desk(args.db).init_schema() as d:
        d.add_confirmation(args.id, args.account, args.source,
                          application_id=args.app, credential_id=args.credential,
                          device_id=args.device, signer=args.signer,
                          statement=args.statement, at=args.at)
    _print_ok(f"confirmation {args.id}")
    return 0


def _cmd_app_submit(args: argparse.Namespace) -> int:
    with Desk(args.db).init_schema() as d:
        d.submit_application(args.id, args.account, old_device_id=args.old,
                              new_device_id=args.new, deadline=args.deadline,
                              note=args.note,
                              replacement_for_order_id=args.replacement_for,
                              person_present=args.person_present, at=args.at)
    _print_ok(f"application {args.id}")
    return 0


def _cmd_order_issue(args: argparse.Namespace) -> int:
    with Desk(args.db).init_schema() as d:
        oid = d.issue_order(args.application, args.order_id, at=args.at)
        row = d._get("orders", oid)
        decision = jloads(row["decision_json"], {})
        _print_ok(f"order {oid}; selected={decision.get('chosen_source_ids')}")
    return 0


def _cmd_order_claim(args: argparse.Namespace) -> int:
    with Desk(args.db).init_schema() as d:
        d.claim_order(args.order, args.device, at=args.at)
    _print_ok(f"{args.device} claimed {args.order}")
    return 0


def _cmd_order_sign(args: argparse.Namespace) -> int:
    with Desk(args.db).init_schema() as d:
        d.sign_order_receipt(args.order, args.device, at=args.at)
    _print_ok(f"{args.device} signed receipt for {args.order}; takeover active")
    return 0


def _cmd_setting_change(args: argparse.Namespace) -> int:
    with Desk(args.db).init_schema() as d:
        rev = d.change_setting(args.account, args.device, args.value,
                               key=args.key, order_id=args.order, at=args.at)
    _print_ok(f"committed revision #{rev}: {args.key}={args.value}")
    return 0


def _cmd_change_start(args: argparse.Namespace) -> int:
    with Desk(args.db).init_schema() as d:
        d.start_pending_change(args.id, args.account, args.device, args.value,
                               key=args.key, at=args.at)
    _print_ok(f"pending change {args.id}")
    return 0


def _cmd_change_apply(args: argparse.Namespace) -> int:
    with Desk(args.db).init_schema() as d:
        rev = d.apply_pending_change(args.id, device_id=args.device,
                                     via_order_id=args.via_order, at=args.at)
        if rev is None:
            _print_ok("change was already committed")
        else:
            _print_ok(f"committed old-device revision #{rev}")
    return 0


def _cmd_revoke(args: argparse.Namespace) -> int:
    with Desk(args.db).init_schema() as d:
        orders = d.revoke_source(args.source, args.reason, actor=args.actor, at=args.at)
    _print_ok(f"revoked {args.source}; paused orders={orders}")
    return 0


def _cmd_link_sync(args: argparse.Namespace) -> int:
    with Desk(args.db).init_schema() as d:
        d.link_source(args.child, args.parent, "synced_copy_of", note=args.note, at=args.at)
    _print_ok(f"{args.child} is now a sync copy of {args.parent}")
    return 0


def _cmd_backfill(args: argparse.Namespace) -> int:
    with Desk(args.db).init_schema() as d:
        d.backfill_evidence(args.order, args.confirmation, at=args.at)
    return 0


def _cmd_remedy_decide(args: argparse.Namespace) -> int:
    with Desk(args.db).init_schema() as d:
        decision = d.decide_in_person_remedy(
            args.order, at=args.at, person_present=not args.person_absent)
    print(jdumps(decision))
    return 0


def _cmd_replay(args: argparse.Namespace) -> int:
    with Desk(args.db).init_schema() as d:
        results = scenarios.run_dana_replays(d)
    for r in results:
        print(f"\n[{r['id']}] {r['future_event']}")
        print(f"  branch: {r['branch_db']}")
        print(f"  frozen order setting allowed: {r['frozen_order_setting_allowed']}")
        print(f"  state: {r['frozen_order_state']}")
        if r.get("remedy_order_id"):
            print(f"  remedy: {r['remedy_order_id']} / {r['remedy_setting']}")
        print(f"  {r['summary']}")
    return 0


def _cmd_replay_list(args: argparse.Namespace) -> int:
    with Desk(args.db).init_schema() as d:
        exists = d.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='replay_runs'"
        ).fetchone()
        if not exists:
            print("(no replay runs; run `./desk demo` or `./desk replay-dana`)")
            return 0
        rows = d.conn.execute("SELECT * FROM replay_runs ORDER BY id").fetchall()
        if not rows:
            print("(no replay runs; run `./desk demo` or `./desk replay-dana`)")
            return 0
        for r in rows:
            print(f"[{r['id']}] base={r['base_order_id']} pair={r['selected_pair']}")
            print(f"  event: {r['future_event']}")
            print(f"  branch: {r['branch_db']}")
            print(f"  frozen_order_setting_allowed={bool(r['setting_allowed'])} "
                  f"backfill_allowed={bool(r['backfill_allowed'])} "
                  f"remedy={r['remedy_order_id'] or '-'}")
            print(f"  {r['summary']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="desk", description="Local account recovery counter")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_db(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--db", default=str(DEFAULT_DB), help="SQLite database path")

    sp = sub.add_parser("demo", help="reset and run all fictional scenarios")
    add_db(sp); sp.set_defaults(func=_cmd_demo)

    sp = sub.add_parser("init"); add_db(sp); sp.set_defaults(func=_cmd_init)
    sp = sub.add_parser("reset", help="delete and recreate the database")
    add_db(sp); sp.set_defaults(func=_cmd_reset)

    sp = sub.add_parser("status"); add_db(sp); sp.set_defaults(func=_cmd_status)
    sp = sub.add_parser("history", aliases=["changes"]); add_db(sp)
    sp.add_argument("--all-branches", action="store_true",
                    help="also read persistent replay branch databases next to --db")
    sp.set_defaults(func=_cmd_history)

    sp = sub.add_parser("audit"); add_db(sp)
    sp.add_argument("--account"); sp.add_argument("--limit", type=int, default=50)
    sp.add_argument("-v", "--verbose", action="store_true")
    sp.set_defaults(func=_cmd_audit)

    sp = sub.add_parser("graph"); add_db(sp)
    sp.add_argument("--account"); sp.add_argument("--format", choices=["mermaid", "ascii"], default="mermaid")
    sp.set_defaults(func=_cmd_graph)

    sp = sub.add_parser("verify"); add_db(sp); sp.set_defaults(func=_cmd_verify)
    sp = sub.add_parser("replay-dana", help="replay both overnight futures from seeded Dana base")
    add_db(sp); sp.set_defaults(func=_cmd_replay)

    sp = sub.add_parser("replays", help="list persisted replay conclusions")
    add_db(sp); sp.set_defaults(func=_cmd_replay_list)

    sp = sub.add_parser("account-add"); add_db(sp)
    sp.add_argument("id"); sp.add_argument("name"); sp.add_argument("--note"); sp.add_argument("--at")
    sp.set_defaults(func=_cmd_account_add)

    sp = sub.add_parser("device-add"); add_db(sp)
    sp.add_argument("id"); sp.add_argument("account"); sp.add_argument("label")
    sp.add_argument("--role", default="unassigned"); sp.add_argument("--sync-root")
    sp.add_argument("--at"); sp.set_defaults(func=_cmd_device_add)

    sp = sub.add_parser("source-add"); add_db(sp)
    sp.add_argument("id"); sp.add_argument("account")
    sp.add_argument("kind", help="paper_booklet/witness_desk/device_key/old_key/in_person/...")
    sp.add_argument("label"); sp.add_argument("--root-id")
    sp.add_argument("--risk", dest="risk", type=int, default=0)
    sp.add_argument("--valid-until"); sp.add_argument("--note"); sp.add_argument("--at")
    sp.set_defaults(func=_cmd_source_add)

    sp = sub.add_parser("credential-add"); add_db(sp)
    sp.add_argument("id"); sp.add_argument("account"); sp.add_argument("source")
    sp.add_argument("label"); sp.add_argument("--valid-until"); sp.add_argument("--at")
    sp.set_defaults(func=_cmd_credential_add)

    sp = sub.add_parser("credential-reissue"); add_db(sp)
    sp.add_argument("id"); sp.add_argument("account"); sp.add_argument("source")
    sp.add_argument("label"); sp.add_argument("--parent", nargs="+", required=True)
    sp.add_argument("--kind", default="trust", choices=["trust", "replaces"])
    sp.add_argument("--note"); sp.add_argument("--valid-until"); sp.add_argument("--at")
    sp.set_defaults(func=_cmd_reissue)

    sp = sub.add_parser("confirm"); add_db(sp)
    sp.add_argument("id"); sp.add_argument("account"); sp.add_argument("source")
    sp.add_argument("--app"); sp.add_argument("--credential"); sp.add_argument("--device")
    sp.add_argument("--signer"); sp.add_argument("--statement"); sp.add_argument("--at")
    sp.set_defaults(func=_cmd_confirm)

    sp = sub.add_parser("application-submit"); add_db(sp)
    sp.add_argument("id"); sp.add_argument("account"); sp.add_argument("--old")
    sp.add_argument("--new"); sp.add_argument("--deadline"); sp.add_argument("--note")
    sp.add_argument("--replacement-for", help="old frozen order being replaced")
    sp.add_argument("--person-present", action="store_true",
                     help="required when changing sources after an order was frozen")
    sp.add_argument("--at"); sp.set_defaults(func=_cmd_app_submit)

    sp = sub.add_parser("order-issue"); add_db(sp)
    sp.add_argument("application"); sp.add_argument("order_id", nargs="?")
    sp.add_argument("--at"); sp.set_defaults(func=_cmd_order_issue)

    sp = sub.add_parser("order-claim"); add_db(sp)
    sp.add_argument("order"); sp.add_argument("device"); sp.add_argument("--at")
    sp.set_defaults(func=_cmd_order_claim)

    sp = sub.add_parser("order-sign"); add_db(sp)
    sp.add_argument("order"); sp.add_argument("device"); sp.add_argument("--at")
    sp.set_defaults(func=_cmd_order_sign)

    sp = sub.add_parser("setting-change"); add_db(sp)
    sp.add_argument("account"); sp.add_argument("device"); sp.add_argument("value")
    sp.add_argument("--key", default="auto_lock_minutes"); sp.add_argument("--order")
    sp.add_argument("--at"); sp.set_defaults(func=_cmd_setting_change)

    sp = sub.add_parser("change-start"); add_db(sp)
    sp.add_argument("id"); sp.add_argument("account"); sp.add_argument("device")
    sp.add_argument("value"); sp.add_argument("--key", default="auto_lock_minutes")
    sp.add_argument("--at"); sp.set_defaults(func=_cmd_change_start)

    sp = sub.add_parser("change-apply"); add_db(sp)
    sp.add_argument("id"); sp.add_argument("--device"); sp.add_argument("--via-order")
    sp.add_argument("--at"); sp.set_defaults(func=_cmd_change_apply)

    sp = sub.add_parser("source-revoke"); add_db(sp)
    sp.add_argument("source"); sp.add_argument("reason"); sp.add_argument("--actor", default="desk")
    sp.add_argument("--at"); sp.set_defaults(func=_cmd_revoke)

    sp = sub.add_parser("source-link-sync"); add_db(sp)
    sp.add_argument("child"); sp.add_argument("parent"); sp.add_argument("--note")
    sp.add_argument("--at"); sp.set_defaults(func=_cmd_link_sync)

    sp = sub.add_parser("backfill"); add_db(sp)
    sp.add_argument("order"); sp.add_argument("confirmation"); sp.add_argument("--at")
    sp.set_defaults(func=_cmd_backfill)

    sp = sub.add_parser("remedy-decide"); add_db(sp)
    sp.add_argument("order"); sp.add_argument("--at")
    sp.add_argument("--person-absent", action="store_true",
                    help="account holder has not appeared in person")
    sp.set_defaults(func=_cmd_remedy_decide)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except DeskError as exc:
        print(f"ERROR  {exc}", file=sys.stderr)
        return 2
    except sqlite3.Error as exc:
        print(f"DATABASE ERROR: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
