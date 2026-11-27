"""Executable proof of the requested studio/cooperative workflow."""
from __future__ import annotations

import shutil
import stat
from pathlib import Path
from typing import Any, Dict, List

from .privacy import assert_clean, scan_for_leaks
from .workspace import Workspace

BASE_SENTENCE = "潮水退回桥下。"
SECOND_EDIT = "潮水漫过桥下。"
REOPEN_EDIT = "潮水涌过桥下。"
CANARY = "CANARY-PRIVATE-NOTE-7F3A：第一句的双关旁注，绝不能出门。"
MIXED_CANARY = "CANARY-MIXED-NOTE-9C21：混合笔中的私有原句考证。"


def _anchor_ids(report: Dict[str, Any]) -> List[str]:
    return sorted({h["anchor"] for h in report.get("affected_locations", [])})


def run_demo(root: Path) -> Dict[str, Any]:
    root = Path(root)
    shutil.rmtree(root, ignore_errors=True)
    exchange = root / "exchange"
    exchange.mkdir(parents=True)

    checks: List[Dict[str, Any]] = []

    def check(name: str, ok: bool, detail: Any = None) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})
        if not ok:
            raise AssertionError(name if detail is None else f"{name}: {detail}")

    # 1. Mail a page with two byte-identical public sentences.
    main = root / "page"
    museum_dir = main / "museum"
    coop_dir = main / "coop"
    museum = Workspace.init(
        museum_dir,
        role="museum",
        author="museum",
        anchors=[("A1", BASE_SENTENCE), ("A2", BASE_SENTENCE)],
    )
    museum.deliver_to(coop_dir, exchange_packet=exchange / "00-delivery.json")
    coop = Workspace(coop_dir)

    museum_public = (museum_dir / "public.txt").read_text(encoding="utf-8")
    coop_public = (coop_dir / "public.txt").read_text(encoding="utf-8")
    check("delivered public materials are identical", museum_public == coop_public)

    # 2. Coop changes only the second sentence, then undoes and redoes it.
    edit_op = coop.edit("A2", SECOND_EDIT, old=BASE_SENTENCE)
    undo_op = coop.undo()
    check(
        "undo restores identical base on A2",
        coop.doc["anchors"][1]["text"] == BASE_SENTENCE,
        undo_op["undo_of"] == edit_op["id"],
    )
    redo_op = coop.redo()
    check(
        "redo reapplies second sentence edit",
        coop.doc["anchors"][1]["text"] == SECOND_EDIT,
        redo_op["redo_of"] == edit_op["id"],
    )
    check(
        "first identical sentence is untouched offline",
        coop.doc["anchors"][0]["text"] == BASE_SENTENCE,
    )
    coop.push(exchange / "10-coop-second.json")

    # 3. Museum adds a note before A1 only in its private directory.
    note = museum.add_private_note("A1", CANARY, placement="before")
    check(
        "private note has museum-only full-text coordinates",
        note["full_text_start"] == 0 and note["full_text_end"] > 0,
        {"anchor": note["anchor"]},
    )
    private_file = museum_dir / "private" / "private.json"
    check("private directory mode is 0700", stat.S_IMODE((museum_dir / "private").stat().st_mode) == 0o700)
    check("private file mode is 0600", stat.S_IMODE(private_file.stat().st_mode) == 0o600)

    # 4. Merge coop redo.  The private note creates neither a public hunk nor
    #    a public conflict; only A2 is affected.
    merge_report = museum.merge_packet(
        exchange / "10-coop-second.json",
        answer_out=exchange / "11-museum-answer.json",
    )
    check("coop revision merges", merge_report["status"] == "merged", merge_report)
    check("no public conflict from private note", merge_report["conflicts"] == [])
    check("only A2 is reported", _anchor_ids(merge_report) == ["A2"], _anchor_ids(merge_report))
    check(
        "merged page keeps A1 base and A2 redo",
        museum.doc["anchors"][0]["text"] == BASE_SENTENCE
        and museum.doc["anchors"][1]["text"] == SECOND_EDIT,
    )

    # 5. On the same public anchor as the private note, museum changes one
    #    public character.  The external merge answer points only at A1.
    museum.edit("A1", "潮光退回桥下。", old=BASE_SENTENCE)
    museum.push(exchange / "20-museum-a1-char.json", peer="coop")
    coop = Workspace(coop_dir)
    a1_report = coop.merge_packet(
        exchange / "20-museum-a1-char.json",
        answer_out=exchange / "21-coop-answer.json",
    )
    check("A1 revision merges", a1_report["status"] == "merged", a1_report)
    check("A1 answer has one anchor", _anchor_ids(a1_report) == ["A1"], _anchor_ids(a1_report))
    check("A1 answer has exactly one hunk", len(a1_report["affected_locations"]) == 1)
    check("A1 answer has no conflict", a1_report["conflicts"] == [])
    museum.acknowledge_answer(exchange / "21-coop-answer.json")

    # 6. Close and reopen both directories.  Confirmed history persists and
    #    both branches can continue making public edits.
    museum = Workspace(museum_dir)
    coop = Workspace(coop_dir)
    check("both ends reopen from disk", museum.author == "museum" and coop.author == "coop")
    coop.edit("A2", REOPEN_EDIT, old=SECOND_EDIT)
    coop.push(exchange / "30-coop-reopened.json")
    museum = Workspace(museum_dir)
    reopen_report = museum.merge_packet(
        exchange / "30-coop-reopened.json",
        answer_out=exchange / "31-museum-reopen-answer.json",
    )
    check("confirmed delivery remains editable after reopen", reopen_report["status"] == "merged")
    check(
        "reopened edit is present",
        museum.doc["anchors"][1]["text"] == REOPEN_EDIT,
    )
    museum.acknowledge_answer(exchange / "31-museum-reopen-answer.json")

    external_roots = [
        coop_dir,
        exchange,
        museum_dir / "outbox",
        museum_dir / "reports",
        museum_dir / "conflicts",
    ]
    findings = scan_for_leaks(external_roots, [CANARY])
    check("main workflow has no privacy leak", findings == [], findings)

    # 7. Literally restore the original page and make only a one-character
    #    museum edit at one anchor.  This leaves one affected public location.
    restored = root / "restored-original"
    rm_dir = restored / "museum"
    rc_dir = restored / "coop"
    rm = Workspace.init(
        rm_dir,
        role="museum",
        author="museum",
        anchors=[("S1", BASE_SENTENCE), ("S2", BASE_SENTENCE)],
    )
    rm.deliver_to(rc_dir)
    rm.edit("S1", "潮光退回桥下。", old=BASE_SENTENCE)
    rm.push(restored / "museum-one-char.json")
    rc = Workspace(rc_dir)
    restored_report = rc.merge_packet(restored / "museum-one-char.json")
    check("restored one-character merge is clean", restored_report["status"] == "merged")
    check(
        "restored merge identifies exactly one affected anchor/hunk",
        _anchor_ids(restored_report) == ["S1"]
        and len(restored_report["affected_locations"]) == 1,
        restored_report["affected_locations"],
    )

    # 8. Both sides cut the same original line at different offsets while
    #    offline.  Alignment uses the true common public base.
    split_root = root / "same-line-split"
    sm_dir = split_root / "museum"
    sc_dir = split_root / "coop"
    line = "一二三四五六七八"
    sm = Workspace.init(sm_dir, role="museum", author="museum", anchors=[("L1", line)])
    sm.deliver_to(sc_dir)
    sm = Workspace(sm_dir)
    sc = Workspace(sc_dir)
    sm.edit("L1", "\n", start=2, end=2)
    sc.edit("L1", "\n", start=5, end=5)
    sc.push(split_root / "coop-cut.json")
    split_m_report = sm.merge_packet(
        split_root / "coop-cut.json", answer_out=split_root / "museum-answer.json"
    )
    sm.push(split_root / "museum-cut.json")
    sc = Workspace(sc_dir)
    split_c_report = sc.merge_packet(split_root / "museum-cut.json")
    expected_split = "一二\n三四五\n六七八"
    check("museum aligns both cuts", split_m_report["status"] == "merged")
    check("coop aligns both cuts", split_c_report["status"] == "merged")
    check(
        "both replicas converge after mutual pushes",
        sm.doc["anchors"][0]["text"] == expected_split
        and sc.doc["anchors"][0]["text"] == expected_split,
        expected_split,
    )

    # 9. Mixed transaction: whole undo before delivery; public correction only
    #    after the public half has been delivered into coop's autonomous史.
    mixed_root = root / "mixed-policy"
    mixed_exchange = mixed_root / "exchange"
    mixed_exchange.mkdir(parents=True, exist_ok=True)
    pre_dir = mixed_root / "pre-delivery"
    pre = Workspace.init(pre_dir, role="museum", author="museum", anchors=[("M1", "甲乙丙")])
    mixed = pre.mixed_edit("M1", "甲X丙", old="甲乙丙", note_text=MIXED_CANARY)
    pre_undo = pre.undo_mixed(mixed["internal_id"])
    check("undelivered mixed op can be wholly undone", pre_undo["policy"] == "whole-mixed-undo")
    check("whole undo restores public text", pre.public_text().strip() == "甲乙丙")

    pm_dir = mixed_root / "museum"
    pc_dir = mixed_root / "coop"
    pm = Workspace.init(pm_dir, role="museum", author="museum", anchors=[("M1", "甲乙丙")])
    pm.deliver_to(pc_dir, exchange_packet=mixed_exchange / "delivery.json")
    delivered_mixed = pm.mixed_edit("M1", "甲X丙", old="甲乙丙", note_text=MIXED_CANARY)
    pm.push(mixed_exchange / "mixed-public.json")
    pc = Workspace(pc_dir)
    mixed_merge = pc.merge_packet(
        mixed_exchange / "mixed-public.json", answer_out=mixed_exchange / "mixed-answer.json"
    )
    check("mixed public half is delivered", mixed_merge["status"] == "merged")
    pm.acknowledge_answer(mixed_exchange / "mixed-answer.json")
    post_undo = pm.undo_mixed(delivered_mixed["internal_id"])
    check(
        "delivered mixed undo becomes a public correction proposal",
        post_undo["policy"] == "public-correction-proposed",
    )
    check("private half is withdrawn in museum store", pm.private["annotations"][-1]["withdrawn"])
    pm.push(mixed_exchange / "correction.json")
    pc = Workspace(pc_dir)
    correction_report = pc.merge_packet(mixed_exchange / "correction.json")
    check("public correction merges externally", correction_report["status"] == "merged")
    check("correction restores public text", pc.public_text().strip() == "甲乙丙")
    mixed_findings = scan_for_leaks(
        [pc_dir, mixed_exchange, pm_dir / "outbox", pm_dir / "reports"],
        [MIXED_CANARY, CANARY],
    )
    check("mixed workflow has no privacy leak", mixed_findings == [], mixed_findings)

    # 10. Explicit authority check: after delivery, coop's autonomous branch
    #     can append a withdrawal offline; museum cannot erase that history.
    authority_root = root / "delivered-authority"
    authority_exchange = authority_root / "exchange"
    authority_exchange.mkdir(parents=True)
    am_dir = authority_root / "museum"
    ac_dir = authority_root / "coop"
    am = Workspace.init(am_dir, role="museum", author="museum", anchors=[("W1", "甲乙")])
    am.deliver_to(ac_dir)
    delivered_op = am.edit("W1", "甲Y", old="甲乙")
    am.push(authority_exchange / "museum-change.json")
    ac = Workspace(ac_dir)
    authority_merge = ac.merge_packet(authority_exchange / "museum-change.json")
    check("authority fixture receives public change", authority_merge["status"] == "merged")
    withdrawal = ac.withdraw_delivered(delivered_op["id"])
    check(
        "coop can withdraw delivered public change offline",
        withdrawal["kind"] == "withdrawal" and ac.public_text().strip() == "甲乙",
    )
    ac.undo()
    check("coop can undo its own withdrawal", ac.public_text().strip() == "甲Y")
    ac.redo()
    check("coop can redo its withdrawal", ac.public_text().strip() == "甲乙")
    authority_findings = scan_for_leaks([ac_dir, authority_exchange, am_dir / "outbox"], [CANARY, MIXED_CANARY])
    check("authority workflow has no privacy leak", authority_findings == [], authority_findings)

    report: Dict[str, Any] = {
        "ok": all(c["ok"] for c in checks),
        "root": str(root),
        "policy": {
            "mode": "autonomous-public-history-split-at-delivery",
            "delivered_public_undo_authority": "coop-branch",
            "own_operation_undo_redo": "offline",
            "museum_delivered_mixed_undo": "private-half-plus-new-public-correction",
        },
        "check_count": len(checks),
        "checks": checks,
        "public_result": museum.public_text(),
    }
    (root / "demo-report.json").write_text(
        __import__("json").dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown = [
        "# Collab editor demo report",
        "",
        f"All {len(checks)} checks passed." if report["ok"] else "Some checks failed.",
        "",
        "## Checks",
        "",
    ]
    for item in checks:
        markdown.append(f"- {'PASS' if item['ok'] else 'FAIL'}: {item['name']}")
    markdown.extend(["", "## Final public page", "", "```text", museum.public_text().rstrip("\n"), "```", ""])
    (root / "DEMO-REPORT.md").write_text("\n".join(markdown), encoding="utf-8")
    return report
