"""Write-side operations for the offline remediation desk.

The store is an append-friendly JSON document.  Historical inventory reports
and closures are never edited in place; corrections are new records.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from .models import (
    AdvisoryStatus,
    ClosureKind,
    DEFAULT_DATA,
    PromiseStatus,
    TicketStatus,
    load_desk,
    next_id,
    save_desk,
)


class Desk:
    def __init__(self, path: str | Path | None = None, data: dict[str, Any] | None = None):
        self.path = Path(path) if path else None
        if data is not None:
            self.data = data
        elif self.path:
            self.data = load_desk(self.path)
        else:
            import copy

            self.data = copy.deepcopy(DEFAULT_DATA)

    def save(self) -> "Desk":
        if self.path:
            save_desk(self.data, self.path)
        return self

    # ---------- lookup helpers ----------

    def _find(self, collection: str, **matches: Any) -> dict[str, Any] | None:
        for row in self.data[collection]:
            if all(row.get(k) == v for k, v in matches.items()):
                return row
        return None

    def get(self, collection: str, row_id: str) -> dict[str, Any]:
        row = self._find(collection, id=row_id)
        if row is None:
            raise KeyError(f"{collection} id not found: {row_id}")
        return row

    def machine(self, machine_no: str) -> dict[str, Any]:
        row = self._find("machines", machine_no=machine_no)
        if row is None:
            raise KeyError(f"machine not registered: {machine_no}")
        return row

    def reports_for(self, machine_no: str) -> list[dict[str, Any]]:
        return [r for r in self.data["inventory_reports"] if r["machine_no"] == machine_no]

    def latest_inventory(self, machine_no: str) -> dict[str, Any] | None:
        rows = self.reports_for(machine_no)
        return max(rows, key=lambda r: r["seq"]) if rows else None

    def active_advisory(self, code: str | None = None) -> dict[str, Any] | None:
        for adv in self.data["advisories"]:
            if adv["status"] != AdvisoryStatus.ACTIVE.value:
                continue
            if code is None or adv["code"] == code:
                return adv
        return None

    # ---------- master data and bulletins ----------

    def register_machine(
        self,
        machine_no: str,
        label: str = "",
        desk: str = "",
        service: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        existing = self._find("machines", machine_no=machine_no)
        if existing:
            return existing
        row = {
            "machine_no": machine_no,
            "label": label,
            "desk": desk,
            "service": service or {},
        }
        self.data["machines"].append(row)
        return row

    def add_advisory(
        self,
        code: str,
        revision: int,
        part_numbers: Iterable[str],
        affected_range: str | list[str],
        fixed_version: str,
        *,
        advisory_id: str | None = None,
        title: str = "",
        enablement: str = "FUNCTION_ENABLED",
        accepted_routes: Iterable[str] = ("REPLACED_NEW_PROCESS", "FUNCTIONS_DISABLED"),
        note: str = "",
        issued_at: str = "",
        status: str = AdvisoryStatus.ACTIVE.value,
    ) -> dict[str, Any]:
        adv_id = advisory_id or f"{code}-R{revision}"
        row = {
            "id": adv_id,
            "code": code,
            "revision": revision,
            "title": title,
            "status": status,
            "part_numbers": list(part_numbers),
            "affected_range": affected_range
            if isinstance(affected_range, list)
            else [p.strip() for p in affected_range.split(",")],
            "fixed_version": fixed_version,
            "enablement": enablement,
            "accepted_routes": list(accepted_routes),
            "note": note,
            "issued_at": issued_at,
            "successor_id": None,
            "withdrawn_reason": None,
        }
        self.data["advisories"].append(row)
        return row

    def withdraw_advisory(
        self, advisory_id: str, reason: str, *, at: str = ""
    ) -> dict[str, Any]:
        adv = self.get("advisories", advisory_id)
        adv["status"] = AdvisoryStatus.WITHDRAWN.value
        adv["withdrawn_reason"] = reason
        adv["withdrawn_at"] = at
        return adv

    def replace_advisory(
        self,
        old_id: str,
        new_advisory: dict[str, Any],
        *,
        reason: str,
        at: str = "",
        withdrawn: bool = False,
    ) -> dict[str, Any]:
        old = self.get("advisories", old_id)
        old["status"] = (
            AdvisoryStatus.WITHDRAWN.value if withdrawn else AdvisoryStatus.SUPERSEDED.value
        )
        old["successor_id"] = new_advisory["id"]
        old["withdrawn_reason"] = reason
        old["withdrawn_at"] = at
        if not self._find("advisories", id=new_advisory["id"]):
            self.data["advisories"].append(new_advisory)
        return new_advisory

    # ---------- evidence ----------

    def submit_inventory(
        self,
        machine_no: str,
        components: list[dict[str, Any]],
        *,
        report_id: str | None = None,
        captured_at: str = "",
        reporter: str = "field-count",
        whole_model: str = "",
        whole_version: str = "",
        raw_note: str = "",
    ) -> dict[str, Any]:
        self.machine(machine_no)
        seq = int(self.data.get("sequence", 0)) + 1
        self.data["sequence"] = seq
        row = {
            "id": report_id or f"INV-{seq:04d}",
            "seq": seq,
            "machine_no": machine_no,
            "captured_at": captured_at or self.data.get("current_shift", ""),
            "reporter": reporter,
            "whole_model": whole_model,
            "whole_version": whole_version,
            "components": components,
            "raw_note": raw_note,
        }
        self.data["inventory_reports"].append(row)
        return row

    def add_repair_receipt(
        self,
        machine_no: str,
        *,
        receipt_id: str | None = None,
        completed_at: str = "",
        action: str = "REPLACE",
        part_no: str = "",
        from_version: str = "",
        to_version: str = "",
        covered_uids: list[str] | None = None,
        installer: str = "maintenance-desk",
        paper_ref: str = "",
        note: str = "",
    ) -> dict[str, Any]:
        self.machine(machine_no)
        row = {
            "id": receipt_id or next_id(self.data, "RR"),
            "machine_no": machine_no,
            "completed_at": completed_at or self.data.get("current_shift", ""),
            "action": action,
            "part_no": part_no,
            "from_version": from_version,
            "to_version": to_version,
            "covered_uids": covered_uids or [],
            "installer": installer,
            "paper_ref": paper_ref,
            "note": note,
        }
        self.data["repair_receipts"].append(row)
        return row

    def add_disablement_receipt(
        self,
        machine_no: str,
        *,
        receipt_id: str | None = None,
        issued_at: str = "",
        covered_uids: list[str] | None = None,
        covered_functions: list[str] | None = None,
        durable: bool = True,
        temporary: bool = False,
        old_processes_stopped: bool = True,
        control: str = "CONFIG_LOCKED_AND_SIGNED",
        valid_through_shift: str | None = None,
        paper_ref: str = "",
        note: str = "",
    ) -> dict[str, Any]:
        self.machine(machine_no)
        if temporary:
            durable = False
        row = {
            "id": receipt_id or next_id(self.data, "DIS"),
            "machine_no": machine_no,
            "issued_at": issued_at or self.data.get("current_shift", ""),
            "covered_uids": covered_uids or [],
            "covered_functions": covered_functions or [],
            "durable": durable,
            "temporary": temporary,
            "old_processes_stopped": old_processes_stopped,
            "control": control,
            "valid_through_shift": valid_through_shift,
            "paper_ref": paper_ref,
            "note": note,
        }
        self.data["disablement_receipts"].append(row)
        return row

    def add_staffing_report(
        self,
        desk: str,
        shift: str,
        manual_backup_available: bool,
        *,
        source: str = "duty-roster",
        reliable: bool = True,
        report_id: str | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        row = {
            "id": report_id or next_id(self.data, "STAFF"),
            "desk": desk,
            "shift": shift,
            "manual_backup_available": manual_backup_available,
            "source": source,
            "reliable": reliable,
            "note": note,
        }
        self.data["staffing_reports"].append(row)
        return row

    def add_resource_event(
        self,
        resource_no: str,
        *,
        event: str,
        machine_no: str | None = None,
        available: bool | None = None,
        linked_machine: str | None = None,
        event_id: str | None = None,
        at: str = "",
        note: str = "",
    ) -> dict[str, Any]:
        row = {
            "id": event_id or next_id(self.data, "RES"),
            "resource_no": resource_no,
            "event": event,
            "machine_no": machine_no,
            "available": available,
            "linked_machine": linked_machine,
            "at": at or self.data.get("current_shift", ""),
            "note": note,
        }
        self.data["resource_events"].append(row)

        # A spare resource backs a NO_DOWNTIME_REPLACE promise, not the security
        # state of the terminal itself. Keep that promise derived from events.
        available = self.spare_available(resource_no)
        for machine in self.data["machines"]:
            if machine.get("service", {}).get("spare_no") != resource_no:
                continue
            for ticket in self.data["tickets"]:
                if ticket["machine_no"] != machine["machine_no"]:
                    continue
                if ticket.get("order_kind") != "NO_DOWNTIME_REPLACE":
                    continue
                promise = ticket.setdefault("promise", {})
                if ticket.get("status") in {TicketStatus.CLOSED.value, TicketStatus.CANCELED.value}:
                    continue
                if available:
                    if promise.get("status") == PromiseStatus.IMPOSSIBLE.value:
                        promise["status"] = PromiseStatus.AVAILABLE.value
                        promise["reason"] = f"{resource_no} is available again."
                else:
                    promise["status"] = PromiseStatus.IMPOSSIBLE.value
                    promise["reason"] = (
                        f"{resource_no} is unavailable after {event}; no reliable "
                        "no-downtime replacement promise can be made."
                    )
                    promise.setdefault(
                        "options",
                        ["recover the spare", "schedule a downtime replacement window"],
                    )
        return row

    # ---------- tickets / closures ----------

    def create_ticket(
        self,
        machine_no: str,
        advisory_code: str,
        *,
        ticket_id: str | None = None,
        order_kind: str = "STANDARD_REMEDIATION",
        promise: dict[str, Any] | None = None,
        opened_at: str = "",
        note: str = "",
    ) -> dict[str, Any]:
        self.machine(machine_no)
        existing = self._find(
            "tickets", machine_no=machine_no, advisory_code=advisory_code
        )
        if existing and existing["status"] != TicketStatus.CANCELED.value:
            return existing
        row = {
            "id": ticket_id or next_id(self.data, "TKT"),
            "machine_no": machine_no,
            "advisory_code": advisory_code,
            "order_kind": order_kind,
            "status": TicketStatus.OPEN.value,
            "opened_at": opened_at or self.data.get("current_shift", ""),
            "promise": promise
            or {
                "kind": "DOWNTIME_REPLACE",
                "status": PromiseStatus.AVAILABLE.value,
                "through_shift": None,
            },
            "items": [],
            "note": note,
            "status_history": [
                {
                    "status": TicketStatus.OPEN.value,
                    "at": opened_at or self.data.get("current_shift", ""),
                    "reason": "ticket opened",
                }
            ],
        }
        self.data["tickets"].append(row)
        return row

    def add_closure(self, closure: dict[str, Any]) -> dict[str, Any]:
        self.data["closures"].append(closure)
        return closure

    def latest_staffing(self, desk: str, shift: str) -> dict[str, Any] | None:
        rows = [
            r
            for r in self.data["staffing_reports"]
            if r["desk"] == desk and r["shift"] == shift
        ]
        return rows[-1] if rows else None

    def spare_available(self, resource_no: str) -> bool:
        events = [e for e in self.data["resource_events"] if e["resource_no"] == resource_no]
        if not events:
            return False
        latest = events[-1]
        if latest.get("available") is not None:
            return bool(latest["available"])
        return latest["event"] not in {"LOANED_OUT", "CONSUMED", "BROKEN"}
