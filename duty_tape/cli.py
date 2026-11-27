"""Command line interface for issuing, recovering, and querying duty tapes."""

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List

from .protocol import ProtocolError, build_artifact, dependency_pin, seal_artifact, write_tape
from .query import QueryEndpoint, QueryError
from .receiver import RecoveryError, RecoveryTerminal
from .sealer import ReceiptError, TestSealer, new_revocation


def _print_json(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True))


def _sealer(args: argparse.Namespace) -> TestSealer:
    key_file = getattr(args, "seal_key_file", None)
    return TestSealer.from_key_file(key_file)


def _parse_table_spec(spec: str | None) -> tuple[str, int] | None:
    if spec is None:
        return None
    path, _, gen_s = spec.rpartition(":")
    if not path or not gen_s.isdigit():
        raise SystemExit(f"bad table spec {spec!r}; expected PATH:GENERATION")
    return path, int(gen_s)


def cmd_init(args: argparse.Namespace) -> int:
    terminal = RecoveryTerminal(args.root, sealer=_sealer(args))
    _print_json({"initialized": str(terminal.root), "current": str(terminal.current)})
    return 0


def cmd_issue(args: argparse.Namespace) -> int:
    sealer = _sealer(args)
    now = args.now or "2026-01-01T00:00:00+00:00"
    artifacts: List[Dict[str, Any]] = []

    contacts_spec = _parse_table_spec(args.contacts)
    lockers_spec = _parse_table_spec(args.lockers)
    duty_spec = _parse_table_spec(args.duty)

    contacts = None
    lockers = None
    duty = None

    if contacts_spec:
        path, gen = contacts_spec
        contacts = build_artifact(
            batch=args.batch,
            kind="contacts",
            generation=gen,
            body=Path(path).read_text(encoding="utf-8"),
            issued_at=now,
        )
        seal_artifact(contacts, sealer)
        artifacts.append(contacts)

    if lockers_spec:
        path, gen = lockers_spec
        lockers = build_artifact(
            batch=args.batch,
            kind="lockers",
            generation=gen,
            body=Path(path).read_text(encoding="utf-8"),
            issued_at=now,
        )
        seal_artifact(lockers, sealer)
        artifacts.append(lockers)

    if duty_spec:
        if lockers is None:
            raise SystemExit("issuing duty requires --lockers in the same issuance")
        path, gen = duty_spec
        duty = build_artifact(
            batch=args.batch,
            kind="duty",
            generation=gen,
            body=Path(path).read_text(encoding="utf-8"),
            issued_at=now,
            dependencies=[dependency_pin(lockers)],
        )
        seal_artifact(duty, sealer)
        # Desired tape order: independently useful contacts first, then the
        # duty dependency and duty itself.
        artifacts.append(duty)

    if args.order:
        rank = {kind: i for i, kind in enumerate(args.order.split(","))}
        artifacts.sort(key=lambda a: rank.get(a["header"]["kind"], 99))

    write_tape(args.out, artifacts)
    _print_json(
        {
            "tape": args.out,
            "batch": args.batch,
            "artifacts": [
                {
                    "kind": a["header"]["kind"],
                    "generation": a["header"]["generation"],
                    "artifact_id": a["artifact_id"],
                    "digest": a["digest"],
                }
                for a in artifacts
            ],
        }
    )
    return 0


def cmd_feed(args: argparse.Namespace) -> int:
    terminal = RecoveryTerminal(args.root, sealer=_sealer(args))
    result = terminal.feed_file(
        args.tape,
        expected_batch=args.expected_batch,
        strict=False,
    )
    _print_json(result)
    return 2 if result["rejected"] and not args.allow_rejected else 0


def cmd_status(args: argparse.Namespace) -> int:
    terminal = RecoveryTerminal(args.root, sealer=_sealer(args))
    _print_json(terminal.status())
    return 0


def cmd_activate(args: argparse.Namespace) -> int:
    terminal = RecoveryTerminal(args.root, sealer=_sealer(args))
    _print_json(terminal.activate(reason=args.reason))
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    endpoint = QueryEndpoint(
        args.root,
        terminal=RecoveryTerminal(args.root, sealer=_sealer(args)),
    )
    if args.query_kind == "abbreviation":
        result: Any = endpoint.abbreviation(args.identifier)
    elif args.query_kind == "locker":
        result = endpoint.locker_number(args.identifier)
    elif args.query_kind == "duty":
        result = endpoint.duty_roster(args.date)
    elif args.query_kind == "person":
        result = endpoint.person(args.identifier, args.date)
    else:  # pragma: no cover - argparse enforces this
        raise SystemExit("unknown query")
    _print_json(result)
    return 0


def cmd_pin(args: argparse.Namespace) -> int:
    terminal = RecoveryTerminal(args.root, sealer=_sealer(args))
    pins = terminal.pin_kind(args.kind, args.digest)
    _print_json(pins)
    return 0


def cmd_unpin(args: argparse.Namespace) -> int:
    terminal = RecoveryTerminal(args.root, sealer=_sealer(args))
    terminal.unpin_kind(args.kind)
    _print_json(terminal.pins())
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    terminal = RecoveryTerminal(args.root, sealer=_sealer(args))
    _print_json(terminal.export_active(args.kind, args.to, copied_by=args.copied_by))
    return 0


def cmd_notice_copy(args: argparse.Namespace) -> int:
    terminal = RecoveryTerminal(args.root, sealer=_sealer(args))
    event = terminal.notice_external_copy(
        args.kind,
        args.location,
        digest=args.digest,
        copied_by=args.copied_by,
    )
    _print_json(event)
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    terminal = RecoveryTerminal(args.root, sealer=_sealer(args))
    if args.verify:
        _print_json(terminal.verify_history())
    else:
        _print_json(terminal.history(args.event))
    return 0


def cmd_tail_failed(args: argparse.Namespace) -> int:
    terminal = RecoveryTerminal(args.root, sealer=_sealer(args))
    _print_json(terminal.tail_failed(args.note))
    return 0


def cmd_clear_tmp(args: argparse.Namespace) -> int:
    terminal = RecoveryTerminal(args.root, sealer=_sealer(args))
    _print_json(terminal.clear_temporary_area())
    return 0


def cmd_revocation_new(args: argparse.Namespace) -> int:
    sealer = _sealer(args)
    rev = new_revocation(
        sealer,
        artifact_digest=args.artifact_digest,
        batch=args.batch,
        reason=args.reason,
        issued_at=args.now or "2026-01-01T00:00:00+00:00",
    )
    Path(args.out).write_text(json.dumps(rev, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _print_json({"wrote": args.out, "receipt": rev})
    return 0


def cmd_revoke(args: argparse.Namespace) -> int:
    terminal = RecoveryTerminal(args.root, sealer=_sealer(args))
    rev = json.loads(Path(args.file).read_text(encoding="utf-8"))
    _print_json(terminal.revoke(rev))
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    endpoint = QueryEndpoint(
        args.root,
        terminal=RecoveryTerminal(args.root, sealer=_sealer(args)),
    )

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, payload: Any) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - stdlib name
            from urllib.parse import parse_qs, urlparse

            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            try:
                if parsed.path == "/health":
                    self._send(200, {"ok": True})
                elif parsed.path == "/v1/abbreviation":
                    self._send(200, endpoint.abbreviation(qs["name"][0]))
                elif parsed.path == "/v1/locker":
                    self._send(200, endpoint.locker_number(qs["id"][0]))
                elif parsed.path == "/v1/duty":
                    self._send(200, endpoint.duty_roster(qs.get("date", [None])[0]))
                elif parsed.path == "/v1/person":
                    self._send(
                        200,
                        endpoint.person(qs["name"][0], qs.get("date", [None])[0]),
                    )
                else:
                    self._send(404, {"error": "not found"})
            except (KeyError, QueryError) as e:
                self._send(400, {"error": str(e)})
            except Exception as e:  # pragma: no cover - server boundary
                self._send(500, {"error": str(e)})

        def log_message(self, fmt: str, *fmt_args: Any) -> None:
            # The JSON CLI already prints the listening URL; keep tests quiet.
            return

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    _print_json({"serving": f"http://{args.host}:{args.port}", "root": args.root})
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="duty-tape", description=__doc__)
    p.add_argument("--root", default="./duty-terminal-state", help="terminal state directory")
    p.add_argument("--seal-key-file", default=None, help="HMAC key; defaults to public test key")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("init")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("issue", help="build and seal a tape from CSV tables")
    sp.add_argument("--batch", required=True)
    sp.add_argument("--contacts")
    sp.add_argument("--lockers")
    sp.add_argument("--duty")
    sp.add_argument("--out", required=True)
    sp.add_argument("--now")
    sp.add_argument("--order", default="contacts,lockers,duty")
    sp.set_defaults(func=cmd_issue)

    sp = sub.add_parser("feed", help="feed one tape segment or whole tape")
    sp.add_argument("--tape", required=True)
    sp.add_argument("--expected-batch")
    sp.add_argument("--allow-rejected", action="store_true")
    sp.set_defaults(func=cmd_feed)

    sp = sub.add_parser("status")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("activate", help="reconcile/mount the safest complete group")
    sp.add_argument("--reason", default="operator_activate")
    sp.set_defaults(func=cmd_activate)

    q = sub.add_parser("query")
    qsub = q.add_subparsers(dest="query_kind", required=True)
    qa = qsub.add_parser("abbreviation")
    qa.add_argument("identifier")
    ql = qsub.add_parser("locker")
    ql.add_argument("identifier")
    qd = qsub.add_parser("duty")
    qd.add_argument("date", nargs="?")
    qp = qsub.add_parser("person")
    qp.add_argument("identifier")
    qp.add_argument("--date")
    q.set_defaults(func=cmd_query)

    sp = sub.add_parser("pin")
    sp.add_argument("--kind", default="lockers")
    sp.add_argument("--digest", required=True)
    sp.set_defaults(func=cmd_pin)

    sp = sub.add_parser("unpin")
    sp.add_argument("--kind", default="lockers")
    sp.set_defaults(func=cmd_unpin)

    sp = sub.add_parser("export")
    sp.add_argument("--kind", required=True)
    sp.add_argument("--to", required=True)
    sp.add_argument("--copied-by", default="other-duty-desk")
    sp.set_defaults(func=cmd_export)

    sp = sub.add_parser("notice-copy")
    sp.add_argument("--kind", required=True)
    sp.add_argument("--location", required=True)
    sp.add_argument("--digest")
    sp.add_argument("--copied-by", default="other-duty-desk")
    sp.set_defaults(func=cmd_notice_copy)

    sp = sub.add_parser("history")
    sp.add_argument("--event", action="append")
    sp.add_argument("--verify", action="store_true", help="verify hash-chain integrity")
    sp.set_defaults(func=cmd_history)

    sp = sub.add_parser("tail-failed")
    sp.add_argument("--note", default="tail of recovery tape was unreadable")
    sp.set_defaults(func=cmd_tail_failed)

    sp = sub.add_parser("clear-tmp")
    sp.set_defaults(func=cmd_clear_tmp)

    sp = sub.add_parser("new-revocation")
    sp.add_argument("--out", required=True)
    g = sp.add_mutually_exclusive_group(required=True)
    g.add_argument("--artifact-digest")
    g.add_argument("--batch")
    sp.add_argument("--reason", required=True)
    sp.add_argument("--now")
    sp.set_defaults(func=cmd_revocation_new)

    sp = sub.add_parser("revoke")
    sp.add_argument("--file", required=True)
    sp.set_defaults(func=cmd_revoke)

    sp = sub.add_parser("serve")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8765)
    sp.set_defaults(func=cmd_serve)

    return p


def main(argv: List[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (RecoveryError, QueryError, ReceiptError, ProtocolError, OSError) as e:
        _print_json({"error": str(e)})
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
