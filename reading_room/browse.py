"""Build a static, fully-offline HTML reading bundle from a sealed edition.

The bundle is a directory of plain files: an ``index.html`` listing every
letter, one page per letter, and one printable guidance page per event (in
both a general and a step-free variant).  All links are relative and every
image is a local file, so the whole thing can be handed to a reader on a USB
stick and opened with no server and no network.

Usage:  python3 -m reading_room.browse --edition <dir> --out <html-dir>
"""

from __future__ import annotations

import html
from pathlib import Path

from . import routing
from .edition import Edition, load_edition
from .reader import render_guidance_html, render_letter_html

_STYLE = """
body{font-family:serif;max-width:860px;margin:1.5em auto;padding:0 1em;line-height:1.55}
nav{background:#f4f4f4;padding:.6em 1em;border-radius:6px;margin-bottom:1.5em}
nav a{margin-right:1em}
.card{border:1px solid #ddd;border-radius:8px;padding:1em;margin:1em 0}
.ev{display:inline-block;margin:.3em .6em .3em 0}
.btn{display:inline-block;padding:.3em .7em;border:1px solid #06c;border-radius:5px;
color:#06c;text-decoration:none;margin-right:.4em}
.substitute{padding:.7em;border:2px dashed #b00;background:#fff5f5;color:#b00;
font-weight:bold;border-radius:5px}
.small{color:#666;font-size:.9em}
h1{font-size:1.4em}h2{font-size:1.15em}
"""


def _page(title: str, body: str, nav: str = "") -> str:
    return (
        "<!doctype html><html lang='zh'><head><meta charset='utf-8'>"
        f"<title>{html.escape(title)}</title><style>{_STYLE}</style></head><body>"
        f"{nav}{body}</body></html>"
    )


def _nav(index_href: str) -> str:
    return f"<nav><a href='{index_href}'>← 返回目录</a></nav>"


def build_bundle(edition: Edition, out_dir) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    # Copy the sealed assets next to the pages using their local names.
    for asset in edition.assets.values():
        src = Path(edition.source) / asset.local_file
        if src.exists():
            dst = out / "assets" / Path(asset.local_file).name
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(src.read_bytes())

    # Rewrite letter pages to point at ./assets/<name> so links stay valid.
    index_cards = []
    for letter in edition.letters:
        page_name = f"letter_{letter.letter_id}.html"
        page_html = _letter_page(edition, letter, page_name)
        (out / page_name).write_text(page_html, encoding="utf-8")

        ev_links = []
        for ev in letter.events:
            for mode, label in ((routing.STANDARD, "通用指引"),
                                (routing.STEP_FREE, "轮椅/无障碍指引")):
                fname = f"guidance_{letter.letter_id}_{ev['event_number']}_{mode}.html"
                try:
                    g = render_guidance_html(edition, letter, ev["event_number"],
                                             mode=mode)
                except Exception as exc:  # keep bundle build resilient
                    g = _page("指引不可用", f"<p>无法签出：{html.escape(str(exc))}</p>",
                              _nav("../index.html"))
                # point the nav back at the right depth
                g = g.replace("</body>", _nav(page_name) + "</body>")
                (out / fname).write_text(g, encoding="utf-8")
                ev_links.append(
                    f"<span class='ev'><strong>{html.escape(ev['event_number'])}</strong> "
                    f"{html.escape(ev['title'])} "
                    f"<a class='btn' href='{fname}'>{label}</a></span>"
                )

        index_cards.append(
            "<div class='card'>"
            f"<h2><a href='{page_name}'>{html.escape(letter.title)}</a></h2>"
            f"<p class='small'>订阅：{html.escape(letter.subscriber)} · "
            f"编号：{html.escape(letter.letter_id)}</p>"
            f"<p>{''.join(ev_links)}</p>"
            "</div>"
        )

    subs = edition.substitutions
    subs_html = ""
    if subs:
        items = "".join(
            f"<li>{html.escape(s['owner'])} / {html.escape(s['asset_id'])}："
            f"{html.escape(s['reason'])}</li>"
            for s in subs
        )
        subs_html = (
            "<div class='card'><h2>替代插图说明</h2>"
            "<p class='small'>以下图片未随本读本保存，正文中以醒目标注的替代图呈现：</p>"
            f"<ul>{items}</ul></div>"
        )

    index_body = (
        f"<h1>离线邮件读本 · 第 {html.escape(edition.issue)} 期</h1>"
        "<p class='small'>本读本完全离线：翻阅、切换、打印均不再访问供图站。</p>"
        + "".join(index_cards)
        + subs_html
    )
    (out / "index.html").write_text(_page("离线邮件读本", index_body), encoding="utf-8")
    return out / "index.html"


def _letter_page(edition: Edition, letter, page_name: str) -> str:
    """Like render_letter_html but with bundle-relative image paths + nav."""
    body = render_letter_html(edition, letter)
    # Strip the standalone wrapper and reuse its body inner HTML.
    inner = body.split("<body>", 1)[-1].rsplit("</body>", 1)[0]
    # Point images at ./assets/<basename>.
    for asset in edition.assets.values():
        if asset.local_file:
            name = Path(asset.local_file).name
            inner = inner.replace(asset.local_file, f"assets/{name}")
            inner = inner.replace(
                f"src=\"{asset.local_file}\"", f"src=\"assets/{name}\""
            )
    # Add guidance links per event.
    links = []
    for ev in letter.events:
        for mode, label in ((routing.STANDARD, "通用指引"),
                            (routing.STEP_FREE, "轮椅/无障碍指引")):
            fname = f"guidance_{letter.letter_id}_{ev['event_number']}_{mode}.html"
            links.append(
                f"<a class='btn' href='{fname}'>{html.escape(ev['event_number'])} "
                f"· {label}</a>"
            )
    inner += "<h2>签出入场指引</h2>" + "".join(links)
    return _page(letter.title, inner, _nav("index.html"))


def main(argv=None) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="reading-room-browse")
    p.add_argument("--edition", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)
    edition = load_edition(args.edition)
    index = build_bundle(edition, args.out)
    print(f"离线读本已生成：{index}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
