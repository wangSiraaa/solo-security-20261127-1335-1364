"""Dependency-free JSON HTTP backend for the local drill."""

from __future__ import annotations

import json
import os
import re
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .engine import Engine
from .errors import DrillError
from .planner import apply_selected_boundary, build_plan
from .scenario import run_branch, run_empty_walkthrough, run_old_plan
from .store import Store

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def create_handler(engine: Engine):
    class DrillHandler(BaseHTTPRequestHandler):
        server_version = "HandbackDrill/1.0"

        def log_message(self, fmt: str, *args: Any) -> None:
            # Keep the local terminal readable; errors are still returned as JSON.
            print(f"[http] {self.address_string()} - {fmt % args}")

        def _send_json(self, payload: Any, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_file(self, path: Path, content_type: str) -> None:
            try:
                body = path.read_bytes()
            except FileNotFoundError:
                self._send_json({"ok": False, "message": "dashboard not found"}, 404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            if length == 0:
                return {}
            raw = self.rfile.read(length)
            if not raw:
                return {}
            try:
                value = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise DrillError("bad_json", f"请求体不是合法 JSON: {exc}", 400)
            if not isinstance(value, dict):
                raise DrillError("bad_request", "请求体必须是 JSON 对象", 400)
            return value

        def do_GET(self) -> None:  # noqa: N802
            try:
                parsed = urlparse(self.path)
                path = parsed.path.rstrip("/") or "/"
                query = parse_qs(parsed.query)
                at = int(query["at"][0]) if "at" in query else None

                if path in ("/", "/ui", "/ui/index.html"):
                    index = WEB_DIR / "index.html"
                    if index.exists():
                        self._send_file(index, "text/html; charset=utf-8")
                    else:
                        self._send_json(self._index())
                    return
                if path == "/app.js":
                    self._send_file(WEB_DIR / "app.js", "application/javascript; charset=utf-8")
                    return
                if path == "/styles.css":
                    self._send_file(WEB_DIR / "styles.css", "text/css; charset=utf-8")
                    return
                if path == "/api/health":
                    self._send_json({"ok": True, "service": "handback-drill"})
                elif path == "/api/state":
                    self._send_json(engine.state())
                elif path == "/api/authority":
                    self._send_json(engine.authority_snapshot(at))
                elif path == "/api/matrix":
                    self._send_json(engine.order_matrix(at))
                elif path == "/api/plan":
                    self._send_json(build_plan(engine, at))
                elif path == "/api/image/snapshots":
                    self._send_json({"ok": True, "snapshots": engine.list_image_snapshots()})
                elif path == "/api/orders":
                    self._send_json({"ok": True, "orders": engine.list_orders()})
                elif path == "/api/todos":
                    self._send_json({"ok": True, "todos": engine.todos(open_only=False)})
                elif path == "/api/replay":
                    case = query.get("case", ["reconnect"])[0]
                    self._send_json(self._replay(case, query.get("late_ack", ["0"])[0] in ("1", "true", "yes")))
                elif m := re.fullmatch(r"/api/orders/([^/]+)", path):
                    self._send_json({"ok": True, "order": engine.order_detail(m.group(1))})
                else:
                    self._send_json({"ok": False, "error": "not_found", "path": path}, 404)
            except DrillError as exc:
                self._send_json(exc.to_dict(), exc.status)
            except Exception as exc:  # pragma: no cover - local server guard
                self._send_json({"ok": False, "error": "internal", "message": str(exc)}, 500)

        def do_POST(self) -> None:  # noqa: N802
            try:
                parsed = urlparse(self.path)
                path = parsed.path.rstrip("/")
                body = self._body()
                result, status = self._route_post(path, body)
                self._send_json(result, status)
            except DrillError as exc:
                self._send_json(exc.to_dict(), exc.status)
            except Exception as exc:  # pragma: no cover
                self._send_json({"ok": False, "error": "internal", "message": str(exc)}, 500)

        def do_PATCH(self) -> None:  # noqa: N802
            try:
                parsed = urlparse(self.path)
                body = self._body()
                m = re.fullmatch(r"/api/orders/([^/]+)", parsed.path.rstrip("/"))
                if not m:
                    raise DrillError("not_found", "没有这个 PATCH 路径", 404)
                self._send_json({"ok": True, "order": engine.amend_order(m.group(1), body)})
            except DrillError as exc:
                self._send_json(exc.to_dict(), exc.status)

        def _route_post(self, path: str, body: dict[str, Any]):
            if path == "/api/reset":
                return engine.reset(body.get("scenario", "empty")), 200
            if path == "/api/admin/restart":
                return engine.restart_review(), 200
            if path == "/api/image/restore":
                return engine.restore_image_claim(body), 200
            if path == "/api/image/snapshot":
                return engine.snapshot_image(body.get("label", "manual"), body.get("snapshot_id")), 201
            if path == "/api/clock/advance":
                return engine.advance(to=body.get("to"), beats=int(body.get("beats", 1))), 200
            if path == "/api/plan/apply":
                return apply_selected_boundary(engine, body.get("at")), 200
            if path == "/api/orders":
                return {"ok": True, "order": engine.create_order(body)}, 201
            if path == "/api/claim-next":
                station = body.get("station")
                if not station:
                    raise DrillError("missing_station", "请在 JSON 中提供 station", 400)
                return {"ok": True, "claim": engine.claim_next(station, body.get("at"))}, 200

            if m := re.fullmatch(r"/api/orders/([^/]+)/(claim|issue|amend)", path):
                order_id, action = m.group(1), m.group(2)
                if action == "claim":
                    return {"ok": True, "order": engine.claim_order(order_id, body.get("station"), body.get("at"))}, 200
                if action == "issue":
                    return engine.issue_order(order_id, body.get("station"), body.get("at")), 200
                if action == "amend":
                    return {"ok": True, "order": engine.amend_order(order_id, body)}, 200

            if m := re.fullmatch(r"/api/switch/([ABab])/(queue|deliver|ack|succeed)", path):
                code, action = m.group(1).upper(), m.group(2)
                if action == "queue":
                    return engine.queue_switch(code), 200
                if action == "deliver":
                    return engine.deliver_switch(code, force=bool(body.get("force"))), 200
                if action == "ack":
                    return engine.acknowledge_switch(code, body.get("receipt_id")), 200
                return engine.successful_switch(code, force=bool(body.get("force"))), 200

            if m := re.fullmatch(r"/api/cabinets/([ABab])/route", path):
                code = m.group(1).upper()
                online = bool(body.get("online", True))
                route = body.get("route", "new")
                return engine.set_route(code, online, route), 200

            raise DrillError("not_found", f"没有这个 POST 路径: {path}", 404)

        def _replay(self, case: str, late_ack: bool):
            path = self._new_temp_db()
            if case == "empty":
                try:
                    return run_empty_walkthrough(path)
                finally:
                    self._cleanup_temp_db(path)
            if case == "old-plan":
                try:
                    return run_old_plan(path)
                finally:
                    self._cleanup_temp_db(path)
            if case not in ("reconnect", "offline"):
                raise DrillError("bad_replay_case", "case 必须是 reconnect、offline、empty 或 old-plan", 400)
            try:
                return run_branch(case, path, late_ack=late_ack)
            finally:
                self._cleanup_temp_db(path)

        @staticmethod
        def _new_temp_db() -> str:
            fd, path = tempfile.mkstemp(prefix="handback-replay-", suffix=".db")
            os.close(fd)
            os.unlink(path)
            return path

        @staticmethod
        def _cleanup_temp_db(path: str) -> None:
            for suffix in ("", "-wal", "-shm"):
                try:
                    Path(path + suffix).unlink(missing_ok=True)
                except OSError:
                    pass

        @staticmethod
        def _index():
            return {
                "ok": True,
                "service": "handback-drill",
                "ui": "/ui",
                "endpoints": [
                    "POST /api/reset",
                    "GET  /api/authority?at=0",
                    "GET  /api/matrix",
                    "GET  /api/plan",
                    "POST /api/plan/apply",
                    "POST /api/switch/A/deliver",
                    "POST /api/switch/A/ack",
                    "POST /api/orders",
                    "POST /api/orders/{id}/claim",
                    "POST /api/orders/{id}/issue",
                    "POST /api/claim-next",
                    "POST /api/clock/advance",
                    "POST /api/cabinets/B/route",
                    "POST /api/admin/restart",
                    "GET  /api/replay?case=reconnect",
                ],
            }

    return DrillHandler


def serve(db_path: str = "handback-drill.db", host: str = "127.0.0.1", port: int = 8787):
    store = Store(db_path)
    engine = Engine(store)
    if store.meta_get("tick") is None:
        engine.reset("empty")
    httpd = ThreadingHTTPServer((host, port), create_handler(engine))
    print(f"Handback drill listening on http://{host}:{port}")
    print(f"SQLite durable file: {Path(db_path).resolve()}")
    print("Try: ./drill replay reconnect   |   ./drill smoke")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
    finally:
        httpd.server_close()
        store.close()
