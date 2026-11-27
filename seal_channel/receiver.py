"""收端三号滑动直收窗口、账册、待对账区与重送幂等处理。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .errors import ReconciliationError
from .models import ReceiveResult
from .verifier import TrustedReceiptVerifier


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def initial_receiver_state(window_size: int = 3) -> Dict[str, Any]:
    return {
        "version": 1,
        "window_size": window_size,
        "high_water": None,
        "ledger_seq": 0,
        "receipt_seq": 0,
        "ledger": [],
        "pending": [],
        "events": [],
        "body_index": {},
        "stats": {
            "received": 0,
            "recorded": 0,
            "duplicate": 0,
            "duplicate_pending": 0,
            "pending_reconciliation": 0,
            "rejected": 0,
            "conflict": 0,
            "manual_reconciled": 0,
        },
    }


class Receiver:
    def __init__(
        self,
        store: Any,
        state_path: str = "receiver.json",
        verifier: Optional[TrustedReceiptVerifier] = None,
        window_size: Optional[int] = None,
    ) -> None:
        self.store = store
        self.state_path = state_path
        if verifier is None:
            trusted = store.read_json("trusted_receipts.json")
            registry = store.read_json("registry.json")
            verifier = TrustedReceiptVerifier(trusted, registry)
        self.verifier = verifier

        default = initial_receiver_state(
            window_size or store.read_json("case.json", default={}).get("window_size", 3)
        )
        self.data = store.read_json(state_path, default=default)

    def save(self) -> None:
        self.store.write_json(self.state_path, value=self.data)

    def _next_receipt(self) -> str:
        self.data["receipt_seq"] += 1
        return f"RCPT-{self.data['receipt_seq']:04d}"

    def _add_event(self, event_type: str, **fields: Any) -> None:
        event = {"at": utc_now(), "type": event_type, **fields}
        self.data.setdefault("events", []).append(event)

    def _window_for(self, high_water: Optional[int]) -> Optional[List[int]]:
        if high_water is None:
            return None
        size = int(self.data.get("window_size", 3))
        lower = high_water - size + 1
        return [n for n in range(max(1, lower), high_water + 1)]

    def _find_ledger_entry(self, seal: int) -> Optional[Dict[str, Any]]:
        for entry in self.data["ledger"]:
            if int(entry["seal"]) == int(seal):
                return entry
        return None

    def _find_active_pending(self, seal: int) -> Optional[Dict[str, Any]]:
        for item in self.data["pending"]:
            if int(item["seal"]) == int(seal) and item.get("state") == "active":
                return item
        return None

    def receive_envelope(self, envelope: Dict[str, Any]) -> ReceiveResult:
        self.data["stats"]["received"] += 1
        verification = self.verifier.verify(envelope)
        if not verification.ok:
            self.data["stats"]["rejected"] += 1
            self._add_event(
                "rejected",
                seal=verification.seal,
                code=verification.code,
                reason=verification.reason,
            )
            self.save()
            return ReceiveResult(
                status="rejected",
                ok=False,
                seal=verification.seal,
                owner=verification.owner,
                code=verification.code,
                message=verification.reason,
                high_water=self.data["high_water"],
                window=self._window_for(self.data["high_water"]),
                rejected=True,
            )

        seal = int(verification.seal)
        owner = verification.owner
        body = envelope["body"]
        body_hash = envelope["body_sha256"]
        key = str(seal)

        known = self.data["body_index"].get(key)
        if known and known.get("body_sha256") != body_hash:
            self.data["stats"]["conflict"] += 1
            self._add_event(
                "conflict",
                seal=seal,
                owner=owner,
                original_body_sha256=known.get("body_sha256"),
                competing_body_sha256=body_hash,
            )
            self.save()
            return ReceiveResult(
                status="conflict",
                ok=False,
                seal=seal,
                owner=owner,
                code="SEAL_BODY_CONFLICT",
                message=(
                    f"{seal} 号封条已认过另一封正文；原信保留，"
                    "不能让新正文占号"
                ),
                high_water=self.data["high_water"],
                window=self._window_for(self.data["high_water"]),
                rejected=True,
                details={
                    "original_body_sha256": known.get("body_sha256"),
                    "competing_body_sha256": body_hash,
                },
            )

        ledger_entry = self._find_ledger_entry(seal)
        if ledger_entry is not None:
            receipt_id = self._next_receipt()
            receipt = {
                "receipt_id": receipt_id,
                "at": utc_now(),
                "seal": seal,
                "status": "duplicate_ledger_no_change",
                "ledger_id": ledger_entry["ledger_id"],
            }
            ledger_entry.setdefault("receipts", []).append(receipt)
            ledger_entry["redelivery_count"] = (
                int(ledger_entry.get("redelivery_count", 0)) + 1
            )
            self.data["stats"]["duplicate"] += 1
            self._add_event("duplicate", seal=seal, receipt_id=receipt_id)
            self.save()
            return ReceiveResult(
                status="duplicate",
                ok=True,
                seal=seal,
                owner=owner,
                message="同一信重送：账册不新增行，只补回条",
                high_water=self.data["high_water"],
                window=self._window_for(self.data["high_water"]),
                ledger_id=ledger_entry["ledger_id"],
                receipt_id=receipt_id,
                duplicate=True,
            )

        pending_entry = self._find_active_pending(seal)
        if pending_entry is not None:
            receipt_id = self._next_receipt()
            pending_entry.setdefault("receipts", []).append(
                {
                    "receipt_id": receipt_id,
                    "at": utc_now(),
                    "status": "duplicate_pending_still_needs_reconciliation",
                }
            )
            pending_entry["copies"] = int(pending_entry.get("copies", 1)) + 1
            self.data["stats"]["duplicate_pending"] += 1
            self._add_event(
                "duplicate_pending", seal=seal, receipt_id=receipt_id
            )
            self.save()
            return ReceiveResult(
                status="duplicate_pending",
                ok=True,
                seal=seal,
                owner=owner,
                message="同一封过窗真信的重送只补回条，仍待人工对账",
                high_water=self.data["high_water"],
                window=self._window_for(self.data["high_water"]),
                receipt_id=receipt_id,
                duplicate=True,
                manual_required=True,
            )

        old_high_water = self.data["high_water"]
        candidate_high_water = seal if old_high_water is None else max(old_high_water, seal)
        size = int(self.data.get("window_size", 3))
        lower = candidate_high_water - size + 1
        current_window = self._window_for(candidate_high_water)

        if old_high_water is not None and seal < lower:
            receipt_id = self._next_receipt()
            item = {
                "seal": seal,
                "owner": owner,
                "body": body,
                "body_sha256": body_hash,
                "received_at": utc_now(),
                "state": "active",
                "reason": "BELOW_DIRECT_WINDOW",
                "high_water_at_arrival": old_high_water,
                "window_at_arrival": self._window_for(old_high_water),
                "copies": 1,
                "receipts": [
                    {
                        "receipt_id": receipt_id,
                        "at": utc_now(),
                        "status": "pending_manual_reconciliation",
                    }
                ],
            }
            self.data["pending"].append(item)
            self.data["body_index"][key] = {
                "body_sha256": body_hash,
                "first_status": "pending_reconciliation",
                "since": item["received_at"],
            }
            self.data["stats"]["pending_reconciliation"] += 1
            self._add_event(
                "pending_reconciliation",
                seal=seal,
                window=item["window_at_arrival"],
                receipt_id=receipt_id,
            )
            self.save()
            return ReceiveResult(
                status="pending_reconciliation",
                ok=True,
                seal=seal,
                owner=owner,
                message=(
                    f"{seal} 号是有效真信，但低于直收窗口 "
                    f"{item['window_at_arrival']}，保留待人工对账"
                ),
                high_water=old_high_water,
                window=item["window_at_arrival"],
                receipt_id=receipt_id,
                manual_required=True,
            )

        if old_high_water is None or seal > old_high_water:
            self.data["high_water"] = seal
            if seal > (old_high_water or 0):
                self._add_event("window_raised", from_high_water=old_high_water, to=seal)

        self.data["ledger_seq"] += 1
        ledger_id = f"LEDGER-{self.data['ledger_seq']:04d}"
        receipt_id = self._next_receipt()
        now = utc_now()
        entry = {
            "ledger_id": ledger_id,
            "seal": seal,
            "owner": owner,
            "body": body,
            "body_sha256": body_hash,
            "ledgered_at": now,
            "source": "direct_window",
            "high_water_after": self.data["high_water"],
            "window_after": self._window_for(self.data["high_water"]),
            "redelivery_count": 0,
            "receipts": [
                {
                    "receipt_id": receipt_id,
                    "at": now,
                    "status": "ledgered_direct",
                    "ledger_id": ledger_id,
                }
            ],
        }
        self.data["ledger"].append(entry)
        self.data["body_index"][key] = {
            "body_sha256": body_hash,
            "first_status": "recorded",
            "ledger_id": ledger_id,
            "since": now,
        }
        self.data["stats"]["recorded"] += 1
        self._add_event(
            "ledgered",
            seal=seal,
            ledger_id=ledger_id,
            receipt_id=receipt_id,
            high_water=self.data["high_water"],
        )
        self.save()
        return ReceiveResult(
            status="recorded",
            ok=True,
            seal=seal,
            owner=owner,
            message=f"{seal} 号在三号直收窗口内，合格首信已即时入册",
            high_water=self.data["high_water"],
            window=self._window_for(self.data["high_water"]),
            ledger_id=ledger_id,
            receipt_id=receipt_id,
        )

    def reconcile(self, seal: int, note: str = "") -> Dict[str, Any]:
        seal = int(seal)
        item = self._find_active_pending(seal)
        if item is None:
            raise ReconciliationError(
                f"没有找到 {seal} 号尚待对账的有效信",
                code="NO_ACTIVE_PENDING",
            )

        self.data["ledger_seq"] += 1
        ledger_id = f"LEDGER-{self.data['ledger_seq']:04d}"
        receipt_id = self._next_receipt()
        now = utc_now()
        entry = {
            "ledger_id": ledger_id,
            "seal": seal,
            "owner": item["owner"],
            "body": item["body"],
            "body_sha256": item["body_sha256"],
            "ledgered_at": now,
            "source": "manual_reconciliation",
            "note": note,
            "original_received_at": item["received_at"],
            "receipts": [
                {
                    "receipt_id": receipt_id,
                    "at": now,
                    "status": "manual_reconciliation_ledgered",
                    "ledger_id": ledger_id,
                }
            ],
        }
        self.data["ledger"].append(entry)
        item["state"] = "reconciled"
        item["reconciled_at"] = now
        item["ledger_id"] = ledger_id
        item["reconciliation_note"] = note
        self.data["body_index"][str(seal)] = {
            "body_sha256": item["body_sha256"],
            "first_status": "recorded_manual",
            "ledger_id": ledger_id,
            "since": now,
        }
        self.data["stats"]["manual_reconciled"] += 1
        self._add_event("manual_reconciled", seal=seal, ledger_id=ledger_id)
        self.save()
        return entry

    def dashboard(self) -> Dict[str, Any]:
        hwm = self.data["high_water"]
        active_pending = [p for p in self.data["pending"] if p.get("state") == "active"]
        return {
            "window_size": self.data["window_size"],
            "high_water": hwm,
            "window": self._window_for(hwm),
            "ledger_seals": sorted(int(e["seal"]) for e in self.data["ledger"]),
            "direct_seals": sorted(
                int(e["seal"])
                for e in self.data["ledger"]
                if e.get("source") == "direct_window"
            ),
            "manual_seals": sorted(
                int(e["seal"])
                for e in self.data["ledger"]
                if e.get("source") == "manual_reconciliation"
            ),
            "pending_seals": sorted(int(p["seal"]) for p in active_pending),
            "ledger_count": len(self.data["ledger"]),
            "pending_count": len(active_pending),
            "stats": self.data["stats"],
        }
