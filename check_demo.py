#!/usr/bin/env python3
"""对 demo.sh 生成的运行目录做关键不变量断言。"""
import json
import subprocess
import sys
from pathlib import Path

DESK = "/workspace/desk"
DATE = "2026-11-26"


def archive(root: Path) -> dict:
    out = subprocess.check_output(
        [DESK, "--root", str(root), "--json", "archive", "query", "--date", DATE],
        text=True,
    )
    return json.loads(out)


def seg(view: dict, sid: str) -> dict:
    return next(x for x in view["segments"] if x["segment_id"] == sid)


def main() -> int:
    base = Path(sys.argv[1] if len(sys.argv) > 1 else "/workspace/runs")

    main_view = archive(base / "01-main")
    ed = main_view["official_edition"]
    assert ed and ed["id"] == "ED-0001", "主流程必须发出 ED-0001"
    assert main_view["official_edition_verification"]["ok"], "正式刊次签字/哈希验证必须通过"
    ids = [x["segment_id"] for x in ed["segment_manifest"]]
    assert ids == ["seg-0001", "seg-0002", "seg-0003"], ids
    assert ed["errata_ids"] == ["ER-0001"], "刊后甲的更正必须作为 ER-0001 勘误"

    trace1 = json.loads((base / "01-main/trace-seg0001.json").read_text(encoding="utf-8"))
    assert trace1["attribution_valid"] is True
    assert trace1["signing_visibility"]["named_viewers"] == ["a"]
    assert any(e.get("type") == "segment.viewed" and e.get("viewer") == "b" for e in trace1["related_events"])

    late = archive(base / "03-cmp-late")
    assert seg(late, "seg-0001")["include_in_named_draft"] is True
    assert seg(late, "seg-0002")["include_in_named_draft"] is True

    cancel = archive(base / "04-cmp-cancel")
    assert seg(cancel, "seg-0001")["include_in_named_draft"] is True
    assert seg(cancel, "seg-0002")["include_in_named_draft"] is False

    both = archive(base / "05-cmp-both")
    assert seg(both, "seg-0001")["include_in_named_draft"] is True
    assert seg(both, "seg-0002")["include_in_named_draft"] is True

    consent = archive(base / "06-consent-withdrawal")
    assert seg(consent, "seg-0001")["include_in_named_draft"] is True
    assert seg(consent, "seg-0002")["include_in_named_draft"] is True
    assert seg(consent, "seg-0003")["include_in_named_draft"] is False
    assert seg(consent, "seg-0003")["publication_consent_blocked"] is True

    negative = archive(base / "02-negative")
    assert seg(negative, "seg-0001")["signature_valid"] is True
    assert seg(negative, "seg-0002")["signature_valid"] is False
    assert seg(negative, "seg-0003")["signature_valid"] is True

    late_after_pub = archive(base / "07-late-after-publication")
    frozen = late_after_pub["official_edition"]
    assert [x["segment_id"] for x in frozen["segment_manifest"]] == ["seg-0001"]
    assert seg(late_after_pub, "seg-0002")["signature_valid"] is True
    assert frozen["errata_ids"] == ["ER-0001"]

    print("断言全部通过：边界、迟到签字、取消、私稿锁、防顶替、防改字、唯一刊次与勘误均符合预期。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
