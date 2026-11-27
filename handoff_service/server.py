"""Standard-library HTTP wrapper.

Endpoints:
  GET  /health
  GET  /api/state
  POST /api/dispatch   {"op":"input", ...}
  POST /v1/command      same envelope, for compatibility with local request scripts
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .core import HandOffError, handle
from .store import DEFAULT_STATE_PATH, load_state, save_state


_LOCK = threading.RLock()
_STATE_PATH = DEFAULT_STATE_PATH


class Handler(BaseHTTPRequestHandler):
    server_version = "PeripheralHandOff/1.0"

    def _send(self, status: int, body: dict[str, Any]) -> None:
        data = json.dumps(body, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        if self.path.split("?", 1)[0] == "/health":
            self._send(200, {"ok": True, "service": "peripheral-handoff"})
            return
        if self.path.split("?", 1)[0] == "/api/state":
            with _LOCK:
                self._send(200, {"ok": True, "state": load_state(_STATE_PATH)})
            return
        self._send(404, {"ok": False, "error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path not in {"/api/dispatch", "/v1/command", "/command"}:
            self._send(404, {"ok": False, "error": "not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            command = json.loads(self.rfile.read(length) or b"{}")
        except (json.JSONDecodeError, ValueError) as exc:
            self._send(400, {"ok": False, "error": "bad_json", "message": str(exc)})
            return

        with _LOCK:
            state = load_state(_STATE_PATH)
            try:
                result = handle(state, command)
                save_state(state, _STATE_PATH)
            except HandOffError as exc:
                # Persist any audit/projection update made before a physical
                # precondition failure, then report the policy error.
                save_state(state, _STATE_PATH)
                status = 409 if exc.code in {"confirmation_required", "lever_conflict", "backup_slot_occupied"} else 400
                self._send(status, {"ok": False, "error": exc.code, "message": exc.message, "details": exc.details})
                return
        status = 200 if result.get("ok", True) is not False else 422
        self._send(status, result)

    def log_message(self, fmt: str, *args: Any) -> None:
        # Keep local test output clean; errors remain visible through responses.
        return


def serve(host: str = "127.0.0.1", port: int = 8765, state_path: str | None = None) -> None:
    global _STATE_PATH
    _STATE_PATH = state_path or DEFAULT_STATE_PATH
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"peripheral hand-off service on http://{host}:{port} (state: {_STATE_PATH})", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    serve()
