from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .errors import StaleAdmissionError, StudioError
from .studio import FontStudio


def _query_int(query: dict[str, list[str]], key: str, default: int) -> int:
    if key not in query:
        return default
    return int(query[key][0])


def make_handler(studio: FontStudio) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "SubtitleStudio/1.0"

        def log_message(self, fmt: str, *args: Any) -> None:
            # Keep acceptance terminals clean; errors are returned as JSON.
            return

        def _json(self, status: int, payload: Any) -> None:
            body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self, max_bytes: int) -> Any:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                raise StudioError("request body must be JSON")
            if length > max_bytes:
                raise StudioError(f"request body exceeds {max_bytes} bytes")
            raw = self.rfile.read(length)
            return json.loads(raw.decode("utf-8"))

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path.strip("/")
            query = parse_qs(parsed.query)
            try:
                if path == "" or path == "api":
                    self._json(200, {
                        "service": "subtitle-studio",
                        "endpoints": [
                            "GET /api/packages",
                            "POST /api/packages/import",
                            "GET /api/packages/<id>/promises",
                            "GET /api/admissions/<id>/verify",
                            "POST /api/layouts/lock",
                            "GET /api/locks/<id>",
                            "GET /api/locks/<id>/canvas",
                        ],
                    })
                    return
                if path == "api/packages":
                    self._json(200, {"packages": studio.list_packages()})
                    return

                parts = path.split("/")
                if len(parts) == 4 and parts[:2] == ["api", "admissions"] and parts[3] == "verify":
                    self._json(200, studio.verify_admission(parts[2]))
                    return
                if len(parts) == 4 and parts[:2] == ["api", "packages"] and parts[3] == "promises":
                    package_id = parts[2]
                    result = studio.promised_chars(
                        package_id,
                        font_size=_query_int(query, "font_size", 5),
                        line_width=_query_int(query, "line_width", 16),
                        mode=query.get("mode", ["live"])[0],
                    )
                    self._json(200, result)
                    return

                if len(parts) == 4 and parts[:2] == ["api", "locks"] and parts[3] == "canvas":
                    readback = studio.read_canvas(parts[2])
                    self._json(200, readback.to_dict())
                    return
                if len(parts) == 3 and parts[:2] == ["api", "locks"]:
                    self._json(200, studio.get_lock(parts[2]))
                    return

                self._json(404, {"error": "not_found", "path": self.path})
            except StaleAdmissionError as exc:
                self._json(409, {"valid": False, "error": "stale_admission", "message": str(exc)})
            except (StudioError, KeyError, ValueError) as exc:
                self._json(400, {"error": type(exc).__name__, "message": str(exc)})

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path.strip("/")
            try:
                if path == "api/packages/import":
                    raw = self._read_json(studio.limits.max_package_bytes)
                    receipt = studio.import_dict(raw)
                    self._json(201, receipt)
                    return
                if path == "api/layouts/lock":
                    body = self._read_json(64_000)
                    if not isinstance(body, dict):
                        raise StudioError("lock body must be an object")
                    lock = studio.lock_layout(
                        body.get("text", ""),
                        package_id=body.get("package_id"),
                        font_size=int(body.get("font_size", 5)),
                        line_width=int(body.get("line_width", 16)),
                        mode=body.get("mode", "live"),
                        canvas_width=body.get("canvas_width"),
                        canvas_height=body.get("canvas_height"),
                        work_budget=body.get("work_budget"),
                        cell_budget=body.get("cell_budget"),
                        lock_id=body.get("lock_id"),
                    )
                    self._json(201, lock)
                    return
                self._json(404, {"error": "not_found", "path": self.path})
            except StaleAdmissionError as exc:
                self._json(409, {"valid": False, "error": "stale_admission", "message": str(exc)})
            except (StudioError, KeyError, ValueError, json.JSONDecodeError) as exc:
                self._json(400, {"error": type(exc).__name__, "message": str(exc)})

    return Handler


def run_server(host: str = "127.0.0.1", port: int = 8765, store_dir: str = ".subtitle_studio") -> None:
    studio = FontStudio(store_dir=store_dir)
    httpd = ThreadingHTTPServer((host, port), make_handler(studio))
    print(f"subtitle-studio API on http://{host}:{port} (store {store_dir})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
