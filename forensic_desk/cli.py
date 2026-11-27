"""Command line for the intake and review desks."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .fixtures import write_all_fixtures
from .intake import IntakeDesk
from .review import ReviewDesk
from .station import verify_station_bundle


def print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _split_csv(value: Optional[str]) -> Optional[List[str]]:
    if value is None:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="forensic-desk", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-fixtures", help="write fictional disks").add_argument("--out", default="disks")

    p = sub.add_parser("intake", help="receive a disk and create the single handoff record")
    p.add_argument("--case-dir", required=True)
    p.add_argument("--disk", required=True)
    p.add_argument("--case-id", required=True)
    p.add_argument("--source", required=True)
    p.add_argument("--generation", required=True)
    p.add_argument("--signed-by", required=True)
    p.add_argument("--mode", choices=["write_protected_station", "custodian_cow"], required=True)
    p.add_argument("--investigator", default="*")
    p.add_argument("--blocks", help="comma-separated IDs, including DIRECTORY/JOURNAL/LAYOUT")
    p.add_argument("--all-blocks", action="store_true")
    p.add_argument("--pages", help="comma-separated page tokens/ranges")
    p.add_argument("--permission", action="append", default=[])
    p.add_argument("--note", default="")

    p = sub.add_parser("amend", help="append a narrow authorization amendment")
    p.add_argument("--case-dir", required=True)
    p.add_argument("--target", required=True)
    p.add_argument("--by", required=True)
    p.add_argument("--reason", required=True)
    p.add_argument("--pages", help="replacement page scope")
    p.add_argument("--add-permission", action="append", default=[])
    p.add_argument("--block", action="append", default=[])

    def review_cmd(name: str, help_text: str) -> argparse.ArgumentParser:
        rp = sub.add_parser(name, help=help_text)
        rp.add_argument("--case-dir", required=True)
        rp.add_argument("--as", dest="investigator", required=True)
        return rp

    rp = review_cmd("raw-block", "request a raw block observation")
    rp.add_argument("block_id")

    rp = review_cmd("page", "request one page under current page scope")
    rp.add_argument("page_id")

    rp = review_cmd("entry", "request physical/replayed/work entry provenance")
    rp.add_argument("--name")
    rp.add_argument("--entry-id")

    rp = review_cmd("absence", "request a scoped absence conclusion")
    rp.add_argument("name")

    rp = review_cmd("replay", "replay original journal without writing")

    rp = review_cmd("open-legacy", "run old viewer in COW or test station direct open")
    rp.add_argument("--target", choices=["cow", "write_protected_station"], default="cow")

    rp = review_cmd("rename", "investigator rename in private COW")
    rp.add_argument("entry_id")
    rp.add_argument("new_name")

    rp = review_cmd("write-line", "investigator write in private COW")
    rp.add_argument("entry_id")
    rp.add_argument("name")
    rp.add_argument("--block-id", default="WORK")
    rp.add_argument("--pages", default="")

    rp = review_cmd("confirm-write", "append investigator-first-write provenance")
    rp.add_argument("entry_id")

    rp = review_cmd("provenance", "review provenance graph for a record/URN")
    rp.add_argument("reference")

    rp = sub.add_parser("record", help="fetch one immutable ledger record")
    rp.add_argument("--case-dir", required=True)
    rp.add_argument("record_id")

    rp = sub.add_parser("verify-ledger", help="verify hash chain")
    rp.add_argument("--case-dir", required=True)

    rp = sub.add_parser("verify-station", help="third-party verify exported station directory")
    rp.add_argument("bundle_dir")

    sp = sub.add_parser("serve", help="run optional local JSON HTTP API")
    sp.add_argument("--cases-root", default="runs/http-cases")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8765)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "init-fixtures":
        paths = write_all_fixtures(args.out)
        print_json({k: str(v) for k, v in paths.items()})
        return 0

    if args.command == "serve":
        from .server import serve

        serve(args.cases_root, host=args.host, port=args.port)
        return 0

    if args.command == "intake":
        desk = IntakeDesk(args.case_dir)
        result = desk.receive(
            args.disk,
            case_id=args.case_id,
            source=args.source,
            generation=args.generation,
            signed_by=args.signed_by,
            collection_mode=args.mode,
            investigator_id=args.investigator,
            readable_blocks=_split_csv(args.blocks),
            all_blocks=args.all_blocks,
            page_ranges=_split_csv(args.pages),
            permissions=args.permission or None,
            notes=args.note,
        )
        print_json(result)
        return 0

    if args.command == "amend":
        desk = IntakeDesk(args.case_dir)
        record = desk.amend_authorization(
            target_investigator=args.target,
            authorized_by=args.by,
            reason=args.reason,
            replace_page_ranges=_split_csv(args.pages),
            add_permissions=args.add_permission,
            readable_blocks_add=args.block,
        )
        print_json(record)
        return 0

    if args.command == "record":
        print_json(LedgerRecordShim(args.case_dir).get(args.record_id))
        return 0

    if args.command == "verify-ledger":
        from .ledger import Ledger

        print_json(Ledger(args.case_dir).verify_chain())
        return 0

    if args.command == "verify-station":
        print_json(verify_station_bundle(args.bundle_dir))
        return 0

    # Commands requiring a review desk.
    desk = ReviewDesk(args.case_dir, args.investigator)
    if args.command == "raw-block":
        response = desk.request_raw_block(args.block_id)
    elif args.command == "page":
        response = desk.request_page(args.page_id)
    elif args.command == "entry":
        response = desk.request_entry(name=args.name, entry_id=args.entry_id)
    elif args.command == "absence":
        response = desk.request_absence(args.name)
    elif args.command == "replay":
        response = desk.submit({"kind": "replay"})
    elif args.command == "open-legacy":
        response = desk.open_legacy(args.target)
    elif args.command == "rename":
        response = desk.rename_entry(args.entry_id, args.new_name)
    elif args.command == "write-line":
        pages = _split_csv(args.pages) or []
        response = desk.write_line(args.entry_id, args.name, pages=pages)
    elif args.command == "confirm-write":
        response = desk.confirm_investigator_first_write(args.entry_id)
    elif args.command == "provenance":
        print_json(desk.review_provenance(args.reference))
        return 0
    else:  # pragma: no cover - argparse enforces commands
        raise AssertionError(args.command)

    print_json(response)
    return 0


class LedgerRecordShim:
    def __init__(self, case_dir: str):
        from .ledger import Ledger

        self.ledger = Ledger(case_dir)

    def get(self, record_id: str) -> Dict[str, Any]:
        return self.ledger.require(record_id)


if __name__ == "__main__":
    sys.exit(main())
