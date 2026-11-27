"""Local online witness/registry simulator.

It binds only to 127.0.0.1.  In the exercise the witness time is a controlled
simulation clock (``cabinet/private/clock.txt`` or ``POST /admin/clock``); this
is explicitly a *simulated* public receipt service.  Real deployments would
replace that clock/admin surface with a trusted timestamp source.
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .common import DELIVERABLES, PRIVATE, TRUST_DIR, make_envelope, write_json
from .scenario import AUTHORITIES, private_key

CLOCK_FILE = PRIVATE / "clock.txt"
TOKEN_FILE = PRIVATE / "admin-token"
LOG_DIR = PRIVATE / "witness-log"


def current_simulated_time() -> str:
    if CLOCK_FILE.exists():
        value = CLOCK_FILE.read_text(encoding="utf-8").strip()
        if value:
            return value
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def admin_token() -> str:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not TOKEN_FILE.exists():
        TOKEN_FILE.write_text(secrets.token_urlsafe(24), encoding="utf-8")
        TOKEN_FILE.chmod(0o600)
    return TOKEN_FILE.read_text(encoding="utf-8").strip()


class WitnessHandler(BaseHTTPRequestHandler):
    server_version = "ArchiveCoopSim/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:  # quieter tests
        return

    def send_json(self, status: int, obj: Any) -> None:
        body = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        obj = json.loads(raw.decode("utf-8") or "{}")
        if not isinstance(obj, dict):
            raise ValueError("request body must be a JSON object")
        return obj

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/health":
            self.send_json(200, {"ok": True, "online": True, "simulated_now": current_simulated_time()})
            return
        if path == "/policy":
            policy = DELIVERABLES / "policy.json"
            if not policy.exists():
                self.send_json(404, {"error": "policy not built; run ./coop build"})
                return
            self.send_json(200, json.loads(policy.read_text(encoding="utf-8")))
            return
        if path.startswith("/trust/"):
            name = Path(path.removeprefix("/trust/")).name
            p = TRUST_DIR / name
            if not p.is_file():
                self.send_json(404, {"error": "public key not found"})
                return
            body = p.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/x-pem-file")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path.startswith("/v1/registry/"):
            manuscript_id = Path(path.removeprefix("/v1/registry/")).name
            log = LOG_DIR / "registry-2026.jsonl"
            records = []
            if log.is_file():
                for line in log.read_text(encoding="utf-8").splitlines():
                    try:
                        env = json.loads(line)
                        if env.get("data", {}).get("manuscript_id") == manuscript_id:
                            records.append(env)
                    except json.JSONDecodeError:
                        continue
            if not records:
                self.send_json(404, {"error": "no online registry records for manuscript",
                                     "manuscript_id": manuscript_id})
                return
            self.send_json(200, {"manuscript_id": manuscript_id, "records": records})
            return
        self.send_json(404, {"error": "not found", "path": path})
        return

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            body = self.read_json_body()
        except Exception as exc:  # noqa: BLE001
            self.send_json(400, {"error": f"invalid JSON: {exc}"})
            return

        if path == "/admin/clock":
            token = self.headers.get("X-Admin-Token", "")
            if not secrets.compare_digest(token, admin_token()):
                self.send_json(403, {"error": "invalid simulation admin token"})
                return
            now = body.get("now")
            if not isinstance(now, str):
                self.send_json(400, {"error": "now must be an ISO string"})
                return
            CLOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
            CLOCK_FILE.write_text(now, encoding="utf-8")
            self.send_json(200, {"simulated_now": now})
            return

        prefix = "/v1/witness/"
        suffix = "/sign"
        if path.startswith(prefix) and path.endswith(suffix):
            key_id = path[len(prefix):-len(suffix)]
            if key_id not in AUTHORITIES:
                self.send_json(404, {"error": f"unknown witness/authority {key_id}"})
                return
            data = body.get("data")
            if not isinstance(data, dict):
                self.send_json(400, {"error": "data object is required"})
                return
            # Honest service fixes the event time from its (simulated) clock.
            data["issued_at"] = current_simulated_time()
            data.setdefault("simulated_online_receipt", True)
            try:
                envelope = make_envelope(data, key_id, private_key(key_id))
            except Exception as exc:  # noqa: BLE001
                self.send_json(500, {"error": f"signing failed: {exc}"})
                return
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            with (LOG_DIR / f"{key_id}.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(envelope, ensure_ascii=False) + "\n")
            self.send_json(201, envelope)
            return

        self.send_json(404, {"error": "not found", "path": path})


def serve(host: str = "127.0.0.1", port: int = 0, endpoint_file: str | Path | None = None) -> None:
    PRIVATE.mkdir(parents=True, exist_ok=True)
    httpd = ThreadingHTTPServer((host, port), WitnessHandler)
    actual_port = httpd.server_address[1]
    base_url = f"http://{host}:{actual_port}"
    token = admin_token()  # create before publishing endpoint metadata
    if endpoint_file:
        write_json(endpoint_file, {
            "base_url": base_url,
            "port": actual_port,
            "pid": __import__("os").getpid(),
            "admin_token_file": str(TOKEN_FILE),
            "admin_token_fingerprint": __import__("hashlib").sha256(token.encode()).hexdigest(),
        })
    print(base_url, flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description="Run local simulated witness service")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=0)
    p.add_argument("--write-endpoint", default=str(PRIVATE / "server-endpoint.json"))
    args = p.parse_args()
    serve(args.host, args.port, args.write_endpoint)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
