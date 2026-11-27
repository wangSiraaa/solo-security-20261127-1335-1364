"""Command-line access layer. Every invocation is a fresh OS process."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, Tuple

from . import __version__, service
from .channels import perform_roundtrip
from .errors import StationError, ValidationError
from .store import Store


def _add_state(sp) -> None:
    sp.add_argument("--state", required=True, help="local state directory")


def _bearer_from_file(path: str | None, field: str) -> Tuple[str | None, Dict[str, Any]]:
    if not path:
        return None, {}
    file_path = Path(path)
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValidationError(f"credential file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValidationError(f"credential file is not JSON: {exc}") from exc
    token = payload.get(field)
    if not token:
        raise ValidationError(f"credential file has no {field!r} field", path=path)
    extra: Dict[str, Any] = {}
    if field == "voucher" and payload.get("upload_key"):
        extra["upload_key"] = payload["upload_key"]
    return token, extra


def _resolve_credentials(args) -> Dict[str, Any]:
    ticket = getattr(args, "ticket", None)
    ticket_file = getattr(args, "ticket_file", None)
    if ticket_file:
        ticket, _ = _bearer_from_file(ticket_file, "ticket")

    voucher = getattr(args, "voucher", None)
    voucher_file = getattr(args, "voucher_file", None)
    file_extra: Dict[str, Any] = {}
    if voucher_file:
        voucher, file_extra = _bearer_from_file(voucher_file, "voucher")

    upload_key = getattr(args, "upload_key", None) or file_extra.get("upload_key")
    return {"ticket": ticket, "voucher": voucher, "upload_key": upload_key}


def build_parser():
    parser = __import__("argparse").ArgumentParser(
        prog="anon_station",
        description="Anonymous image-report access layer / durable staging cabinet demo",
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="initialize local gateway/cabinet state")
    _add_state(init)
    init.add_argument("--slots", type=int, default=1)
    init.add_argument("--policy", choices=["reservation", "onwrite"], default="reservation")
    init.add_argument("--reservation-ttl", type=int, default=3600, dest="reservation_ttl")
    init.add_argument("--partial-ttl", type=int, default=86400, dest="partial_ttl")
    init.add_argument("--witness-ttl", type=int, default=600, dest="witness_ttl")
    init.add_argument("--force", action="store_true")

    rt = sub.add_parser("roundtrip", help="perform a local simulated channel round trip")
    _add_state(rt)
    rt.add_argument("--channel", required=True)
    rt.add_argument("--out", required=True)

    ask = sub.add_parser("ask", help="first question: may I try to send?")
    _add_state(ask)
    ask.add_argument("--witness", required=True)
    ask.add_argument("--segments", type=int, default=3)
    ask.add_argument("--egress", default=None, help="display-only egress/exit label")
    ask.add_argument("--ticket-out", dest="ticket_out")
    ask.add_argument("--voucher-out", dest="voucher_out")
    ask.add_argument("--upload-key", dest="upload_key")

    migrate = sub.add_parser("migrate", help="migrate a reservation to a newly witnessed channel")
    _add_state(migrate)
    migrate.add_argument("--ticket")
    migrate.add_argument("--ticket-file", dest="ticket_file")
    migrate.add_argument("--witness", required=True)
    migrate.add_argument("--egress")

    write = sub.add_parser("write", help="write one ordered body segment")
    _add_state(write)
    write.add_argument("--ticket")
    write.add_argument("--ticket-file", dest="ticket_file")
    write.add_argument("--voucher")
    write.add_argument("--voucher-file", dest="voucher_file")
    write.add_argument("--upload-key", dest="upload_key")
    write.add_argument("--witness", required=True)
    write.add_argument("--index", required=True, type=int)
    data_group = write.add_mutually_exclusive_group(required=True)
    data_group.add_argument("--data")
    data_group.add_argument("--data-file", dest="data_file")
    write.add_argument("--egress")
    write.add_argument("--idempotency-key", dest="idempotency_key")
    write.add_argument("--ticket-out", dest="ticket_out")

    deliver = sub.add_parser("deliver", help="check all segments and record final sign-off")
    _add_state(deliver)
    deliver.add_argument("--ticket")
    deliver.add_argument("--ticket-file", dest="ticket_file")
    deliver.add_argument("--signoff", required=True)
    deliver.add_argument("--out")

    clear = sub.add_parser("clear-slot", help="physically clear a delivered/expired/abandoned slot")
    _add_state(clear)
    clear.add_argument("--slot", required=True, type=int)
    clear.add_argument("--operator", default="operator")

    reap = sub.add_parser("reap", help="release reservations/half items past their explicit TTL")
    _add_state(reap)

    for name in ("inspect", "status"):
        inspect = sub.add_parser(name, help="inspect slots and orders")
        _add_state(inspect)
        inspect.add_argument("--json", action="store_true")
        inspect.add_argument("--slot", type=int)

    return parser


def _print_json(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False))


def format_inspect(payload: Dict[str, Any]) -> str:
    lines = [
        f"state={payload['state_directory']} policy={payload['policy']} "
        f"slots={payload['slots_count']} time={payload['now_iso']}",
        f"counts={json.dumps(payload['counts'], ensure_ascii=False, sort_keys=True)}",
        "",
    ]
    for slot in payload["slots"]:
        if slot["state"] == "empty":
            lines.append(f"[{slot['slot']}] 空格 EMPTY")
            continue
        lines.append(
            f"[{slot['slot']}] {slot['state'].upper()} — {slot['label']} "
            f"order={slot['order_id']} channel={slot.get('bound_channel')}"
        )
        if slot["state"] in {"reserved", "writing", "complete"}:
            lines.append(
                f"      segments={slot['segments_received']}/{slot['segments_total']} "
                f"received={slot['received_indexes']} missing={slot['missing_indexes']}"
            )
            if slot.get("reservation_expires_at"):
                lines.append(f"      reservation_hold_until_epoch={slot['reservation_expires_at']}")
            if slot.get("partial_expires_at"):
                lines.append(f"      half_upload_deadline_epoch={slot['partial_expires_at']}")
        if slot["state"] == "delivered":
            lines.append(
                f"      delivered_at_epoch={slot['delivered_at']} signoff={slot.get('signoff')!r} "
                f"sha256={slot.get('package_sha256')}"
            )
            lines.append(f"      artifact={slot.get('artifact_path')}")
            lines.append(
                "      hold_released=true reusable_for_new_order=true "
                f"clear_command_available={slot['releasable_by_clear_command']}"
            )
        else:
            lines.append(
                f"      hold_released=false clear_command_available="
                f"{slot['releasable_by_clear_command']}"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "init":
            Store.initialize(
                args.state,
                slots=args.slots,
                policy=args.policy,
                reservation_ttl_seconds=args.reservation_ttl,
                partial_ttl_seconds=args.partial_ttl,
                witness_ttl_seconds=args.witness_ttl,
                force=args.force,
            )
            result = {
                "ok": True,
                "command": "init",
                "state": str(Path(args.state)),
                "slots": args.slots,
                "policy": args.policy,
            }
            _print_json(result)
            return 0

        store = Store(args.state)

        if args.command == "roundtrip":
            result = perform_roundtrip(store, args.channel, args.out)
        elif args.command == "ask":
            result = service.ask(
                store,
                witness_path=args.witness,
                segments_total=args.segments,
                egress=args.egress,
                ticket_out=args.ticket_out,
                voucher_out=args.voucher_out,
                upload_key=args.upload_key,
            )
        elif args.command == "migrate":
            creds = _resolve_credentials(args)
            if not creds["ticket"]:
                raise ValidationError("a ticket is required for migration")
            result = service.migrate_line(store, creds["ticket"], args.witness, args.egress)
        elif args.command == "write":
            creds = _resolve_credentials(args)
            result = service.write_segment(
                store,
                index=args.index,
                data=args.data,
                data_file=args.data_file,
                witness_path=args.witness,
                ticket=creds["ticket"],
                voucher=creds["voucher"],
                upload_key=creds["upload_key"],
                egress=args.egress,
                idempotency_key=args.idempotency_key,
                ticket_out=args.ticket_out,
            )
        elif args.command == "deliver":
            creds = _resolve_credentials(args)
            if not creds["ticket"]:
                raise ValidationError("a ticket is required for delivery")
            result = service.deliver_item(store, creds["ticket"], args.signoff, args.out)
        elif args.command == "clear-slot":
            result = service.clear_slot(store, args.slot, args.operator)
        elif args.command == "reap":
            result = service.reap_now(store)
        elif args.command in {"inspect", "status"}:
            result = service.inspect_cabinet(store, args.slot)
            if args.json:
                _print_json(result)
            else:
                print(format_inspect(result))
            return 0
        else:  # pragma: no cover - argparse enforces this
            raise ValidationError(f"unknown command {args.command}")

        _print_json(result)
        return 0

    except StationError as exc:
        _print_json(exc.to_dict())
        return exc.exit_code
    except Exception as exc:  # noqa: BLE001 - CLI boundary should emit JSON
        payload = {"ok": False, "code": "INTERNAL_ERROR", "error": str(exc)}
        _print_json(payload)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
