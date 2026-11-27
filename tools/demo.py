#!/usr/bin/env python3
"""Live demo of the reading-room requirements.

Replays, against a running local supply station:

  * import (seal at arrival) of the three-letter issue,
  * two different reading orders, proving the station log is unchanged,
  * the two entry guidances (A with map, B address+text only),
  * the revised-map variant: only A's map/description change, the wheelchair
    guidance detours, and B is untouched.

Equivalent to ``python3 -m tools.proof`` but writes the artefacts to a
directory you can inspect.  Usage:

    python3 tools/demo.py [output-dir]
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reading_room import reader, routing
from reading_room.browse import build_bundle
from reading_room.edition import seal_issue
from reading_room.fixtures import ensure_fixtures
from reading_room.supply import SupplyStation
from reading_room.wire import load_mailbox

ROOT = Path(__file__).resolve().parent.parent
ensure_fixtures(ROOT)


def main(argv):
    out = Path(argv[1]) if len(argv) > 1 else ROOT / "demo_out"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    def seal(fixture, name):
        station_root = out / f"station_{name}"
        shutil.copytree(ROOT / "supply_station" / "assets", station_root)
        log_path = out / f"requests_{name}.log"
        with SupplyStation(station_root, log_path) as st:
            ed = seal_issue(
                load_mailbox(ROOT / "fixtures" / fixture),
                out / f"edition_{name}",
                base_url=st.base_url,
            )
            entries = st.log.entries()
        return ed, entries, log_path

    # ---- base issue ----
    ed, seal_log, log_path = seal("issue.json", "base")
    print("### 封存请求")
    for e in seal_log:
        print(f"  {e['status']} {e['path']}")

    a, b, c = (ed.letter(x) for x in ("letter_a", "letter_b", "letter_c"))

    def sig():
        st = SupplyStation(out / "station_base", log_path)
        return [(e["method"], e["path"], e["status"]) for e in st.log.entries()]

    baseline = sig()

    # ---- reading order 1 ----
    reader.render_letter_text(ed, a, "A-101")
    reader.render_letter_text(ed, b, "B-202")
    reader.render_guidance_text(ed, a, "A-101", mode=routing.STEP_FREE)
    reader.render_letter_html(ed, c)
    reader.render_guidance_html(ed, b, "B-202")
    order1 = sig()

    # ---- reading order 2 ----
    reader.render_letter_text(ed, c, "C-303")
    reader.render_letter_html(ed, a)
    reader.render_guidance_text(ed, b, "B-202", mode=routing.STEP_FREE)
    reader.render_letter_text(ed, a)
    reader.render_guidance_text(ed, a, "A-101", mode=routing.STANDARD)
    reader.render_guidance_html(ed, c, "C-303")
    order2 = sig()

    print("\n### 两种阅读顺序后供图记录一致：", order1 == order2 == baseline)

    # ---- two guidances ----
    print("\n### 甲（有随信地图）· 轮椅路线")
    print(reader.render_guidance_text(ed, a, "A-101", mode=routing.STEP_FREE))
    print("\n### 乙（仅地址+文字）· 轮椅路线")
    print(reader.render_guidance_text(ed, b, "B-202", mode=routing.STEP_FREE))

    # ---- offline bundle ----
    build_bundle(ed, out / "bundle_base")

    # ---- revised map ----
    ed2, seal_log2, _ = seal("issue_revised_map.json", "rev")
    print("\n### 修订版封存请求")
    for e in seal_log2:
        print(f"  {e['status']} {e['path']}")
    a2 = ed2.letter("letter_a")
    route2 = reader.plan(ed2, a2, "A-101", mode=routing.STEP_FREE)
    print("\n### 仅改甲随信地图后 · 轮椅指引")
    print(reader.render_guidance_text(ed2, a2, "A-101", mode=routing.STEP_FREE))
    print(f"  含楼梯：{route2.has_stairs}（应 False）")
    used = {(s.edge.u, s.edge.v) for s in route2.steps}
    print(f"  改走东廊入会场：{('east_corridor', 'venue') in used}（应 True）")
    print("  乙信未变：",
          (b.directions == ed2.letter("letter_b").directions
           and b.assets == ed2.letter("letter_b").assets))
    build_bundle(ed2, out / "bundle_revised")
    print(f"\n离线读本：{out/'bundle_base'/'index.html'}")
    print(f"修订读本：{out/'bundle_revised'/'index.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
