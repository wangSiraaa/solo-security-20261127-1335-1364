"""A local stand-in for the image supply station.

It serves image bytes from a directory on disk and appends every request it
receives to a log file.  The station **cannot see** anything except the
requests that reach it: no reader identity, no mailbox, no session.  All it
knows is "someone asked for this path at this time".

This is what makes the privacy claim testable: after a reader browses,
re-reads, switches, prints and closes letters, the log must be byte-for-byte
the same as the log produced by any other reader of the same issue.
"""

from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from .assets import PUBLIC_PREFIX, SUBSCRIBER_PREFIX, UNKNOWN


class RequestLog:
    """Append-only record of what the supply station was asked for.

    The station records requests only.  It never receives a subscriber name,
    a reader id, or any header beyond the request line, so it cannot tell
    "which reader" fetched a shared illustration.
    """

    def __init__(self, path):
        self.path = os.fspath(path)
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        open(self.path, "a", encoding="utf-8").close()

    def record(self, method: str, path: str, status: int) -> dict:
        entry = {
            "method": method,
            "path": path,
            "status": status,
            "at": time.time(),
        }
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, sort_keys=True) + "\n")
        return entry

    def entries(self) -> list[dict]:
        out = []
        if not os.path.exists(self.path):
            return out
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    def paths(self) -> list[str]:
        return [e["path"] for e in self.entries()]

    def clear(self) -> None:
        with self._lock:
            open(self.path, "w", encoding="utf-8").close()


class AssetStore:
    """Filesystem-backed image store keyed by request path.

    Byte content is looked up by the *normalised* request path.  Two distinct
    paths may hold identical bytes (the point of the "same look, different
    owner" test); ownership lives in the path, not in the picture.
    """

    def __init__(self, root):
        self.root = os.path.abspath(os.fspath(root))

    def _disk_path(self, url_path: str) -> str:
        rel = url_path.lstrip("/")
        candidate = os.path.abspath(os.path.join(self.root, rel))
        if not (candidate == self.root or candidate.startswith(self.root + os.sep)):
            raise ValueError("path escapes asset root")
        return candidate

    def has(self, url_path: str) -> bool:
        try:
            return os.path.isfile(self._disk_path(url_path))
        except ValueError:
            return False

    def read(self, url_path: str) -> bytes:
        with open(self._disk_path(url_path), "rb") as fh:
            return fh.read()

    def write(self, url_path: str, data: bytes) -> None:
        disk = self._disk_path(url_path)
        os.makedirs(os.path.dirname(disk), exist_ok=True)
        with open(disk, "wb") as fh:
            fh.write(data)

    def list_paths(self) -> list[str]:
        found = []
        for dirpath, _dirs, files in os.walk(self.root):
            for name in files:
                full = os.path.join(dirpath, name)
                rel = "/" + os.path.relpath(full, self.root).replace(os.sep, "/")
                found.append(rel)
        return sorted(found)


def _make_handler(store: AssetStore, log: RequestLog):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ReadingRoomSupply/1.0"

        def log_message(self, *args):  # keep stderr quiet; the log file is truth
            return

        def _handle(self):
            parsed = urlsplit(self.path)
            url_path = unquote(parsed.path)
            if self.command != "GET":
                log.record(self.command, url_path, 405)
                self.send_error(405)
                return
            # The station records every request, whatever its namespace.
            if not store.has(url_path):
                log.record("GET", url_path, 404)
                self.send_error(404, "not found")
                return
            try:
                data = store.read(url_path)
            except (OSError, ValueError):
                log.record("GET", url_path, 500)
                self.send_error(500)
                return
            log.record("GET", url_path, 200)
            self.send_response(200)
            ctype = "image/png"
            if url_path.endswith(".jpg") or url_path.endswith(".jpeg"):
                ctype = "image/jpeg"
            elif url_path.endswith(".gif"):
                ctype = "image/gif"
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = _handle
        do_POST = _handle
        do_HEAD = _handle

    return Handler


class SupplyStation:
    """Context-managed HTTP server wrapping an :class:`AssetStore`."""

    def __init__(self, root, log_path, host="127.0.0.1", port=0):
        self.store = AssetStore(root)
        self.log = RequestLog(log_path)
        self.host = host
        self.port = port
        self._httpd = None
        self._thread = None

    @property
    def base_url(self) -> str:
        if self._httpd is None:
            raise RuntimeError("server is not running")
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}"

    def start(self) -> "SupplyStation":
        handler = _make_handler(self.store, self.log)
        self._httpd = ThreadingHTTPServer((self.host, self.port), handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()
        return False


def classify_path(url_path: str) -> str:
    """Public classification of a path, mirroring ``assets.classify_url``."""
    if url_path.startswith(PUBLIC_PREFIX):
        return "public"
    if url_path.startswith(SUBSCRIBER_PREFIX):
        return "subscriber"
    return UNKNOWN
