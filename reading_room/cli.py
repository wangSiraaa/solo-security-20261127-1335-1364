"""Command-line reading-room: import, browse, print.

Subcommands
-----------
``import``   Seal a wire mailbox into a local edition, optionally fetching
             shared public illustrations from a running local supply station.
``browse``   Print the (offline) contents of a sealed letter.
``print``    Sign out and print entry guidance for a selected event.
``bases``    Show which delivered route bases can sign guidance for an event.

No ``browse``/``print``/``bases`` invocation contacts the network.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import routing
from .edition import load_edition, seal_issue
from .reader import (
    ReaderError,
    available_bases,
    render_guidance_html,
    render_guidance_text,
    render_letter_html,
    render_letter_text,
)
from .wire import load_mailbox


def _cmd_import(args) -> int:
    mailbox = load_mailbox(args.mailbox)
    edition = seal_issue(
        mailbox,
        args.edition,
        base_url=args.base_url,
        allow_network=bool(args.base_url),
    )
    summary = {
        "edition_id": edition.edition_id,
        "issue": edition.issue,
        "letters": [l.letter_id for l in edition.letters],
        "assets": len(edition.assets),
        "substitutions": edition.substitutions,
        "source": edition.source,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _cmd_browse(args) -> int:
    edition = load_edition(args.edition)
    letter = edition.letter(args.letter)
    if args.format == "html":
        out = render_letter_html(edition, letter, selected_event=args.event)
    else:
        out = render_letter_text(edition, letter, selected_event=args.event)
    _emit(out, args.output)
    return 0


def _cmd_print(args) -> int:
    edition = load_edition(args.edition)
    letter = edition.letter(args.letter)
    try:
        if args.format == "html":
            out = render_guidance_html(
                edition, letter, args.event, mode=args.mode, basis=args.basis
            )
        else:
            out = render_guidance_text(
                edition, letter, args.event, mode=args.mode, basis=args.basis
            )
    except (routing.RouteError, ReaderError, KeyError) as exc:
        print(f"无法签出指引：{exc}", file=sys.stderr)
        return 2
    _emit(out, args.output)
    return 0


def _cmd_bases(args) -> int:
    edition = load_edition(args.edition)
    letter = edition.letter(args.letter)
    for mode in routing.MODES:
        bases = available_bases(edition, letter, args.event, mode)
        print(f"{mode}: {', '.join(bases) or '（无可用依据）'}")
    return 0


def _emit(text: str, output: str | None) -> None:
    if output:
        Path(output).write_text(text, encoding="utf-8")
        print(f"写入 {output}")
    else:
        print(text)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="reading-room", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    pi = sub.add_parser("import", help="封存一期刊物为本地版")
    pi.add_argument("--mailbox", required=True, help="wire 邮箱 JSON")
    pi.add_argument("--edition", required=True, help="本地版目录")
    pi.add_argument("--base-url", default=None,
                    help="本地供图站基址；省略则完全离线（公共图记为替代）")
    pi.set_defaults(func=_cmd_import)

    pb = sub.add_parser("browse", help="翻阅已封存信件（离线）")
    pb.add_argument("--edition", required=True)
    pb.add_argument("--letter", required=True)
    pb.add_argument("--event", default=None)
    pb.add_argument("--format", choices=["text", "html"], default="text")
    pb.add_argument("--output", default=None)
    pb.set_defaults(func=_cmd_browse)

    pp = sub.add_parser("print", help="签出入场指引（离线）")
    pp.add_argument("--edition", required=True)
    pp.add_argument("--letter", required=True)
    pp.add_argument("--event", required=True, help="活动号")
    pp.add_argument("--mode", choices=list(routing.MODES), default=routing.STANDARD)
    pp.add_argument("--basis", default="auto",
                    choices=["auto", routing.LETTER_MAP, routing.LETTER_TEXT,
                             routing.COMMON_MAP])
    pp.add_argument("--format", choices=["text", "html"], default="text")
    pp.add_argument("--output", default=None)
    pp.set_defaults(func=_cmd_print)

    pbs = sub.add_parser("bases", help="列出某活动可用的路线依据")
    pbs.add_argument("--edition", required=True)
    pbs.add_argument("--letter", required=True)
    pbs.add_argument("--event", required=True)
    pbs.set_defaults(func=_cmd_bases)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
