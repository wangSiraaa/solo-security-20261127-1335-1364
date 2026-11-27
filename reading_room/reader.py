"""Reader-side operations over a sealed edition: browse, select, print.

Everything here works purely against the sealed local edition.  No function in
this module opens a network connection, so a reader's browsing, switching,
printing and closing of letters can never produce new supply-station requests.
"""

from __future__ import annotations

import html
import os
from pathlib import Path

from . import routing
from .edition import Edition, SealedAsset, SealedLetter
from .wire import RouteDescription

#: Priority for provenance shadowing when the reader asks for the *default*
#: combined route: lower number wins for a given pair of endpoints.  The
#: accompanying map is the most specific on-site document, then the reliable
#: written directions, then the shared issue illustration.
PRIORITY = {
    routing.LETTER_MAP: 0,
    routing.LETTER_TEXT: 1,
    routing.COMMON_MAP: 2,
}

BASIS_CHOICES = ("auto", routing.LETTER_MAP, routing.LETTER_TEXT, routing.COMMON_MAP)


class ReaderError(RuntimeError):
    pass


def _desc(raw: dict | None, label: str) -> RouteDescription | None:
    if not raw:
        return None
    return RouteDescription.from_dict(raw, label=label)


def sources_for_letter(
    edition: Edition, letter: SealedLetter
) -> list[tuple[str, RouteDescription]]:
    """All delivered route descriptions available to a reader of ``letter``."""
    sources: list[tuple[str, RouteDescription]] = []
    if letter.map_description:
        sources.append((routing.LETTER_MAP, _desc(letter.map_description, routing.LETTER_MAP)))
    if letter.directions:
        sources.append((routing.LETTER_TEXT, _desc(letter.directions, routing.LETTER_TEXT)))
    if edition.common_map_description:
        sources.append(
            (routing.COMMON_MAP, _desc(edition.common_map_description, routing.COMMON_MAP))
        )
    return [(s, d) for s, d in sources if d is not None]


def available_bases(
    edition: Edition, letter: SealedLetter, event_number: str, mode: str
) -> list[str]:
    """Which provenance bases can actually sign a route for this event/mode."""
    event = letter.event(event_number)
    bases = []
    for source, _desc_ in sources_for_letter(edition, letter):
        try:
            routing.plan_route(
                [(source, _desc_)],
                letter.arrival_gate,
                event["venue_node"],
                mode=mode,
                labels=edition.node_labels,
                allowed_sources={source},
            )
        except routing.RouteError:
            continue
        bases.append(source)
    return bases


def plan(
    edition: Edition,
    letter: SealedLetter,
    event_number: str,
    mode: str = routing.STANDARD,
    basis: str = "auto",
) -> routing.Route:
    """Plan entry guidance for one event.

    ``basis`` may be ``auto`` (combine sources with map-authoritative
    shadowing), or one explicit provenance tag to sign from only that
    delivered route.
    """
    if mode not in routing.MODES:
        raise ReaderError(f"unknown mode {mode!r}")
    if basis not in BASIS_CHOICES:
        raise ReaderError(f"unknown basis {basis!r}")
    event = letter.event(event_number)
    sources = sources_for_letter(edition, letter)
    if not sources:
        raise ReaderError("这一封信没有任何已交付的路线依据")

    if basis == "auto":
        return routing.plan_route(
            sources,
            letter.arrival_gate,
            event["venue_node"],
            mode=mode,
            labels=edition.node_labels,
            priorities=PRIORITY,
        )
    if basis not in {s for s, _ in sources}:
        raise ReaderError(
            f"这封信没有 {basis!r} 依据；可用：" + ", ".join(s for s, _ in sources)
        )
    return routing.plan_route(
        sources,
        letter.arrival_gate,
        event["venue_node"],
        mode=mode,
        labels=edition.node_labels,
        allowed_sources={basis},
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _asset_note(asset: SealedAsset) -> str:
    if asset.substituted:
        return asset.label or "（替代插图）"
    return ""


def render_letter_text(
    edition: Edition,
    letter: SealedLetter,
    selected_event: str | None = None,
    show_bases: bool = True,
) -> str:
    out = []
    out.append("=" * 64)
    out.append(f"《{letter.title}》   订阅：{letter.subscriber}   编号：{letter.letter_id}")
    out.append("=" * 64)
    out.append(letter.body_text.strip())
    out.append("")

    out.append("—— 插图 ——")
    for key in letter.assets:
        asset = edition.assert_visible(letter, key)
        if asset.substituted:
            out.append(f"[{asset.asset_id}] {_asset_note(asset)}")
        else:
            exists = edition.local_path(key).exists()
            status = "已封存" if exists else "本地文件缺失"
            out.append(f"[{asset.asset_id}] {asset.alt or '(无说明)'}（{status}）")

    if letter.letter_map is not None:
        map_key = letter.letter_map["key"]
        map_asset = edition.assert_visible(letter, map_key)
        out.append("")
        out.append("—— 随信地图 ——")
        if map_asset.substituted:
            out.append(f"[{map_asset.asset_id}] {_asset_note(map_asset)}")
        else:
            out.append(f"[{map_asset.asset_id}] 随信地图（已封存）")
        if letter.map_description and letter.map_description.get("notes"):
            for note in letter.map_description["notes"]:
                out.append(f"  说明：{note}")

    out.append("")
    out.append("—— 活动（凭活动号选择）——")
    for ev in letter.events:
        marker = "→" if selected_event == ev["event_number"] else " "
        out.append(f" {marker} {ev['event_number']}  {ev['title']}  @ {ev['venue']}")
        if ev.get("note"):
            out.append(f"      {ev['note']}")

    if selected_event is not None:
        ev = letter.event(selected_event)
        out.append("")
        out.append(f"已选活动：{ev['event_number']} {ev['title']}")
        if show_bases:
            std = available_bases(edition, letter, selected_event, routing.STANDARD)
            sf = available_bases(edition, letter, selected_event, routing.STEP_FREE)
            out.append(f"  可用路线依据（通用）：{', '.join(std) or '无'}")
            out.append(f"  可用路线依据（无障碍）：{', '.join(sf) or '无'}")
    return "\n".join(out)


def _img_tag(edition: Edition, asset: SealedAsset) -> str:
    if asset.substituted:
        lbl = html.escape(asset.label or "（替代插图）")
        return (
            f'<div class="substitute"><span class="sub-label">{lbl}</span></div>'
        )
    path = edition.local_path(asset.key)
    if not path.exists():
        return '<div class="missing">（本地图片文件缺失）</div>'
    rel = os.path.relpath(path, Path(edition.source))
    alt = html.escape(asset.alt or asset.asset_id)
    return f'<img src="{html.escape(rel)}" alt="{alt}">'


def render_letter_html(
    edition: Edition,
    letter: SealedLetter,
    selected_event: str | None = None,
) -> str:
    parts = []
    parts.append("<!doctype html><html lang='zh'><head><meta charset='utf-8'>")
    parts.append("<title>%s</title>" % html.escape(letter.title))
    parts.append(
        "<style>body{font-family:serif;max-width:760px;margin:2em auto;line-height:1.5}"
        "img{max-width:100%;border:1px solid #ccc}"
        ".substitute{padding:1em;border:2px dashed #b00;background:#fff5f5;"
        "color:#b00;font-weight:bold}.missing{padding:1em;border:1px dashed #999}"
        ".map{border:2px solid #333;padding:.5em}h1{font-size:1.3em}"
        ".ev{padding:.2em .5em}.ev.sel{background:#eef}</style></head><body>"
    )
    parts.append(f"<h1>{html.escape(letter.title)}</h1>")
    parts.append(
        f"<p>订阅：{html.escape(letter.subscriber)} · 编号：{html.escape(letter.letter_id)}</p>"
    )
    parts.append("<p>" + html.escape(letter.body_text).replace("\n", "<br>") + "</p>")
    parts.append("<h2>插图</h2>")
    for key in letter.assets:
        asset = edition.assert_visible(letter, key)
        parts.append(f"<figure>{_img_tag(edition, asset)}"
                     f"<figcaption>{html.escape(asset.alt or asset.asset_id)}</figcaption></figure>")
    if letter.letter_map is not None:
        map_asset = edition.assert_visible(letter, letter.letter_map["key"])
        parts.append("<h2>随信地图</h2><div class='map'>")
        parts.append(_img_tag(edition, map_asset))
        if letter.map_description and letter.map_description.get("notes"):
            for note in letter.map_description["notes"]:
                parts.append(f"<p class='note'>{html.escape(note)}</p>")
        parts.append("</div>")
    parts.append("<h2>活动（凭活动号选择）</h2>")
    for ev in letter.events:
        cls = "ev sel" if selected_event == ev["event_number"] else "ev"
        parts.append(
            f"<p class='{cls}'><strong>{html.escape(ev['event_number'])}</strong> "
            f"{html.escape(ev['title'])} @ {html.escape(ev['venue'])}</p>"
        )
    parts.append("</body></html>")
    return "\n".join(parts)


def render_guidance_text(
    edition: Edition,
    letter: SealedLetter,
    event_number: str,
    mode: str = routing.STANDARD,
    basis: str = "auto",
) -> str:
    # ``auto`` lets the planner weigh every delivered description, taking the
    # cheapest evidenced route and using provenance priority only to break
    # ties.  ``basis`` pins guidance to one delivered document.
    route = plan(edition, letter, event_number, mode=mode, basis=basis)

    ev = letter.event(event_number)
    out = []
    out.append("=" * 64)
    out.append(f"入场指引  {letter.title}")
    out.append(f"活动号：{ev['event_number']}   活动：{ev['title']}")
    out.append(f"场地：{ev['venue']}")
    if ev.get("note"):
        out.append(f"备注：{ev['note']}")
    out.append("-" * 64)
    out.append(route.describe())
    out.append("-" * 64)
    # Substitution disclosure relevant to the documents this route relied on.
    if routing.LETTER_MAP in route.sources and letter.letter_map is not None:
        ma = edition.assert_visible(letter, letter.letter_map["key"])
        if ma.substituted:
            out.append("注意：随信地图以替代图呈现，以上指引依据其文字说明。")
    if routing.COMMON_MAP in route.sources:
        cm = edition.asset(edition.common_map_key)
        if cm.substituted:
            out.append("注意：公共图以替代图呈现，以上指引依据其文字说明。")
    if routing.LETTER_TEXT in route.sources:
        out.append("依据随信文字方向（可靠路线）。")
    out.append("=" * 64)
    return "\n".join(out)


def render_guidance_html(
    edition: Edition,
    letter: SealedLetter,
    event_number: str,
    mode: str = routing.STANDARD,
    basis: str = "auto",
) -> str:
    text = render_guidance_text(edition, letter, event_number, mode, basis)
    body = html.escape(text).replace("\n", "<br>")
    return (
        "<!doctype html><html lang='zh'><head><meta charset='utf-8'>"
        f"<title>入场指引 {html.escape(event_number)}</title>"
        "<style>body{font-family:ui-monospace,monospace;max-width:820px;"
        "margin:2em auto;white-space:pre-wrap}</style></head><body>"
        f"{body}</body></html>"
    )
