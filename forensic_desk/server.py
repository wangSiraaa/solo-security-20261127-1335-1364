"""Optional JSON HTTP adapters for the intake and review desks.

The core desk API and CLI do not depend on this module.  It exists so the two
roles can be exposed as local HTTP ends without adding a web framework.
"""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from .disk import safe_artifact_id
from .intake import IntakeDesk
from .review import ReviewDesk
from .station import verify_station_bundle


CASE_RE = re.compile(r"^/(?:intake|review)/cases/(?P<case_id>[A-Za-z0-9_.:-]+)(?:/(?P<rest>.*))?$")


class DeskHttpServer(ThreadingHTTPServer):
    def __init__(self, cases_root: str | Path, host: str = "127.0.0.1", port: int = 0):
        self.cases_root = Path(cases_root)
        self.cases_root.mkdir(parents=True, exist_ok=True)
        super().__init__((host, port), DeskRequestHandler)


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: Any) -> None:
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _read_json(handler: BaseHTTPRequestHandler) -> Dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0"))
    if length == 0:
        return {}
    raw = handler.rfile.read(length)
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("request body must be a JSON object")
    return value


class DeskRequestHandler(BaseHTTPRequestHandler):
    server: DeskHttpServer

    def log_message(self, fmt: str, *args: Any) -> None:
        # Keep test/demo output clean; errors are still returned as JSON.
        return

    def _case_dir(self, case_id: str) -> Path:
        safe_artifact_id(case_id)
        return self.server.cases_root / case_id

    def do_GET(self) -> None:  # noqa: N802 - stdlib name
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/health":
            _json_response(self, 200, {"ok": True, "service": "forensic-desk"})
            return
        if path == "/":
            _json_response(
                self,
                200,
                {
                    "intake": "POST /intake/cases/<case_id>",
                    "amend": "POST /intake/cases/<case_id>/amendments",
                    "review": "POST /review/cases/<case_id>/requests (X-Investigator)",
                    "record": "GET /cases/<case_id>/records/<record_id>",
                    "provenance": "GET /review/cases/<case_id>/provenance/<ref>?as=<id>",
                    "verify_station": "GET /verify-station?path=<bundle>",
                },
            )
            return
        if path == "/verify-station":
            bundle = query.get("path", [""])[0]
            try:
                _json_response(self, 200, verify_station_bundle(bundle))
            except Exception as exc:  # noqa: BLE001 - boundary returns error JSON
                _json_response(self, 400, {"ok": False, "error": str(exc)})
            return

        shared = re.match(r"^/cases/(?P<case_id>[A-Za-z0-9_.:-]+)/records/(?P<record_id>.+)$", path)
        if shared:
            try:
                record = IntakeDesk(self._case_dir(shared.group("case_id"))).ledger.require(
                    shared.group("record_id")
                )
                _json_response(self, 200, record)
            except KeyError as exc:
                _json_response(self, 404, {"error": str(exc)})
            return

        match = CASE_RE.match(path)
        if not match:
            _json_response(self, 404, {"error": "not found"})
            return
        case_id = match.group("case_id")
        rest = match.group("rest") or ""

        try:
            if rest.startswith("records/"):
                record_id = rest.split("/", 1)[1]
                record = ReviewDesk(self._case_dir(case_id), query.get("as", ["anonymous"])[0]).get_record(record_id)
                _json_response(self, 200, record)
                return
            if rest.startswith("provenance/"):
                ref = rest.split("/", 1)[1]
                investigator = self._investigator(query, {})
                desk = ReviewDesk(self._case_dir(case_id), investigator)
                _json_response(self, 200, desk.review_provenance(ref))
                return
        except KeyError as exc:
            _json_response(self, 404, {"error": str(exc)})
            return
        except Exception as exc:  # noqa: BLE001
            _json_response(self, 400, {"error": str(exc)})
            return

        _json_response(self, 404, {"error": "unsupported GET route"})

    @staticmethod
    def _investigator(query: Dict[str, list[str]], body: Dict[str, Any]) -> str:
        header = None
        # Header is accessed by caller in do_POST; this helper handles query/body.
        return str(body.get("investigator_id") or query.get("as", ["anonymous"])[0])

    def do_POST(self) -> None:  # noqa: N802
        match = CASE_RE.match(urlparse(self.path).path)
        if not match:
            _json_response(self, 404, {"error": "not found"})
            return
        case_id = match.group("case_id")
        rest = match.group("rest") or ""
        try:
            body = _read_json(self)
            case_dir = self._case_dir(case_id)

            if rest == "" and self.path.startswith("/intake/"):
                desk = IntakeDesk(case_dir)
                result = desk.receive(
                    body["disk_path"],
                    case_id=body.get("case_id", case_id),
                    source=body["source"],
                    generation=body["generation"],
                    signed_by=body["signed_by"],
                    collection_mode=body["collection_mode"],
                    investigator_id=body.get("investigator_id", "*"),
                    readable_blocks=body.get("readable_blocks"),
                    all_blocks=body.get("all_blocks", False),
                    page_ranges=body.get("page_ranges"),
                    permissions=body.get("permissions"),
                    notes=body.get("notes", ""),
                )
                _json_response(self, 201, result)
                return

            if rest == "amendments" and self.path.startswith("/intake/"):
                record = IntakeDesk(case_dir).amend_authorization(
                    target_investigator=body["target_investigator"],
                    authorized_by=body["authorized_by"],
                    reason=body["reason"],
                    replace_page_ranges=body.get("replace_page_ranges"),
                    add_permissions=body.get("add_permissions"),
                    revoke_permissions=body.get("revoke_permissions"),
                    readable_blocks_add=body.get("readable_blocks_add"),
                    all_blocks=body.get("all_blocks"),
                    raw_blocks_released=body.get("raw_blocks_released"),
                )
                _json_response(self, 201, record)
                return

            if rest == "requests" and self.path.startswith("/review/"):
                investigator = self.headers.get("X-Investigator") or body.get("investigator_id") or "anonymous"
                request = body.get("request", body)
                request.pop("investigator_id", None)
                desk = ReviewDesk(case_dir, investigator)
                _json_response(self, 200, desk.submit(request))
                return

            _json_response(self, 404, {"error": "unsupported POST route"})
        except KeyError as exc:
            _json_response(self, 400, {"error": f"missing field: {exc}"})
        except Exception as exc:  # noqa: BLE001 - HTTP boundary
            _json_response(self, 400, {"error": str(exc)})


def serve(cases_root: str | Path, host: str = "127.0.0.1", port: int = 8765) -> None:
    httpd = DeskHttpServer(cases_root, host, port)
    print(f"forensic desk HTTP API on http://{host}:{httpd.server_address[1]} (cases={cases_root})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--cases-root", default="runs/http-cases")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    serve(args.cases_root, args.host, args.port)
