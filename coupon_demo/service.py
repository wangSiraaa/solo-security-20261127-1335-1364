"""Core coupon provenance, custody, correction and redemption service."""
from __future__ import annotations

import hmac
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from .adapter import LocalSealedSourceAdapter, fingerprint_secret
from .store import Store


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


class CouponError(Exception):
    code = "coupon_error"
    http_status = 400

    def __init__(self, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(CouponError):
    code = "not_found"
    http_status = 404


class ValidationError(CouponError):
    code = "validation_error"
    http_status = 400


class ProvenanceError(CouponError):
    code = "provenance_error"
    http_status = 422


class EligibilityError(CouponError):
    code = "eligibility_blocked"
    http_status = 409


class ConflictError(CouponError):
    code = "conflict"
    http_status = 409


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


class CouponService:
    def __init__(
        self,
        db_path: str = ":memory:",
        *,
        store: Store | None = None,
        adapter: LocalSealedSourceAdapter | None = None,
    ) -> None:
        self.store = store or Store(db_path)
        self.adapter = adapter or LocalSealedSourceAdapter()
        self.lock = threading.RLock()

    def close(self) -> None:
        self.store.close()

    @property
    def conn(self) -> sqlite3.Connection:
        return self.store.conn

    # ---------- generic helpers ----------

    def _audit(
        self,
        event_type: str,
        aggregate_type: str = "",
        aggregate_id: str = "",
        detail: dict[str, Any] | None = None,
    ) -> None:
        self.conn.execute(
            """INSERT INTO audit_events
               (event_type, aggregate_type, aggregate_id, detail_json, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                event_type,
                aggregate_type,
                aggregate_id,
                json.dumps(detail or {}, ensure_ascii=False, sort_keys=True),
                now_iso(),
            ),
        )

    def _correction(
        self,
        kind: str,
        aggregate_type: str,
        aggregate_id: str,
        summary: str,
        *,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        correction_id: str | None = None,
    ) -> str:
        cid = correction_id or new_id("CORR")
        self.conn.execute(
            """INSERT INTO corrections
               (correction_id, kind, aggregate_type, aggregate_id, summary,
                before_json, after_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                cid,
                kind,
                aggregate_type,
                aggregate_id,
                summary,
                json.dumps(before or {}, ensure_ascii=False, sort_keys=True),
                json.dumps(after or {}, ensure_ascii=False, sort_keys=True),
                now_iso(),
            ),
        )
        return cid

    def _get_row(self, table: str, key_column: str, key: str) -> sqlite3.Row:
        row = self.conn.execute(
            f"SELECT * FROM {table} WHERE {key_column} = ?", (key,)
        ).fetchone()
        if row is None:
            raise NotFoundError(f"{table.rstrip('s')} not found: {key}")
        return row

    def reset_data(self) -> None:
        """Delete local demo data without dropping the schema."""
        with self.lock:
            tables = [
                "audit_events",
                "replacement_orders",
                "corrections",
                "redemptions",
                "claims",
                "counter_receipts",
                "handoffs",
                "coupons",
                "stacks",
                "witnesses",
                "sources",
                "segments",
                "backups",
                "materials",
            ]
            with self.conn:
                for table in tables:
                    self.conn.execute(f"DELETE FROM {table}")
                self.conn.execute(
                    "DELETE FROM sqlite_sequence WHERE name='audit_events'"
                )

    # ---------- material registration ----------

    def register_material(
        self,
        *,
        lot_id: str | None = None,
        kind: str = "blank_ticket_stock",
        quantity: int = 10,
        note: str = "",
    ) -> dict[str, Any]:
        if quantity <= 0:
            raise ValidationError("quantity must be positive")
        lot_id = lot_id or new_id("LOT")
        with self.lock, self.conn:
            exists = self.conn.execute(
                "SELECT 1 FROM materials WHERE lot_id=?", (lot_id,)
            ).fetchone()
            if exists:
                raise ConflictError(f"material lot already exists: {lot_id}")
            self.conn.execute(
                """INSERT INTO materials
                   (lot_id, kind, quantity, consumed, note, created_at)
                   VALUES (?, ?, ?, 0, ?, ?)""",
                (lot_id, kind, quantity, note, now_iso()),
            )
            self._audit("material_registered", "material", lot_id, {"quantity": quantity})
        return self.get_material(lot_id)

    def get_material(self, lot_id: str) -> dict[str, Any]:
        row = self._get_row("materials", "lot_id", lot_id)
        return dict(row)

    # ---------- sealed sources / backups ----------

    def create_shared_backup(
        self,
        backup_id: str | None = None,
        *,
        seed: str | None = None,
        segment_id: str | None = None,
    ) -> dict[str, Any]:
        backup_id = backup_id or new_id("BACKUP")
        created = self.adapter.create_backup(
            backup_id, seed=seed, segment_id=segment_id
        )
        with self.lock, self.conn:
            self.conn.execute(
                """INSERT INTO segments
                   (segment_id, kind, backup_id, status, witness_scope,
                    observed_machines_json, created_at, updated_at)
                   VALUES (?, 'restored_backup', ?, 'old_unresolved', 'unresolved',
                           '[]', ?, ?)""",
                (created["segment_id"], backup_id, now_iso(), now_iso()),
            )
            self.conn.execute(
                """INSERT INTO backups
                   (backup_id, segment_id, state_secret, envelope_json, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    backup_id,
                    created["segment_id"],
                    created["state_secret"],
                    json.dumps(created["envelope"], ensure_ascii=False),
                    now_iso(),
                ),
            )
            self._audit(
                "backup_created",
                "backup",
                backup_id,
                {"segment_id": created["segment_id"]},
            )
        return {
            "backup_id": backup_id,
            "segment_id": created["segment_id"],
            "envelope": created["envelope"],
        }

    def restore_source_from_backup(
        self,
        backup_id: str,
        machine_id: str,
        *,
        source_id: str | None = None,
        start_seq: int = 0,
    ) -> dict[str, Any]:
        backup = self._get_row("backups", "backup_id", backup_id)
        restored = self.adapter.restore_from_backup(
            machine_id=machine_id,
            backup_id=backup_id,
            segment_id=backup["segment_id"],
            state_secret=backup["state_secret"],
            source_id=source_id,
            start_seq=start_seq,
        )
        with self.lock, self.conn:
            self.conn.execute(
                """INSERT INTO sources
                   (source_id, machine_id, segment_id, backup_id, kind,
                    state_secret, epoch, next_seq, status, checkout_json, created_at)
                   VALUES (?, ?, ?, ?, 'restored_backup', ?, ?, ?, 'active', ?, ?)""",
                (
                    restored["source_id"],
                    machine_id,
                    restored["segment_id"],
                    backup_id,
                    restored["state_secret"],
                    restored["epoch"],
                    restored["next_seq"],
                    json.dumps(restored["envelope"], ensure_ascii=False),
                    now_iso(),
                ),
            )
            self._audit(
                "source_restored",
                "source",
                restored["source_id"],
                {"machine_id": machine_id, "backup_id": backup_id},
            )
        return self.get_source(restored["source_id"])

    def install_independent_source(
        self,
        machine_id: str,
        *,
        source_id: str | None = None,
        seed: str | None = None,
        freeze_old_state: bool = True,
    ) -> dict[str, Any]:
        created = self.adapter.new_independent_source(
            machine_id=machine_id, source_id=source_id, seed=seed
        )
        with self.lock, self.conn:
            self.conn.execute(
                """INSERT INTO segments
                   (segment_id, kind, backup_id, status, witness_scope,
                    observed_machines_json, print_frozen, created_at, updated_at)
                   VALUES (?, 'independent', NULL, 'independent_active',
                           'single_machine', ?, 0, ?, ?)""",
                (
                    created["segment_id"],
                    json.dumps([machine_id]),
                    now_iso(),
                    now_iso(),
                ),
            )
            self.conn.execute(
                """INSERT INTO sources
                   (source_id, machine_id, segment_id, backup_id, kind,
                    state_secret, epoch, next_seq, status, checkout_json, created_at)
                   VALUES (?, ?, ?, NULL, 'independent_seed', ?, ?, ?, 'active', ?, ?)""",
                (
                    created["source_id"],
                    machine_id,
                    created["segment_id"],
                    created["state_secret"],
                    created["epoch"],
                    created["next_seq"],
                    json.dumps(created["envelope"], ensure_ascii=False),
                    now_iso(),
                ),
            )
            frozen_segments: list[str] = []
            if freeze_old_state:
                rows = self.conn.execute(
                    """SELECT segment_id FROM segments
                       WHERE kind='restored_backup' AND print_frozen=0
                       AND reprint_confirmed=0"""
                ).fetchall()
                frozen_segments = [r["segment_id"] for r in rows]
                self.conn.execute(
                    """UPDATE segments SET print_frozen=1, updated_at=?
                       WHERE kind='restored_backup' AND reprint_confirmed=0""",
                    (now_iso(),),
                )
                self.conn.execute(
                    """UPDATE sources SET status='frozen'
                       WHERE kind='restored_backup' AND status='active'
                       AND segment_id IN (
                         SELECT segment_id FROM segments WHERE print_frozen=1
                       )"""
                )
            self._correction(
                "independent_source_installed",
                "source",
                created["source_id"],
                f"{machine_id} 装入全新独立随机源",
                after={
                    "segment_id": created["segment_id"],
                    "frozen_old_segments": frozen_segments,
                },
            )
            self._audit(
                "independent_source_installed",
                "source",
                created["source_id"],
                {"machine_id": machine_id, "frozen": frozen_segments},
            )
        return self.get_source(created["source_id"])

    def visit_new_seed_station(
        self, machine_id: str = "printer-b", *, seed: str | None = None
    ) -> dict[str, Any]:
        """The custodian witnesses installation of one independent source."""
        source = self.install_independent_source(machine_id, seed=seed)
        decision = self.decide_recovery_priority()
        return {"installed_source": source, "decision": decision}

    def reseed_source(self, source_id: str, new_seed: str | None = None) -> dict[str, Any]:
        source = self._get_row("sources", "source_id", source_id)
        result = self.adapter.reseed(
            source_id=source_id,
            machine_id=source["machine_id"],
            segment_id=source["segment_id"],
            old_epoch=source["epoch"],
            old_state_secret=source["state_secret"],
            new_seed=new_seed,
        )
        with self.lock, self.conn:
            self.conn.execute(
                """UPDATE sources SET state_secret=?, epoch=?, next_seq=0
                   WHERE source_id=?""",
                (result["state_secret"], result["epoch"], source_id),
            )
            self._correction(
                "source_reseeded",
                "source",
                source_id,
                f"密封源补种至 epoch {result['epoch']}",
                after={"epoch": result["epoch"], "envelope": result["envelope"]},
            )
        return self.get_source(source_id)

    def get_source(self, source_id: str) -> dict[str, Any]:
        row = self._get_row("sources", "source_id", source_id)
        return self._source_dict(row)

    def _source_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "source_id": row["source_id"],
            "machine_id": row["machine_id"],
            "segment_id": row["segment_id"],
            "backup_id": row["backup_id"],
            "kind": row["kind"],
            "epoch": row["epoch"],
            "next_seq": row["next_seq"],
            "status": row["status"],
            "checkout": _loads(row["checkout_json"], {}),
            "created_at": row["created_at"],
        }

    # ---------- packet printing ----------

    def print_packet(
        self,
        stack_id: str | None = None,
        *,
        source_id: str,
        material_lot_id: str,
        count: int = 3,
        ticket_prefix: str = "PUB",
        ticket_nos: list[str] | None = None,
        title: str = "刮刮券封包",
        purpose: str = "ordinary",
        planned_count: int | None = None,
        holder_names: list[str] | None = None,
        real_name: bool = False,
        seal_id: str | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        if count <= 0:
            raise ValidationError("count must be positive")
        stack_id = stack_id or new_id("STACK")
        seal_id = seal_id or "SEAL-" + uuid.uuid4().hex[:8].upper()
        planned_count = planned_count if planned_count is not None else count

        with self.lock, self.conn:
            source = self._get_row("sources", "source_id", source_id)
            segment = self._get_row("segments", "segment_id", source["segment_id"])
            material = self._get_row("materials", "lot_id", material_lot_id)

            if source["status"] != "active":
                raise ProvenanceError(
                    f"source {source_id} is {source['status']}; no recognized printing",
                    details={"source_status": source["status"]},
                )
            if segment["print_frozen"]:
                raise ProvenanceError(
                    "old state segment is frozen after independent source installation",
                    details={"segment_id": segment["segment_id"]},
                )
            if material["quantity"] - material["consumed"] < count:
                raise ValidationError("material lot does not have enough unused stock")
            if self.conn.execute(
                "SELECT 1 FROM stacks WHERE stack_id=?", (stack_id,)
            ).fetchone():
                raise ConflictError(f"stack already exists: {stack_id}")

            if ticket_nos is None:
                ticket_nos = [
                    f"{ticket_prefix}-{i:03d}" for i in range(1, count + 1)
                ]
            if len(ticket_nos) != count:
                raise ValidationError("ticket_nos length must equal count")
            if len(set(ticket_nos)) != len(ticket_nos):
                raise ValidationError("public ticket numbers must be unique in packet")
            for ticket_no in ticket_nos:
                if self.conn.execute(
                    "SELECT 1 FROM coupons WHERE ticket_no=?", (ticket_no,)
                ).fetchone():
                    raise ConflictError(f"public ticket number already used: {ticket_no}")

            names = list(holder_names or [])
            if names and len(names) != count:
                raise ValidationError("holder_names length must equal count")

            drawn = self.adapter.draw(
                state_secret=source["state_secret"],
                epoch=source["epoch"],
                start_seq=source["next_seq"],
                ticket_nos=ticket_nos,
            )
            witness_id = new_id("WIT")
            previous = self.conn.execute(
                """SELECT chain_hash FROM witnesses
                   WHERE machine_id=? AND segment_id=?
                   ORDER BY rowid DESC LIMIT 1""",
                (source["machine_id"], source["segment_id"]),
            ).fetchone()
            envelope = self.adapter.usage_witness(
                witness_id=witness_id,
                source_id=source_id,
                machine_id=source["machine_id"],
                segment_id=source["segment_id"],
                epoch=source["epoch"],
                drawn=drawn,
                previous_chain_hash=previous["chain_hash"] if previous else None,
            )
            if not self.adapter.verify(envelope):
                raise ProvenanceError("adapter usage witness failed signature verification")

            first = drawn[0]
            last = drawn[-1]
            self.conn.execute(
                """INSERT INTO witnesses
                   (witness_id, segment_id, source_id, machine_id, from_seq, to_seq,
                    chain_hash, envelope_json, valid_signature, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
                (
                    witness_id,
                    source["segment_id"],
                    source_id,
                    source["machine_id"],
                    first.seq,
                    last.seq,
                    envelope["payload"]["chain_hash"],
                    json.dumps(envelope, ensure_ascii=False),
                    now_iso(),
                ),
            )
            self.conn.execute(
                "UPDATE sources SET next_seq=? WHERE source_id=?",
                (last.seq + 1, source_id),
            )
            self.conn.execute(
                "UPDATE materials SET consumed=consumed+? WHERE lot_id=?",
                (count, material_lot_id),
            )

            machines = set(_loads(segment["observed_machines_json"], []))
            machines.add(source["machine_id"])
            scope = segment["witness_scope"]
            if scope == "unresolved":
                scope = "single_machine" if len(machines) == 1 else "two_machines_shared"
            elif len(machines) >= 2:
                scope = "two_machines_shared"
            self.conn.execute(
                """UPDATE segments SET observed_machines_json=?, witness_scope=?,
                   updated_at=? WHERE segment_id=?""",
                (
                    json.dumps(sorted(machines)),
                    scope,
                    now_iso(),
                    segment["segment_id"],
                ),
            )

            interrupted = planned_count > count
            provenance_status = (
                "independent_verified"
                if source["kind"] == "independent_seed"
                else "old_state_unverified"
            )
            custody_status = "printing_interrupted" if interrupted else "warehoused"
            details = {
                "note": note,
                "witness_ids": [witness_id],
                "last_witness_id": witness_id,
                "adapter": self.adapter.name,
            }
            self.conn.execute(
                """INSERT INTO stacks
                   (stack_id, title, purpose, machine_id, source_id, segment_id,
                    material_lot_id, planned_count, print_count, seal_id, seal_status,
                    provenance_status, custody_status, details_json,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'applied_intact', ?, ?, ?, ?, ?)""",
                (
                    stack_id,
                    title,
                    purpose,
                    source["machine_id"],
                    source_id,
                    source["segment_id"],
                    material_lot_id,
                    planned_count,
                    count,
                    seal_id,
                    provenance_status,
                    custody_status,
                    json.dumps(details, ensure_ascii=False),
                    now_iso(),
                    now_iso(),
                ),
            )

            demo_secrets: list[dict[str, str]] = []
            for i, item in enumerate(drawn):
                holder = names[i] if names else ""
                status = "reserved" if holder else "printed"
                self.conn.execute(
                    """INSERT INTO coupons
                       (ticket_no, stack_id, seq, secret_hash, holder_name, real_name,
                        status, correction_state, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 'none', ?)""",
                    (
                        item.ticket_no,
                        stack_id,
                        item.seq,
                        item.secret_hash,
                        holder,
                        1 if real_name or holder else 0,
                        status,
                        now_iso(),
                    ),
                )
                # The plaintext is returned only in this print-job result, like
                # a sealed print manifest. It is never stored or returned by
                # generic state/redemption APIs.
                demo_secrets.append({"ticket_no": item.ticket_no, "secret": item.secret})

            self._audit(
                "packet_printed",
                "stack",
                stack_id,
                {"count": count, "seal_id": seal_id, "witness_id": witness_id},
            )

        result = self.get_stack(stack_id)
        result["print_manifest_once"] = demo_secrets
        return result

    # ---------- old cabinet / physical evidence ----------

    def visit_old_cabinet(
        self,
        stack_id: str,
        *,
        inspector: str = "保管人",
        seal_intact: bool = True,
        witness_id: str | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        stack = self._get_row("stacks", "stack_id", stack_id)
        details = _loads(stack["details_json"], {})
        witness_id = witness_id or details.get("last_witness_id")
        if not witness_id:
            raise ValidationError("stack has no continuous usage witness")
        witness = self._get_row("witnesses", "witness_id", witness_id)
        envelope = _loads(witness["envelope_json"], {})
        if not self.adapter.verify(envelope) or not witness["valid_signature"]:
            raise ProvenanceError("usage witness signature is invalid")
        if not envelope.get("payload", {}).get("continuous"):
            raise ProvenanceError("usage witness is not continuous")

        other_machine = self.conn.execute(
            """SELECT COUNT(DISTINCT machine_id) AS n FROM witnesses
               WHERE segment_id=? AND machine_id <> ?""",
            (stack["segment_id"], stack["machine_id"]),
        ).fetchone()["n"]
        if other_machine:
            raise ConflictError(
                "another machine already has a witness for this segment; "
                "use reprint confirmation/correction instead of exclusive certification",
                details={"segment_id": stack["segment_id"]},
            )

        with self.lock, self.conn:
            if seal_intact:
                seal_status = "verified_intact"
                provenance_status = "original_batch_secured"
                summary = "旧柜复核：封条完好，旧机专属连续记录可证明原批次"
            else:
                seal_status = "broken"
                provenance_status = "unverifiable"
                summary = "旧柜复核：封条破损，原批次不可作为可兑奖来历"
            details["old_cabinet_inspection"] = {
                "inspector": inspector,
                "seal_intact": seal_intact,
                "witness_id": witness_id,
                "note": note,
                "at": now_iso(),
            }
            self.conn.execute(
                """UPDATE stacks SET seal_status=?, provenance_status=?,
                   details_json=?, updated_at=? WHERE stack_id=?""",
                (
                    seal_status,
                    provenance_status,
                    json.dumps(details, ensure_ascii=False),
                    now_iso(),
                    stack_id,
                ),
            )
            if not seal_intact:
                self.conn.execute(
                    """UPDATE coupons SET status='quarantined',
                       correction_state='broken_seal_isolate'
                       WHERE stack_id=? AND status NOT IN ('redeemed')""",
                    (stack_id,),
                )
            self._correction(
                "old_cabinet_verification",
                "stack",
                stack_id,
                summary,
                after={
                    "seal_status": seal_status,
                    "provenance_status": provenance_status,
                    "witness_id": witness_id,
                },
            )
            self._audit("old_cabinet_visit", "stack", stack_id, {"seal_intact": seal_intact})
        return self.get_stack(stack_id)

    # ---------- handoff and counter receipt ----------

    def create_handoff(
        self,
        stack_id: str,
        *,
        handoff_id: str | None = None,
        from_party: str = "打印房",
        to_party: str = "旧柜/库房",
        complete: bool = False,
        documents: list[str] | None = None,
        missing: list[str] | None = None,
        signed_by: str = "",
        note: str = "",
    ) -> dict[str, Any]:
        handoff_id = handoff_id or new_id("HANDOFF")
        self._get_row("stacks", "stack_id", stack_id)
        docs = documents or []
        miss = [] if complete else (missing or ["交接回执未齐全"])
        if complete and miss:
            raise ValidationError("cannot complete handoff with missing documents")
        with self.lock, self.conn:
            self.conn.execute(
                """INSERT INTO handoffs
                   (handoff_id, stack_id, from_party, to_party, complete,
                    documents_json, missing_json, signed_by, note,
                    created_at, completed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    handoff_id,
                    stack_id,
                    from_party,
                    to_party,
                    1 if complete else 0,
                    json.dumps(docs, ensure_ascii=False),
                    json.dumps(miss, ensure_ascii=False),
                    signed_by,
                    note,
                    now_iso(),
                    now_iso() if complete else None,
                ),
            )
            if complete:
                self._apply_handoff_completion(handoff_id)
            else:
                self.conn.execute(
                    "UPDATE stacks SET custody_status='handoff_incomplete', updated_at=? WHERE stack_id=?",
                    (now_iso(), stack_id),
                )
            self._audit("handoff_created", "handoff", handoff_id, {"complete": complete})
        return self.get_handoff(handoff_id)

    def complete_handoff(
        self,
        handoff_id: str,
        *,
        documents: list[str] | None = None,
        signed_by: str = "库房值班员",
        counter_id: str | None = None,
        create_counter_receipt: bool = True,
    ) -> dict[str, Any]:
        handoff = self._get_row("handoffs", "handoff_id", handoff_id)
        if handoff["complete"]:
            raise ConflictError("handoff is already complete")
        docs = documents if documents is not None else _loads(handoff["documents_json"], [])
        if not docs:
            docs = ["旧交接单", "连续使用记录", "封条核验单", "库房台账"]

        with self.lock, self.conn:
            self.conn.execute(
                """UPDATE handoffs SET complete=1, documents_json=?, missing_json='[]',
                   signed_by=?, completed_at=? WHERE handoff_id=?""",
                (json.dumps(docs, ensure_ascii=False), signed_by, now_iso(), handoff_id),
            )
            result = self._apply_handoff_completion(
                handoff_id,
                counter_id=counter_id,
                create_counter_receipt=create_counter_receipt,
                signed_by=signed_by,
            )
        return result

    def _apply_handoff_completion(
        self,
        handoff_id: str,
        *,
        counter_id: str | None = None,
        create_counter_receipt: bool = False,
        signed_by: str = "库房值班员",
    ) -> dict[str, Any]:
        handoff = self._get_row("handoffs", "handoff_id", handoff_id)
        stack_id = handoff["stack_id"]
        stack = self._get_row("stacks", "stack_id", stack_id)
        blocked = stack["provenance_status"] == "reprint_confirmed" or stack["custody_status"] == "quarantined"

        receipt = None
        if create_counter_receipt and counter_id and not stack["active_receipt_id"]:
            receipt_id = new_id("RCPT")
            received_at = now_iso()
            self.conn.execute(
                """INSERT INTO counter_receipts
                   (receipt_id, counter_id, stack_id, signed_by, received_at,
                    status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'active', ?, ?)""",
                (receipt_id, counter_id, stack_id, signed_by, received_at, received_at, received_at),
            )
            self.conn.execute(
                "UPDATE stacks SET active_receipt_id=? WHERE stack_id=?",
                (receipt_id, stack_id),
            )
            receipt = receipt_id

        if not blocked:
            if receipt:
                custody = "counter_received"
                self.conn.execute(
                    """UPDATE coupons SET status='at_counter'
                       WHERE stack_id=? AND status IN ('printed','reserved','in_inventory')""",
                    (stack_id,),
                )
            else:
                custody = "handoff_complete"
            self.conn.execute(
                "UPDATE stacks SET custody_status=?, updated_at=? WHERE stack_id=?",
                (custody, now_iso(), stack_id),
            )

        coupons = self.conn.execute(
            "SELECT ticket_no, status FROM coupons WHERE stack_id=?", (stack_id,)
        ).fetchall()
        claimed = [r["ticket_no"] for r in coupons if r["status"] == "claimed"]
        unclaimed = [
            r["ticket_no"]
            for r in coupons
            if r["status"] in ("printed", "reserved", "in_inventory", "at_counter")
        ]
        for ticket_no in claimed:
            self._correction(
                "late_handoff_claimed_regularized",
                "coupon",
                ticket_no,
                "旧交接单后补齐全：已领取券的持有事实予以补正，但不改变其核查/冻结状态",
                after={"handoff_id": handoff_id, "holder_state": "claimed"},
            )
            self.conn.execute(
                """UPDATE coupons SET correction_state=CASE
                     WHEN correction_state LIKE 'reprint%' THEN correction_state
                     ELSE 'late_handoff_regularized_claimed' END
                   WHERE ticket_no=?""",
                (ticket_no,),
            )
        for ticket_no in unclaimed:
            self._correction(
                "late_handoff_unclaimed_cleared",
                "coupon",
                ticket_no,
                "旧交接单后补齐全：未领取券补齐交割链；若已被重印核查命中仍须隔离",
                after={"handoff_id": handoff_id, "holder_state": "unclaimed"},
            )
            self.conn.execute(
                """UPDATE coupons SET correction_state=CASE
                     WHEN correction_state LIKE 'reprint%' THEN correction_state
                     ELSE 'late_handoff_cleared_unclaimed' END
                   WHERE ticket_no=?""",
                (ticket_no,),
            )
        self._correction(
            "handoff_completed",
            "handoff",
            handoff_id,
            "旧交接单后来齐全；手续补全不能治愈已确认的暗码重印",
            after={
                "stack_id": stack_id,
                "receipt_id": receipt,
                "claimed": claimed,
                "unclaimed": unclaimed,
                "blocked_by_reprint_or_quarantine": blocked,
            },
        )
        return self.get_handoff(handoff_id)

    def deliver_to_counter(
        self,
        stack_id: str,
        *,
        counter_id: str = "COUNTER-1",
        signed_by: str = "柜台值班员",
        receipt_id: str | None = None,
        documents: list[str] | None = None,
    ) -> dict[str, Any]:
        stack = self._get_row("stacks", "stack_id", stack_id)
        if stack["seal_status"] == "broken":
            raise EligibilityError("cannot deliver stack with broken seal")
        if stack["provenance_status"] not in (
            "independent_verified",
            "original_batch_secured",
        ):
            raise ProvenanceError(
                "stack lacks accepted independent/original-batch provenance",
                details={"provenance_status": stack["provenance_status"]},
            )
        segment = self._get_row("segments", "segment_id", stack["segment_id"])
        if segment["reprint_confirmed"]:
            raise ProvenanceError("confirmed reprint segment cannot be delivered for sale")
        duplicate = self.conn.execute(
            """SELECT COUNT(*) AS n FROM coupons c
               WHERE c.stack_id=? AND EXISTS (
                 SELECT 1 FROM coupons c2
                 WHERE c2.secret_hash=c.secret_hash
                   AND c2.ticket_no <> c.ticket_no
               )""",
            (stack_id,),
        ).fetchone()
        if duplicate["n"]:
            raise ProvenanceError("duplicate secret codes must be investigated first")
        if stack["active_receipt_id"]:
            raise ConflictError("stack already has an active counter receipt")

        receipt_id = receipt_id or new_id("RCPT")
        docs = documents or ["材料登记", "印制见证", "封条完好", "库房交接", "柜台清点"]
        with self.lock, self.conn:
            handoff_id = new_id("HANDOFF")
            ts = now_iso()
            self.conn.execute(
                """INSERT INTO handoffs
                   (handoff_id, stack_id, from_party, to_party, complete,
                    documents_json, missing_json, signed_by, note,
                    created_at, completed_at)
                   VALUES (?, ?, '库房', ?, 1, ?, '[]', ?, '正常库存交割', ?, ?)""",
                (
                    handoff_id,
                    stack_id,
                    counter_id,
                    json.dumps(docs, ensure_ascii=False),
                    signed_by,
                    ts,
                    ts,
                ),
            )
            self.conn.execute(
                """INSERT INTO counter_receipts
                   (receipt_id, counter_id, stack_id, signed_by, received_at,
                    status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'active', ?, ?)""",
                (receipt_id, counter_id, stack_id, signed_by, ts, ts, ts),
            )
            self.conn.execute(
                """UPDATE stacks SET active_receipt_id=?, custody_status='counter_received',
                   updated_at=? WHERE stack_id=?""",
                (receipt_id, ts, stack_id),
            )
            self.conn.execute(
                """UPDATE coupons SET status='at_counter'
                   WHERE stack_id=? AND status IN ('printed','reserved','in_inventory')""",
                (stack_id,),
            )
            self._audit(
                "counter_delivery",
                "receipt",
                receipt_id,
                {"stack_id": stack_id, "counter_id": counter_id},
            )
        return self.get_receipt(receipt_id)

    def retarget_counter_receipt(
        self,
        receipt_id: str,
        new_stack_id: str,
        *,
        reason: str = "更正柜台收货回执：实际收到另一叠",
        signed_by: str | None = None,
    ) -> dict[str, Any]:
        receipt = self._get_row("counter_receipts", "receipt_id", receipt_id)
        new_stack = self._get_row("stacks", "stack_id", new_stack_id)
        old_stack_id = receipt["stack_id"]
        if old_stack_id == new_stack_id:
            raise ValidationError("receipt already points to that stack")

        with self.lock, self.conn:
            cid = self._correction(
                "counter_receipt_retargeted",
                "receipt",
                receipt_id,
                reason,
                before={"stack_id": old_stack_id, "counter_id": receipt["counter_id"]},
                after={"stack_id": new_stack_id, "counter_id": receipt["counter_id"]},
            )
            ts = now_iso()
            self.conn.execute(
                """UPDATE counter_receipts
                   SET stack_id=?, status='active', original_stack_id=?,
                       retarget_reason=?, correction_id=?, signed_by=COALESCE(?, signed_by),
                       updated_at=?
                   WHERE receipt_id=?""",
                (
                    new_stack_id,
                    receipt["original_stack_id"] or old_stack_id,
                    reason,
                    cid,
                    signed_by,
                    ts,
                    receipt_id,
                ),
            )
            old_stack = self._get_row("stacks", "stack_id", old_stack_id)
            self.conn.execute(
                "UPDATE stacks SET active_receipt_id=NULL, updated_at=? WHERE stack_id=?",
                (ts, old_stack_id),
            )
            if old_stack["custody_status"] not in ("quarantined",):
                self.conn.execute(
                    """UPDATE stacks SET custody_status='receipt_retargeted_under_verification'
                       WHERE stack_id=?""",
                    (old_stack_id,),
                )
                self.conn.execute(
                    """UPDATE coupons SET status='in_inventory'
                       WHERE stack_id=? AND status='at_counter'""",
                    (old_stack_id,),
                )

            self.conn.execute(
                "UPDATE stacks SET active_receipt_id=?, updated_at=? WHERE stack_id=?",
                (receipt_id, ts, new_stack_id),
            )
            if new_stack["custody_status"] != "quarantined":
                self.conn.execute(
                    "UPDATE stacks SET custody_status='counter_received' WHERE stack_id=?",
                    (new_stack_id,),
                )
                self.conn.execute(
                    """UPDATE coupons SET status='at_counter'
                       WHERE stack_id=? AND status IN ('printed','reserved','in_inventory')""",
                    (new_stack_id,),
                )
            self._audit(
                "receipt_retargeted",
                "receipt",
                receipt_id,
                {"old_stack_id": old_stack_id, "new_stack_id": new_stack_id},
            )
        return self.get_receipt(receipt_id)

    def get_handoff(self, handoff_id: str) -> dict[str, Any]:
        row = self._get_row("handoffs", "handoff_id", handoff_id)
        return {
            **dict(row),
            "complete": bool(row["complete"]),
            "documents": _loads(row["documents_json"], []),
            "missing": _loads(row["missing_json"], []),
        }

    def get_receipt(self, receipt_id: str) -> dict[str, Any]:
        row = self._get_row("counter_receipts", "receipt_id", receipt_id)
        return dict(row)

    # ---------- claims and redemption ----------

    def _active_receipt(
        self, stack_id: str, counter_id: str | None = None
    ) -> sqlite3.Row | None:
        if counter_id:
            return self.conn.execute(
                """SELECT * FROM counter_receipts
                   WHERE stack_id=? AND counter_id=? AND status='active'
                   ORDER BY rowid DESC LIMIT 1""",
                (stack_id, counter_id),
            ).fetchone()
        return self.conn.execute(
            """SELECT * FROM counter_receipts
               WHERE stack_id=? AND status='active'
               ORDER BY rowid DESC LIMIT 1""",
            (stack_id,),
        ).fetchone()

    def evaluate_coupon(
        self, ticket_no: str, counter_id: str | None = None
    ) -> dict[str, Any]:
        self.reconcile_confirmed_reprints()
        row = self.conn.execute(
            "SELECT * FROM coupons WHERE ticket_no=?", (ticket_no,)
        ).fetchone()
        if row is None:
            raise NotFoundError(f"ticket not found: {ticket_no}")
        stack = self._get_row("stacks", "stack_id", row["stack_id"])
        segment = self._get_row("segments", "segment_id", stack["segment_id"])

        blockers: list[dict[str, str]] = []
        if row["status"] == "redeemed":
            blockers.append({"code": "already_redeemed", "message": "该券已经兑现，不能重复发福利"})
        if row["status"] == "quarantined":
            blockers.append({"code": "quarantined", "message": "未领取券已因暗码重印隔离"})
        if row["status"] == "review_hold":
            blockers.append({"code": "review_hold", "message": "已领取券正在重印核查，暂停兑现"})
        if row["status"] == "void":
            blockers.append({"code": "void", "message": "该券已作废"})

        if stack["provenance_status"] == "old_state_unverified":
            blockers.append(
                {"code": "provenance_not_secured", "message": "旧备份状态印件尚未完成来历/封条核验"}
            )
        if stack["provenance_status"] == "unverifiable" or stack["seal_status"] == "broken":
            blockers.append({"code": "seal_unverifiable", "message": "封条或原批次来历不可验证"})
        if stack["provenance_status"] == "reprint_confirmed" or segment["reprint_confirmed"]:
            blockers.append(
                {"code": "reprint_under_investigation", "message": "该段已确认两机重印，须先核查/更正"}
            )

        duplicate = self.conn.execute(
            """SELECT ticket_no FROM coupons
               WHERE secret_hash=? AND ticket_no <> ?""",
            (row["secret_hash"], ticket_no),
        ).fetchall()
        if duplicate:
            blockers.append(
                {
                    "code": "duplicate_secret",
                    "message": "存在共用同一暗码的另一公开票号；票号不同也不构成独立兑奖券",
                    "other_tickets": [r["ticket_no"] for r in duplicate],
                }
            )

        receipt = self._active_receipt(row["stack_id"], counter_id)
        if receipt is None:
            blockers.append(
                {"code": "no_active_counter_receipt", "message": "该叠没有当前有效的柜台收货回执"})

        claim_blockers = list(blockers)
        redeem_blockers = list(blockers)
        if row["status"] != "at_counter":
            claim_blockers.append(
                {"code": "not_at_counter", "message": f"券当前状态为 {row['status']}，不能在柜台领取"}
            )
        if row["status"] not in ("claimed", "at_counter"):
            redeem_blockers.append(
                {"code": "not_customer_held", "message": "券尚未由顾客领取/持有"}
            )

        claim_codes = {b["code"] for b in claim_blockers}
        redeem_codes = {b["code"] for b in redeem_blockers}
        return {
            "ticket_no": ticket_no,
            "stack_id": row["stack_id"],
            "status": row["status"],
            "correction_state": row["correction_state"],
            "active_receipt_id": receipt["receipt_id"] if receipt else None,
            "blockers": blockers,
            "claimable": not claim_blockers,
            "redeemable": not redeem_blockers,
            "claim_blocker_codes": sorted(claim_codes),
            "redeem_blocker_codes": sorted(redeem_codes),
        }

    def record_legacy_claim(
        self,
        ticket_no: str,
        *,
        holder_name: str = "",
        id_reference: str = "",
        note: str = "回执未全时已发生的实名领取，事后补录事实",
    ) -> dict[str, Any]:
        """Record a pickup that factually happened before complete paperwork."""
        row = self._get_row("coupons", "ticket_no", ticket_no)
        if row["status"] == "redeemed":
            raise ConflictError("redeemed coupon cannot be re-claimed")
        if row["status"] == "claimed":
            raise ConflictError("coupon is already claimed")
        claim_id = new_id("CLAIM")
        ts = now_iso()
        with self.lock, self.conn:
            self.conn.execute(
                """INSERT INTO claims (claim_id, ticket_no, holder_name, id_reference, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (claim_id, ticket_no, holder_name or row["holder_name"], id_reference, ts),
            )
            self.conn.execute(
                """UPDATE coupons SET status='claimed', claim_id=?, claimed_at=?,
                   holder_name=CASE WHEN ?='' THEN holder_name ELSE ? END,
                   correction_state='legacy_claim_pending_handoff',
                   correction_note=?
                   WHERE ticket_no=?""",
                (
                    claim_id,
                    ts,
                    holder_name,
                    holder_name,
                    note,
                    ticket_no,
                ),
            )
            self._correction(
                "legacy_claim_recorded",
                "coupon",
                ticket_no,
                note,
                after={"claim_id": claim_id, "holder_state": "claimed_before_complete_handoff"},
            )
        return {"claim_id": claim_id, "ticket_no": ticket_no, "status": "claimed"}

    def claim_coupon(
        self,
        ticket_no: str,
        *,
        holder_name: str = "",
        id_reference: str = "",
        counter_id: str | None = None,
    ) -> dict[str, Any]:
        evaluation = self.evaluate_coupon(ticket_no, counter_id)
        if not evaluation["claimable"]:
            raise EligibilityError(
                "coupon cannot be claimed", details={"evaluation": evaluation}
            )
        claim_id = new_id("CLAIM")
        ts = now_iso()
        with self.lock, self.conn:
            self.conn.execute(
                """INSERT INTO claims (claim_id, ticket_no, holder_name, id_reference, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (claim_id, ticket_no, holder_name, id_reference, ts),
            )
            self.conn.execute(
                """UPDATE coupons SET status='claimed', claim_id=?, claimed_at=?,
                   holder_name=CASE WHEN ?='' THEN holder_name ELSE ? END
                   WHERE ticket_no=?""",
                (claim_id, ts, holder_name, holder_name, ticket_no),
            )
            self._audit("coupon_claimed", "coupon", ticket_no, {"claim_id": claim_id})
        return {"claim_id": claim_id, "ticket_no": ticket_no, "status": "claimed"}

    def redeem_coupon(
        self,
        ticket_no: str,
        secret: str,
        *,
        counter_id: str | None = None,
        benefit: str = "虚构福利一份",
    ) -> dict[str, Any]:
        if not isinstance(secret, str) or not secret:
            raise ValidationError("secret is required")
        row = self.conn.execute(
            "SELECT * FROM coupons WHERE ticket_no=?", (ticket_no,)
        ).fetchone()
        if row is None:
            # Do not echo submitted secret in errors/logs.
            raise NotFoundError("ticket or secret mismatch")

        expected_hash = fingerprint_secret(secret)
        if not hmac.compare_digest(expected_hash, row["secret_hash"]):
            self._audit("redemption_denied_secret", "coupon", ticket_no, {})
            raise EligibilityError("票号或暗码不匹配")

        if row["status"] == "redeemed":
            prior = self.conn.execute(
                "SELECT redemption_id FROM redemptions WHERE ticket_no=?",
                (ticket_no,),
            ).fetchone()
            raise ConflictError(
                "该券已兑现；重开软件也不能再发一份",
                details={"redemption_id": prior["redemption_id"] if prior else None},
            )

        evaluation = self.evaluate_coupon(ticket_no, counter_id)
        if not evaluation["redeemable"]:
            raise EligibilityError(
                "coupon cannot be redeemed", details={"evaluation": evaluation}
            )

        prior_secret = self.conn.execute(
            "SELECT redemption_id, ticket_no FROM redemptions WHERE secret_hash=?",
            (expected_hash,),
        ).fetchone()
        if prior_secret:
            raise ConflictError(
                "this secret was already honored through another public ticket; "
                "duplicate ticket numbers are not independent benefits",
                details={
                    "redemption_id": prior_secret["redemption_id"],
                    "first_ticket_no": prior_secret["ticket_no"],
                },
            )

        redemption_id = new_id("RED")
        ts = now_iso()
        with self.lock, self.conn:
            try:
                self.conn.execute(
                    """INSERT INTO redemptions
                       (redemption_id, ticket_no, counter_id, secret_hash, benefit, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        redemption_id,
                        ticket_no,
                        counter_id or evaluation.get("active_receipt_id") or "UNKNOWN",
                        expected_hash,
                        benefit,
                        ts,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ConflictError("secret has already been honored once") from exc
            self.conn.execute(
                """UPDATE coupons SET status='redeemed', redeemed_at=?,
                   redemption_id=? WHERE ticket_no=?""",
                (ts, redemption_id, ticket_no),
            )
            self._audit(
                "redemption_accepted",
                "redemption",
                redemption_id,
                {"ticket_no": ticket_no, "counter_id": counter_id},
            )
        return {
            "redemption_id": redemption_id,
            "ticket_no": ticket_no,
            "benefit": benefit,
            "redeemed_at": ts,
            "message": "兑换成功；暗码不会被再次展示或写入审计明细",
        }

    # ---------- reprint witness correction ----------

    def update_segment_witness(
        self,
        segment_id: str,
        *,
        witness_scope: str | None = None,
        reprint_confirmed: bool | None = None,
        machine_ids: list[str] | None = None,
        note: str = "",
        confirmation_ref: str | None = None,
    ) -> dict[str, Any]:
        segment = self._get_row("segments", "segment_id", segment_id)
        before = {
            "witness_scope": segment["witness_scope"],
            "reprint_confirmed": bool(segment["reprint_confirmed"]),
            "observed_machines": _loads(segment["observed_machines_json"], []),
        }
        valid_scopes = {"unresolved", "single_machine", "two_machines_shared"}
        if witness_scope is not None and witness_scope not in valid_scopes:
            raise ValidationError(f"invalid witness_scope: {witness_scope}")

        with self.lock, self.conn:
            machines = machine_ids if machine_ids is not None else before["observed_machines"]
            if witness_scope == "two_machines_shared" and len(machines) < 2:
                # The scope can be corrected from external evidence even if the
                # local witness table has not yet imported both records.
                machines = sorted(set(machines) | {"printer-a", "printer-b"})
            new_reprint = (
                bool(reprint_confirmed)
                if reprint_confirmed is not None
                else bool(segment["reprint_confirmed"])
            )
            self.conn.execute(
                """UPDATE segments SET witness_scope=?, reprint_confirmed=?,
                   observed_machines_json=?, witness_note=?, updated_at=?
                   WHERE segment_id=?""",
                (
                    witness_scope or segment["witness_scope"],
                    1 if new_reprint else 0,
                    json.dumps(sorted(machines)),
                    (segment["witness_note"] + "\n" + note).strip(),
                    now_iso(),
                    segment_id,
                ),
            )
            cid = self._correction(
                "segment_witness_updated",
                "segment",
                segment_id,
                note or "段见证被更正",
                before=before,
                after={
                    "witness_scope": witness_scope or segment["witness_scope"],
                    "reprint_confirmed": new_reprint,
                    "observed_machines": sorted(machines),
                    "confirmation_ref": confirmation_ref,
                },
            )
            result: dict[str, Any] = {"segment_id": segment_id, "correction_id": cid}
            if new_reprint and not segment["reprint_confirmed"]:
                holds = self._apply_reprint_holds(
                    segment_id, cid, confirmation_ref or note or "confirmed reprint"
                )
                result["corrections"] = holds
            self._audit("segment_witness_updated", "segment", segment_id, result)
        result["segment"] = self.get_segment(segment_id)
        return result

    def confirm_reprint_from_witnesses(self, segment_id: str) -> dict[str, Any]:
        segment = self._get_row("segments", "segment_id", segment_id)
        machines = [
            r["machine_id"]
            for r in self.conn.execute(
                "SELECT DISTINCT machine_id FROM witnesses WHERE segment_id=?",
                (segment_id,),
            ).fetchall()
        ]
        duplicates = self.conn.execute(
            """SELECT c.secret_hash, GROUP_CONCAT(c.ticket_no) AS tickets,
                      COUNT(*) AS n
               FROM coupons c JOIN stacks s ON s.stack_id=c.stack_id
               WHERE s.segment_id=?
               GROUP BY c.secret_hash HAVING n > 1""",
            (segment_id,),
        ).fetchall()
        if len(machines) < 2 or not duplicates:
            raise ConflictError(
                "need witnesses from two machines and at least one duplicate secret hash"
            )
        dup_groups = [
            {"tickets": r["tickets"].split(","), "count": r["n"]} for r in duplicates
        ]
        return self.update_segment_witness(
            segment_id,
            witness_scope="two_machines_shared",
            reprint_confirmed=True,
            machine_ids=machines,
            note="两份连续使用见证出现重叠暗码，确认两机共用旧段并重印同一段",
            confirmation_ref=json.dumps(dup_groups, ensure_ascii=False),
        )

    def reconcile_confirmed_reprints(self) -> dict[str, dict[str, list[str]]]:
        """Apply holds if a segment finding was corrected directly in storage.

        The normal API applies holds atomically with the witness correction.
        This sweep also makes a literal one-column/one-row witness mutation
        take effect when the service next evaluates or reads state, while
        remaining idempotent.
        """
        result: dict[str, dict[str, list[str]]] = {}
        with self.lock, self.conn:
            rows = self.conn.execute(
                "SELECT segment_id FROM segments WHERE reprint_confirmed=1"
            ).fetchall()
            for row in rows:
                result[row["segment_id"]] = self._apply_reprint_holds(
                    row["segment_id"],
                    "reconcile-confirmed-witness",
                    "segment witness is marked two-machine shared with confirmed reprint",
                )
        return result

    def _apply_reprint_holds(
        self, segment_id: str, master_correction_id: str, reason: str
    ) -> dict[str, list[str]]:
        coupons = self.conn.execute(
            """SELECT c.* FROM coupons c JOIN stacks s ON s.stack_id=c.stack_id
               WHERE s.segment_id=?""",
            (segment_id,),
        ).fetchall()
        out = {"redeemed_no_reissue": [], "claimed_frozen": [], "unclaimed_isolated": []}
        ts = now_iso()
        for coupon in coupons:
            ticket = coupon["ticket_no"]
            prior_state = coupon["correction_state"]
            if coupon["status"] == "redeemed" or prior_state == "honored_no_reissue":
                state = "honored_no_reissue"
                new_status = "redeemed"
                summary = "已兑现：福利已支付，保留原兑换记录；不能靠重开软件再发一份"
                bucket = "redeemed_no_reissue"
                order = False
            elif (
                coupon["status"] == "claimed"
                or prior_state == "reprint_claimed_freeze"
            ):
                state = "reprint_claimed_freeze"
                new_status = "review_hold"
                summary = "已领取未兑现：暗码重印核查，冻结兑换并建立更正/换发单"
                bucket = "claimed_frozen"
                order = True
            else:
                state = "reprint_unclaimed_isolate"
                new_status = "quarantined"
                summary = "未领取：立即隔离，不得发往柜台或顾客，等待独立来源重印"
                bucket = "unclaimed_isolated"
                order = True

            already_recorded = prior_state == state
            if not already_recorded:
                cid = self._correction(
                    "reprint_ticket_correction",
                    "coupon",
                    ticket,
                    summary,
                    before={
                        "status": coupon["status"],
                        "correction_state": coupon["correction_state"],
                    },
                    after={"status": state, "new_status": new_status},
                    correction_id=None,
                )
                if order:
                    rid = new_id("REPL")
                    self.conn.execute(
                        """INSERT INTO replacement_orders
                           (replacement_id, original_ticket_no, correction_id, reason,
                            state, created_at)
                           VALUES (?, ?, ?, ?, 'pending_independent_source', ?)""",
                        (rid, ticket, cid, state, ts),
                    )

            self.conn.execute(
                """UPDATE coupons SET status=?, correction_state=?,
                   correction_note=? WHERE ticket_no=?""",
                (new_status, state, reason, ticket),
            )
            out[bucket].append(ticket)

        self.conn.execute(
            """UPDATE stacks SET provenance_status='reprint_confirmed',
               custody_status='quarantined', updated_at=?
               WHERE segment_id=?""",
            (ts, segment_id),
        )
        self._audit(
            "reprint_holds_applied",
            "segment",
            segment_id,
            {"master_correction_id": master_correction_id, **out},
        )
        return out

    def complete_replacement_order(
        self,
        replacement_id: str,
        *,
        new_stack_id: str,
        new_ticket_no: str,
    ) -> dict[str, Any]:
        order = self._get_row("replacement_orders", "replacement_id", replacement_id)
        new_ticket = self._get_row("coupons", "ticket_no", new_ticket_no)
        if new_ticket["stack_id"] != new_stack_id:
            raise ValidationError("new ticket does not belong to new stack")
        with self.lock, self.conn:
            self.conn.execute(
                """UPDATE replacement_orders SET state='replaced',
                   new_stack_id=?, new_ticket_no=?, completed_at=?
                   WHERE replacement_id=?""",
                (new_stack_id, new_ticket_no, now_iso(), replacement_id),
            )
            self.conn.execute(
                "UPDATE coupons SET replacement_ticket_no=? WHERE ticket_no=?",
                (new_ticket_no, order["original_ticket_no"]),
            )
            self._correction(
                "replacement_order_completed",
                "replacement",
                replacement_id,
                "使用独立来源新券完成更正换发；已兑现券无换发单",
                after={"new_ticket_no": new_ticket_no},
            )
        return dict(self._get_row("replacement_orders", "replacement_id", replacement_id))

    # ---------- reads / decision ----------

    def get_segment(self, segment_id: str) -> dict[str, Any]:
        row = self._get_row("segments", "segment_id", segment_id)
        return {
            **dict(row),
            "reprint_confirmed": bool(row["reprint_confirmed"]),
            "print_frozen": bool(row["print_frozen"]),
            "observed_machines": _loads(row["observed_machines_json"], []),
        }

    def get_stack(self, stack_id: str) -> dict[str, Any]:
        row = self._get_row("stacks", "stack_id", stack_id)
        stack = dict(row)
        stack["details"] = _loads(row["details_json"], {})
        coupons = self.conn.execute(
            "SELECT * FROM coupons WHERE stack_id=? ORDER BY seq", (stack_id,)
        ).fetchall()
        stack["coupons"] = [self._coupon_public(r) for r in coupons]
        receipt = None
        if row["active_receipt_id"]:
            receipt_row = self.conn.execute(
                "SELECT * FROM counter_receipts WHERE receipt_id=?",
                (row["active_receipt_id"],),
            ).fetchone()
            # Validate the side of the relation: a receipt whose target was
            # corrected away must not remain evidence for the old stack even
            # if a test/operator changed only counter_receipts.stack_id.
            if (
                receipt_row is not None
                and receipt_row["stack_id"] == stack_id
                and receipt_row["status"] == "active"
            ):
                receipt = dict(receipt_row)
        if receipt is None:
            active = self._active_receipt(stack_id)
            receipt = dict(active) if active is not None else None
        stack["active_receipt"] = receipt
        return stack

    def _coupon_public(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "ticket_no": row["ticket_no"],
            "stack_id": row["stack_id"],
            "seq": row["seq"],
            "holder_name": row["holder_name"],
            "real_name": bool(row["real_name"]),
            "status": row["status"],
            "correction_state": row["correction_state"],
            "correction_note": row["correction_note"],
            "replacement_ticket_no": row["replacement_ticket_no"],
            "claimed_at": row["claimed_at"],
            "redeemed_at": row["redeemed_at"],
            "redemption_id": row["redemption_id"],
            "secret_on_file": bool(row["secret_hash"]),
            # Deliberately no secret or secret_hash in public state.
        }

    def can_continue_segment(
        self, segment_id: str, machine_id: str
    ) -> dict[str, Any]:
        """Decide whether this machine may continue the old code segment.

        A restored segment may be continued as the *original* chain only when
        the signed continuous witness history is exclusive to that machine and
        the segment has not been frozen/confirmed as shared-reprinted.
        """
        segment = self.get_segment(segment_id)
        machine_rows = self.conn.execute(
            "SELECT DISTINCT machine_id FROM witnesses WHERE segment_id=?",
            (segment_id,),
        ).fetchall()
        machines = [r["machine_id"] for r in machine_rows]
        reasons: list[str] = []
        exclusive_machine = machines[0] if len(machines) == 1 else None

        if segment["kind"] != "restored_backup":
            reasons.append("not_a_restored_backup_segment")
        if segment["print_frozen"]:
            reasons.append("old_segment_frozen_after_independent_source")
        if segment["reprint_confirmed"]:
            reasons.append("shared_reprint_confirmed")
        if len(machines) > 1:
            reasons.append("state_continued_on_another_machine")
        elif exclusive_machine and exclusive_machine != machine_id:
            reasons.append("witness_belongs_to_another_machine")

        return {
            "segment_id": segment_id,
            "machine_id": machine_id,
            "observed_machines": machines,
            "exclusive_machine": exclusive_machine,
            "can_continue_original_segment": not reasons,
            "reasons": reasons,
            "allowed_recovery": (
                "continue_original_batch_after_cabinet_and_handoff"
                if not reasons
                else "install_new_independent_source_and_reprint"
            ),
        }

    def decide_recovery_priority(self) -> dict[str, Any]:
        """Return the service's current operational decision."""
        old_segments = self.conn.execute(
            "SELECT * FROM segments WHERE kind='restored_backup'"
        ).fetchall()
        independent_sources = self.conn.execute(
            "SELECT COUNT(*) AS n FROM sources WHERE kind='independent_seed' AND status='active'"
        ).fetchone()["n"]
        open_reprints = self.conn.execute(
            "SELECT COUNT(*) AS n FROM segments WHERE reprint_confirmed=1"
        ).fetchone()["n"]
        incomplete_stacks = self.conn.execute(
            """SELECT COUNT(*) AS n FROM stacks
               WHERE custody_status IN ('handoff_incomplete','printing_interrupted')"""
        ).fetchone()["n"]
        active_restored_sources = self.conn.execute(
            "SELECT COUNT(*) AS n FROM sources WHERE kind='restored_backup' AND status='active'"
        ).fetchone()["n"]
        salvageable_reserved = self.conn.execute(
            """SELECT COUNT(*) AS n FROM stacks
               WHERE purpose='real_name_reservation'
                 AND provenance_status='old_state_unverified'
                 AND seal_status IN ('applied_intact','verified_intact')"""
        ).fetchone()["n"]

        if open_reprints:
            priority = "contain_reprint_and_recover_with_new_source"
            first_stop = "new_source_after_holds"
            recommendation = (
                "先执行重印更正：已领取券冻结核查，未领取券隔离；"
                "随后只能用新的独立来源重印/交割，并把柜台回执改到有效另一叠。"
            )
        elif independent_sources == 0 and incomplete_stacks:
            if active_restored_sources >= 2:
                first_stop = "new_seed"
                priority = "restore_new_printing_and_freeze_shared_state_first"
                recommendation = (
                    "两机都挂着同一备份段且仍可继续出码，优先去新种子站："
                    "装入独立源并冻结旧段，先阻止乙机把同一段印成第二叠，同时恢复临时活动用券。"
                    "甲机昨夜实名预订叠不会被新来源补证；其封条和旧机专属连续记录仍须另班去旧柜核验，"
                    "交接单齐全且无重印后才能交割。"
                )
            elif salvageable_reserved:
                first_stop = "old_cabinet"
                priority = "salvage_reserved_original_batch_first"
                recommendation = (
                    "当前只有甲机旧段痕迹、没有第二台机待继续出码的即时扩散面，"
                    "优先去旧柜复核末份旧机专属连续记录和实物封条，保住实名预订原批次。"
                    "新活动在装入独立来源前不得借用旧状态印制可收兑券。"
                )
            else:
                first_stop = "new_seed"
                priority = "establish_independent_provenance"
                recommendation = "先到新种子站建立独立来源并冻结旧状态，再恢复印制。"
        elif independent_sources > 0:
            first_stop = "use_independent_stack"
            priority = "new_print_capability_available"
            recommendation = (
                "独立来源已建立；只可印制/交割 independent_verified 且有封条、"
                "有效柜台回执的新叠。旧叠按其证据和更正状态处理。"
            )
        else:
            first_stop = "register_material"
            priority = "register_materials_and_begin"
            recommendation = "先登记来料，再建立密封来源和印制封包。"

        sendable = []
        for row in self.conn.execute("SELECT stack_id FROM stacks").fetchall():
            sid = row["stack_id"]
            stack = self.get_stack(sid)
            if (
                stack["provenance_status"] == "independent_verified"
                and stack["seal_status"] not in ("broken", "none")
                and stack["active_receipt"]
                and not self.conn.execute(
                    "SELECT reprint_confirmed FROM segments WHERE segment_id=?",
                    (stack["segment_id"],),
                ).fetchone()["reprint_confirmed"]
            ):
                sendable.append(sid)
            elif (
                stack["provenance_status"] == "original_batch_secured"
                and stack["seal_status"] == "verified_intact"
                and stack["active_receipt"]
            ):
                sendable.append(sid)

        return {
            "priority": priority,
            "first_stop": first_stop,
            "recommendation": recommendation,
            "sendable_stack_ids": sendable,
            "open_reprint_segments": open_reprints,
            "active_independent_sources": independent_sources,
            "active_restored_sources": active_restored_sources,
            "salvageable_reserved_stacks": salvageable_reserved,
            "incomplete_stack_count": incomplete_stacks,
            "station_options": [
                {
                    "station": "new_seed",
                    "effect": "一台机装入独立来源；两机旧状态今后印制被冻结；不补证旧印件",
                },
                {
                    "station": "old_cabinet",
                    "effect": "核验旧机专属连续见证和实物封条；可保住原批次，但不恢复新活动用券来历",
                },
            ],
        }

    def get_state(self) -> dict[str, Any]:
        self.reconcile_confirmed_reprints()

        def all_rows(sql: str) -> list[dict[str, Any]]:
            return [dict(r) for r in self.conn.execute(sql).fetchall()]

        materials = all_rows("SELECT * FROM materials ORDER BY created_at")
        sources = [self._source_dict(r) for r in self.conn.execute("SELECT * FROM sources ORDER BY created_at").fetchall()]
        segments = [self.get_segment(r["segment_id"]) for r in self.conn.execute("SELECT segment_id FROM segments ORDER BY created_at").fetchall()]
        stacks = [self.get_stack(r["stack_id"]) for r in self.conn.execute("SELECT stack_id FROM stacks ORDER BY created_at").fetchall()]
        handoffs = [self.get_handoff(r["handoff_id"]) for r in self.conn.execute("SELECT handoff_id FROM handoffs ORDER BY created_at").fetchall()]
        receipts = all_rows("SELECT * FROM counter_receipts ORDER BY created_at")
        claims = all_rows("SELECT * FROM claims ORDER BY created_at")
        redemptions = all_rows("SELECT redemption_id,ticket_no,counter_id,benefit,created_at FROM redemptions ORDER BY created_at")
        corrections = []
        for r in self.conn.execute("SELECT * FROM corrections ORDER BY rowid DESC").fetchall():
            corrections.append(
                {
                    **dict(r),
                    "before": _loads(r["before_json"], {}),
                    "after": _loads(r["after_json"], {}),
                }
            )
        replacement_orders = all_rows("SELECT * FROM replacement_orders ORDER BY created_at DESC")
        witnesses = []
        for r in self.conn.execute("SELECT * FROM witnesses ORDER BY created_at").fetchall():
            envelope = _loads(r["envelope_json"], {})
            # Redact verifier hashes from the broad read model. The signed
            # envelope remains intact in SQLite for signature verification.
            public_envelope = json.loads(json.dumps(envelope))
            for item in public_envelope.get("payload", {}).get("items", []):
                if "secret_hash" in item:
                    item["secret_recorded"] = True
                    del item["secret_hash"]
            witnesses.append(
                {
                    "witness_id": r["witness_id"],
                    "segment_id": r["segment_id"],
                    "source_id": r["source_id"],
                    "machine_id": r["machine_id"],
                    "from_seq": r["from_seq"],
                    "to_seq": r["to_seq"],
                    "chain_hash": r["chain_hash"],
                    "valid_signature": bool(r["valid_signature"]),
                    "envelope": public_envelope,
                    "created_at": r["created_at"],
                }
            )
        audit = all_rows("SELECT * FROM audit_events ORDER BY rowid DESC LIMIT 100")
        return {
            "decision": self.decide_recovery_priority(),
            "materials": materials,
            "segments": segments,
            "sources": sources,
            "witnesses": witnesses,
            "stacks": stacks,
            "handoffs": handoffs,
            "receipts": receipts,
            "claims": claims,
            "redemptions": redemptions,
            "corrections": corrections,
            "replacement_orders": replacement_orders,
            "audit_events": audit,
        }
