from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .cabinet import append_event, query_word


def make_handler(state_path: Path, cabinet_id: str):
    class CabinetHandler(BaseHTTPRequestHandler):
        def _send(self, status: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - stdlib hook
            parsed = urlparse(self.path)
            if parsed.path == "/health":
                self._send(200, {"ok": True, "cabinet_id": cabinet_id})
                return
            if parsed.path == "/query":
                word = parse_qs(parsed.query).get("word", [None])[0]
                events = query_word(state_path, word)
                self._send(200, {"cabinet_id": cabinet_id, "count": len(events), "events": events})
                return
            self._send(404, {"ok": False, "error": "not found"})

        def do_POST(self) -> None:  # noqa: N802 - stdlib hook
            parsed = urlparse(self.path)
            if parsed.path != "/collect":
                self._send(404, {"ok": False, "error": "not found"})
                return
            length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                word = str(payload["word"])
                source = str(payload.get("source", "unknown"))
            except Exception as exc:
                self._send(400, {"ok": False, "error": f"invalid event: {exc}"})
                return
            event = append_event(
                state_path,
                cabinet_id=cabinet_id,
                word=word,
                source=source,
                extra=payload.get("extra"),
            )
            self._send(202, {"ok": True, "received": event})

        def log_message(self, fmt: str, *args) -> None:
            # The cabinet state is the audit record; keep demo output quiet.
            return

    return CabinetHandler


def serve(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mock external lookup/receiving cabinet")
    parser.add_argument("--state", required=True)
    parser.add_argument("--cabinet-id", default="mock-cabinet-A")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(Path(args.state), args.cabinet_id))
    print(json.dumps({"ok": True, "host": args.host, "port": args.port, "state": args.state}, sort_keys=True))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(serve())
