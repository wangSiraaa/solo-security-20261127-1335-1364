"""Dependency-free local web UI and JSON API."""
from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .scenario import build_full_correction_demo, incident_setup
from .service import CouponError, CouponService

STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_server(service: CouponService, *, host: str, port: int) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        server_version = "CouponDemo/1.0"

        def log_message(self, fmt: str, *args: Any) -> None:
            # Keep local terminal output concise; never log request bodies here.
            super().log_message("%s - %s", self.address_string(), fmt % args)

        def _send_json(self, value: Any, status: int = 200) -> None:
            body = json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_static(self, filename: str, content_type: str) -> None:
            if filename not in {"index.html", "app.js", "styles.css"}:
                self.send_error(404)
                return
            path = STATIC_DIR / filename
            body = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length == 0:
                return {}
            raw = self.rfile.read(length)
            try:
                value = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise CouponError("request body must be valid JSON object") from exc
            if not isinstance(value, dict):
                raise CouponError("request body must be a JSON object")
            return value

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path
            try:
                if path == "/":
                    self._send_static("index.html", "text/html; charset=utf-8")
                elif path == "/app.js":
                    self._send_static("app.js", "text/javascript; charset=utf-8")
                elif path == "/styles.css":
                    self._send_static("styles.css", "text/css; charset=utf-8")
                elif path == "/api/state":
                    self._send_json({"ok": True, "state": service.get_state()})
                elif path == "/api/evaluate":
                    qs = parse_qs(parsed.query)
                    ticket = qs.get("ticket_no", [""])[0]
                    counter = qs.get("counter_id", [None])[0]
                    self._send_json(
                        {"ok": True, "evaluation": service.evaluate_coupon(ticket, counter)}
                    )
                elif m := re.fullmatch(r"/api/segments/([^/]+)/continuation", path):
                    qs = parse_qs(parsed.query)
                    machine_id = qs.get("machine_id", [""])[0]
                    self._send_json(
                        {"ok": True, "continuation": service.can_continue_segment(m.group(1), machine_id)}
                    )
                else:
                    self.send_error(404, "not found")
            except CouponError as exc:
                self._send_json(
                    {"ok": False, "error": exc.code, "message": exc.message, "details": exc.details},
                    exc.http_status,
                )
            except Exception as exc:  # local demo: surface failure in JSON
                self._send_json({"ok": False, "error": "internal", "message": str(exc)}, 500)

        def do_POST(self) -> None:  # noqa: N802,C901
            path = urlparse(self.path).path
            try:
                body = self._read_json()
                result = self._route_post(path, body)
                self._send_json({"ok": True, "result": result})
            except CouponError as exc:
                self._send_json(
                    {"ok": False, "error": exc.code, "message": exc.message, "details": exc.details},
                    exc.http_status,
                )
            except Exception as exc:
                self._send_json({"ok": False, "error": "internal", "message": str(exc)}, 500)

        def _route_post(self, path: str, body: dict[str, Any]) -> Any:  # noqa: C901
            s = service

            if path == "/api/demo/seed":
                if body.get("reset", True):
                    s.reset_data()
                return build_full_correction_demo(s)
            if path == "/api/demo/incident-setup":
                if body.get("reset", False):
                    s.reset_data()
                return incident_setup(s)

            if path == "/api/materials":
                return s.register_material(**_pick(body, ["lot_id", "kind", "quantity", "note"]))

            if path == "/api/backups":
                return s.create_shared_backup(**_pick(body, ["backup_id", "seed", "segment_id"]))
            if path == "/api/backups/restore":
                return s.restore_source_from_backup(
                    body["backup_id"], body["machine_id"],
                    **_pick(body, ["source_id", "start_seq"]),
                )

            if path == "/api/sources/independent":
                return s.install_independent_source(
                    body["machine_id"], **_pick(body, ["source_id", "seed", "freeze_old_state"])
                )
            if path == "/api/station/new-seed":
                return s.visit_new_seed_station(
                    body.get("machine_id", "printer-b"), seed=body.get("seed")
                )
            if path == "/api/station/old-cabinet":
                return s.visit_old_cabinet(
                    body["stack_id"],
                    **_pick(body, ["inspector", "seal_intact", "witness_id", "note"]),
                )
            if m := re.fullmatch(r"/api/sources/([^/]+)/reseed", path):
                return s.reseed_source(m.group(1), body.get("new_seed"))

            if path == "/api/packets/print":
                options = _pick(body, [
                    "source_id", "material_lot_id", "count", "ticket_prefix",
                    "ticket_nos", "title", "purpose", "planned_count",
                    "holder_names", "real_name", "seal_id", "note",
                ])
                return s.print_packet(body.get("stack_id"), **options)

            if path == "/api/handoffs":
                return s.create_handoff(body["stack_id"], **_pick(body, [
                    "handoff_id", "from_party", "to_party", "complete",
                    "documents", "missing", "signed_by", "note",
                ]))
            if m := re.fullmatch(r"/api/handoffs/([^/]+)/complete", path):
                return s.complete_handoff(m.group(1), **_pick(body, [
                    "documents", "signed_by", "counter_id", "create_counter_receipt",
                ]))

            if path == "/api/deliver":
                return s.deliver_to_counter(body["stack_id"], **_pick(body, [
                    "counter_id", "signed_by", "receipt_id", "documents",
                ]))
            if m := re.fullmatch(r"/api/receipts/([^/]+)/retarget", path):
                return s.retarget_counter_receipt(
                    m.group(1), body["new_stack_id"], **_pick(body, ["reason", "signed_by"])
                )

            if m := re.fullmatch(r"/api/coupons/([^/]+)/claim", path):
                return s.claim_coupon(m.group(1), **_pick(body, [
                    "holder_name", "id_reference", "counter_id",
                ]))
            if m := re.fullmatch(r"/api/coupons/([^/]+)/legacy-claim", path):
                return s.record_legacy_claim(m.group(1), **_pick(body, [
                    "holder_name", "id_reference", "note",
                ]))
            if path == "/api/redeem":
                return s.redeem_coupon(
                    body["ticket_no"], body["secret"], **_pick(body, ["counter_id", "benefit"])
                )

            if m := re.fullmatch(r"/api/segments/([^/]+)/witness", path):
                return s.update_segment_witness(m.group(1), **_pick(body, [
                    "witness_scope", "reprint_confirmed", "machine_ids",
                    "note", "confirmation_ref",
                ]))
            if m := re.fullmatch(r"/api/segments/([^/]+)/confirm-reprint", path):
                return s.confirm_reprint_from_witnesses(m.group(1))
            if m := re.fullmatch(r"/api/replacements/([^/]+)/complete", path):
                return s.complete_replacement_order(
                    m.group(1), body["new_stack_id"], body["new_ticket_no"]
                )

            raise CouponError(f"unknown API route: {path}")

    return ThreadingHTTPServer((host, port), Handler)


def _pick(body: dict[str, Any], keys: list[str]) -> dict[str, Any]:
    return {k: body[k] for k in keys if k in body}
