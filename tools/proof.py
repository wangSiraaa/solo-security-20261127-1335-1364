"""End-to-end proof harness for the reading-room requirements.

Runs the whole story against a live local supply station and prints a report
covering:

1. supply-station log after sealing (public-once, subscriber-never),
2. two different reading orders over the same issue -> identical station log,
3. the two entry guidances (letter A with map, letter B address+text only),
4. the revised-map variant: only letter A's map/description change, and the
   wheelchair guidance detours instead of climbing stairs.

Usage:  python3 -m tools.proof
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from reading_room import reader, routing
from reading_room.edition import seal_issue
from reading_room.fixtures import ensure_fixtures
from reading_room.supply import SupplyStation
from reading_room.wire import load_mailbox

ROOT = Path(__file__).resolve().parent.parent
ensure_fixtures(ROOT)
STATION_SRC = ROOT / "supply_station" / "assets"


def _seal(mailbox_path, workdir):
    workdir = Path(workdir)
    station_root = workdir / "station"
    if not station_root.exists():
        shutil.copytree(STATION_SRC, station_root)
    log_path = workdir / "requests.log"
    with SupplyStation(station_root, log_path) as station:
        mb = load_mailbox(mailbox_path)
        edition = seal_issue(mb, workdir / "edition", base_url=station.base_url)
        station_log = station.log.entries()
    return edition, station_log, log_path


def _log_signature(entries):
    return [(e["method"], e["path"], e["status"]) for e in entries]


def reading_order_1(edition):
    """Reader flips: A -> B -> A(print) -> B(print) -> close A."""
    a = edition.letter("letter_a")
    b = edition.letter("letter_b")
    reader.render_letter_text(edition, a, "A-101")
    reader.render_letter_text(edition, b, "B-202")
    reader.render_guidance_text(edition, a, "A-101", mode=routing.STEP_FREE)
    reader.render_letter_html(edition, a)
    reader.render_guidance_text(edition, b, "B-202", mode=routing.STANDARD)
    reader.render_letter_html(edition, b)


def reading_order_2(edition):
    """Reader flips the other way, double-backs, prints twice, closes B."""
    a = edition.letter("letter_a")
    b = edition.letter("letter_b")
    reader.render_letter_text(edition, b, "B-202")
    reader.render_letter_text(edition, a, "A-101")
    reader.render_letter_text(edition, b)
    reader.render_guidance_text(edition, b, "B-202", mode=routing.STEP_FREE)
    reader.render_guidance_html(edition, a, "A-101", mode=routing.STEP_FREE)
    reader.render_guidance_text(edition, a, "A-101", mode=routing.STANDARD)
    reader.render_letter_html(edition, b)


def report():
    lines = []
    work = Path(tempfile.mkdtemp(prefix="readingroom-proof-"))
    try:
        # ---- 1 & 2: seal once, prove offline reads add nothing ----
        edition, seal_log, log_path = _seal(ROOT / "fixtures" / "issue.json", work / "base")
        before = _log_signature(SupplyStation(STATION_SRC, log_path).log.entries())
        lines.append("## 1. 封存时的供图请求")
        for m, p, s in before:
            lines.append(f"  {s} {m} {p}")
        subscriber_hits = [p for _, p, _ in before if p.startswith("/sub/")]
        lines.append(f"  订阅者地址请求数：{len(subscriber_hits)}（应为 0）")
        public_dupes = {}
        for _, p, _ in before:
            public_dupes[p] = public_dupes.get(p, 0) + 1
        shared_path = "/public/issue1420/shared_illu.png"
        lines.append(
            f"  公共图 {shared_path} 请求次数：{public_dupes.get(shared_path, 0)}"
            "（多封来信共用同一公共图，应只取 1 次）"
        )

        # Perform both reading orders; the station is not running, so any
        # accidental network use would raise.  We also snapshot the log file.
        reading_order_1(edition)
        after_order1 = _log_signature(SupplyStation(STATION_SRC, log_path).log.entries())
        reading_order_2(edition)
        after_order2 = _log_signature(SupplyStation(STATION_SRC, log_path).log.entries())
        lines.append("")
        lines.append("## 2. 两种阅读顺序后的供图记录")
        lines.append(f"  顺序一后记录条数：{len(after_order1)}")
        lines.append(f"  顺序二后记录条数：{len(after_order2)}")
        lines.append(f"  两种顺序记录完全一致：{after_order1 == after_order2 == before}")

        # ---- 3: two entry guidances ----
        a = edition.letter("letter_a")
        b = edition.letter("letter_b")
        lines.append("")
        lines.append("## 3. 两张入场指引")
        lines.append("### 甲（有随信地图）· 通用 auto")
        lines.append(reader.render_guidance_text(edition, a, "A-101", mode=routing.STANDARD))
        lines.append("### 甲 · 无障碍 auto")
        lines.append(reader.render_guidance_text(edition, a, "A-101", mode=routing.STEP_FREE))
        lines.append("### 乙（仅地址+文字）· 无障碍 auto")
        lines.append(reader.render_guidance_text(edition, b, "B-202", mode=routing.STEP_FREE))

        # reliable written route independently usable for both letters
        lines.append("")
        lines.append("## 4. 可靠的随信文字路线可单独签出")
        for letter, ev in [(a, "A-101"), (b, "B-202")]:
            for mode in routing.MODES:
                route = reader.plan(edition, letter, ev, mode=mode,
                                    basis=routing.LETTER_TEXT)
                lines.append(
                    f"  {letter.letter_id} {mode}: 依据字母路线签出 OK，"
                    f"含楼梯={route.has_stairs}"
                )
        shutil.rmtree(work / "base", ignore_errors=True)

        # ---- 5: revised map variant ----
        edition2, _, _ = _seal(
            ROOT / "fixtures" / "issue_revised_map.json", work / "rev"
        )
        a2 = edition2.letter("letter_a")
        lines.append("")
        lines.append("## 5. 仅改随信地图（西廊改单向）后的甲指引")
        lines.append("### 甲(修订) · 通用 auto")
        lines.append(reader.render_guidance_text(edition2, a2, "A-101", mode=routing.STANDARD))
        lines.append("### 甲(修订) · 无障碍 auto")
        sf = reader.render_guidance_text(edition2, a2, "A-101", mode=routing.STEP_FREE)
        lines.append(sf)
        sf_route = reader.plan(edition2, a2, "A-101", mode=routing.STEP_FREE)
        lines.append(f"  无障碍指引仍含楼梯：{sf_route.has_stairs}（应为 False）")
        # and it really detours via the reliable eastern corridor, not west.
        used = {(s.edge.u, s.edge.v) for s in sf_route.steps} | {
            (s.edge.v, s.edge.u) for s in sf_route.steps
        }
        lines.append(
            f"  无障碍指引经东廊：{('east_corridor', 'venue') in used}"
            "（西廊单向后应改走东廊）"
        )
        # letter B must be untouched.
        b2 = edition2.letter("letter_b")
        same_b = (b.directions == b2.directions and b.assets == b2.assets)
        lines.append(f"  乙信未受影响：{same_b}")

        return "\n".join(lines)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main():
    print(report())


if __name__ == "__main__":
    main()
