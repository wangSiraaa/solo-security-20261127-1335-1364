from __future__ import annotations

from pathlib import Path
from typing import Any

from .environment import BOOK_ID, DeskEnvironment

LINE_ID = "mandate-zhou"


def setup_mandate_and_pass(
    env: DeskEnvironment,
    *,
    version: int = 1,
    pass_id: str = "pass-ch1",
    chapter_ids: list[str] | None = None,
    edit_self: bool = True,
    redelegate: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    mandate = env.author_desk.issue_mandate(
        env.station.person,
        station_id=env.station.station_id,
        book_id=BOOK_ID,
        chapter_ids=["ch1", "ch2"],
        mandate_id=f"mandate-zhou-v{version}",
        line_id=LINE_ID,
        version=version,
        edit_self=edit_self,
        redelegate=redelegate,
        supersedes=None if version == 1 else f"mandate-zhou-v{version - 1}",
        library_connected=True,
    )
    env.sync_station()
    env.deliver_to_library()
    task_pass = env.station.issue_pass(
        env.terminal.person,
        pass_id=pass_id,
        mandate=env.station.current_mandate or mandate,
        chapter_ids=chapter_ids or ["ch1"],
    )
    return mandate, task_pass


def _revision_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in results if "receipt" in r or r.get("status") in {"landed", "rejected", "pending_merge"}]


def run_main_story(root: str | Path) -> dict[str, Any]:
    env = DeskEnvironment(root)
    env.author_desk.choose_delivery(
        "portable_pass",
        True,
        "作者接受可携带代办凭据，同时确认书库只按当地已送达的撤回记录执行。",
    )
    mandate_v1, task_pass = setup_mandate_and_pass(env)
    terminal_statement = env.terminal.explain_portable(mandate_v1, task_pass)

    # The author can act directly.
    author_pkg = env.author_desk.submit_self_edit(
        book_id=BOOK_ID,
        chapter_id="ch2",
        base_rev_id="rev-0000",
        replacements=[{"old": "青石板的缝里", "new": "青石板的缝隙里"}],
        summary="作者本人补一“隙”字",
    )
    env.deliver_to_library()

    # The outsourced proofreader makes a real correction through the pass.
    proof_pkg = env.terminal.submit_portable(
        mandate=mandate_v1,
        task_pass=task_pass,
        book_id=BOOK_ID,
        chapter_id="ch1",
        base_rev_id="rev-0000",
        replacements=[{"old": "人声顶沸", "new": "人声鼎沸"}],
        summary="校录员订正错别字：顶沸→鼎沸",
    )
    env.deliver_to_library()
    rev_after_proof = env.library.state["book"]["chapters"]["ch1"]["head_rev_id"]

    # Remove ONLY the independent redelegation fact; edit_self remains true.
    mandate_v2 = env.author_desk.remove_redelegation(mandate_v1)
    env.sync_station()
    env.deliver_to_library()

    station_refused_new_pass = False
    station_refusal_reason = ""
    try:
        env.station.issue_pass(
            env.terminal.person,
            pass_id="pass-should-not-exist",
            mandate=mandate_v2,
            chapter_ids=["ch1"],
        )
    except Exception as exc:  # noqa: BLE001 - scenario records the denial
        station_refused_new_pass = True
        station_refusal_reason = str(exc)

    rejected_pkg = env.terminal.submit_portable(
        mandate=mandate_v1,
        task_pass=task_pass,
        book_id=BOOK_ID,
        chapter_id="ch1",
        base_rev_id=rev_after_proof,
        replacements=[{"old": "穿过灯市", "new": "走过灯市"}],
        summary="删去转委托条款后，同一校录员仍试图代表作者送件",
    )
    negative_results = env.deliver_to_library()
    rejected_result = next(
        r for r in negative_results
        if r.get("receipt", {}).get("package_id") == rejected_pkg["id"]
    )

    # The editor's own power is a separate fact and still works.
    ch2_head = env.library.state["book"]["chapters"]["ch2"]["head_rev_id"]
    editor_pkg = env.station.submit_self_edit(
        mandate=mandate_v2,
        book_id=BOOK_ID,
        chapter_id="ch2",
        base_rev_id=ch2_head,
        replacements=[{"old": "鞋尖还是湿了", "new": "衣角却先湿了"}],
        summary="责编本人润色第二章",
        library_connected=True,
    )
    env.deliver_to_library()

    # Restore redelegation.  The editor locks another proof cycle first and
    # advances the actual chapter revision.
    mandate_v3 = env.author_desk.restore_redelegation(mandate_v2)
    env.sync_station()
    env.deliver_to_library()
    restored_pass = env.station.issue_pass(
        env.terminal.person,
        pass_id="pass-ch1-restored",
        mandate=mandate_v3,
        chapter_ids=["ch1"],
    )
    ch1_head_before_lock = env.library.state["book"]["chapters"]["ch1"]["head_rev_id"]
    env.library.lock_chapter(
        "ch1",
        holder_id=env.station.person.id,
        holder_name=env.station.person.name,
        cycle_id="second-proof",
        cycle_name="二校",
        station_id=env.station.station_id,
    )
    editor_ch1_pkg = env.station.submit_self_edit(
        mandate=mandate_v3,
        book_id=BOOK_ID,
        chapter_id="ch1",
        base_rev_id=ch1_head_before_lock,
        replacements=[{"old": "她在桥头停住", "new": "她在桥头停下脚步"}],
        summary="责编先落二校润色",
        library_connected=True,
    )
    env.deliver_to_library()

    stale_base = ch1_head_before_lock
    pending_pkg = env.terminal.submit_portable(
        mandate=mandate_v3,
        task_pass=restored_pass,
        book_id=BOOK_ID,
        chapter_id="ch1",
        base_rev_id=stale_base,
        replacements=[{"old": "穿过灯市", "new": "走过灯市"}],
        summary="校录员另一笔基于旧校次的修改",
    )
    conflict_results = env.deliver_to_library()
    pending_result = next(
        r for r in conflict_results
        if r.get("receipt", {}).get("package_id") == pending_pkg["id"]
    )

    # Restart from disk.  A new environment reads the same trusted cabinet,
    # library state and keys.
    restarted = env.restart()
    report = restarted.library.author_report_text()
    return {
        "state_dir": str(env.root),
        "terminal_statement": terminal_statement,
        "report": report,
        "revisions": restarted.library.revision_report(),
        "pending_merges": restarted.library.state["pending_merges"],
        "submissions": restarted.library.state["submissions"],
        "station_refused_new_pass": station_refused_new_pass,
        "station_refusal_reason": station_refusal_reason,
        "negative_result": {
            "status": rejected_result["status"],
            "code": rejected_result["outcome"]["code"],
            "reason": rejected_result["outcome"]["reason"],
        },
        "pending_result": {
            "status": pending_result["status"],
            "merge_id": pending_result.get("merge_id"),
            "reasons": pending_result.get("reasons"),
        },
        "chapter1_text": restarted.library.chapter_text("ch1"),
        "chapter2_text": restarted.library.chapter_text("ch2"),
        "delivery_choices": restarted.author_desk.choices,
        "cabinet_root": str(env.cabinet.root),
    }


def run_portable_order_case(root: str | Path, order: str) -> dict[str, Any]:
    if order not in {"edit_first", "revoke_first"}:
        raise ValueError(order)
    env = DeskEnvironment(root)
    env.author_desk.choose_delivery(
        "portable_pass",
        True,
        "作者选择可携带凭据，并接受断联时以书库本地撤回记录为准的窗口。",
    )
    _mandate, task_pass = setup_mandate_and_pass(env)
    env.library.set_station_link(False)

    def proofread_ding() -> dict[str, Any]:
        pkg = env.terminal.submit_portable(
            mandate=env.station.current_mandate or _mandate,
            task_pass=task_pass,
            book_id=BOOK_ID,
            chapter_id="ch1",
            base_rev_id="rev-0000",
            replacements=[{"old": "人声顶沸", "new": "人声鼎沸"}],
            summary="断联窗口中的校录订正",
        )
        results = env.deliver_to_library()
        return next(r for r in results if r.get("receipt", {}).get("package_id") == pkg["id"])

    def try_second_edit_after_sync() -> dict[str, Any]:
        head = env.library.state["book"]["chapters"]["ch1"]["head_rev_id"]
        pkg = env.terminal.submit_portable(
            mandate=_mandate,
            task_pass=task_pass,
            book_id=BOOK_ID,
            chapter_id="ch1",
            base_rev_id=head,
            replacements=[{"old": "穿过灯市", "new": "走过灯市"}],
            summary="撤回同步后再次送件",
        )
        results = env.deliver_to_library()
        return next(r for r in results if r.get("receipt", {}).get("package_id") == pkg["id"])

    timeline: list[dict[str, Any]] = []
    revocation = None
    first_edit: dict[str, Any] | None = None

    if order == "edit_first":
        first_edit = proofread_ding()
        timeline.append({"step": "edit", "landed": first_edit.get("landed"), "rev_id": first_edit.get("rev_id")})
        revocation = env.author_desk.revoke(
            LINE_ID,
            "mandate_line",
            "作者在第一笔落稿后到站收回委托",
            library_connected=False,
        )
        env.sync_station()
        timeline.append({"step": "global_revoke_while_library_disconnected", "revocation_id": revocation["id"]})
    else:
        revocation = env.author_desk.revoke(
            LINE_ID,
            "mandate_line",
            "作者在改稿前先到站收回委托",
            library_connected=False,
        )
        env.sync_station()
        timeline.append({"step": "global_revoke_at_station_before_edit", "revocation_id": revocation["id"]})
        first_edit = proofread_ding()
        timeline.append({
            "step": "edit_present_portable_pass_to_library",
            "landed": first_edit.get("landed"),
            "rev_id": first_edit.get("rev_id"),
            "library_known_revocations": first_edit["outcome"]["local_revocation_boundary"]["known_revocation_ids"],
        })

    before_reconnect_text = env.library.chapter_text("ch1")
    # Reconnect and pump the signed revocation from the author desk outbox.
    pumped = env.cabinet.pump("author_desk", "library")
    env.deliver_to_library()
    second = try_second_edit_after_sync()
    timeline.append({"step": "revocation_synced_to_library", "envelopes": len(pumped)})
    timeline.append({"step": "later_edit", "status": second.get("status"), "code": second.get("outcome", {}).get("code")})

    final_text = env.library.chapter_text("ch1")
    return {
        "order": order,
        "state_dir": str(env.root),
        "timeline": timeline,
        "first_edit_status": first_edit.get("status") if first_edit else None,
        "first_rev_id": first_edit.get("rev_id") if first_edit else None,
        "second_status": second.get("status"),
        "second_code": second.get("outcome", {}).get("code"),
        "text_before_reconnect": before_reconnect_text,
        "final_chapter1_text": final_text,
        "landed_ding_character": "鼎" in final_text,
        "old_top_typo_remaining": "人声顶沸" in final_text,
        "library_known_revocation_ids": [r["id"] for r in env.library.state["revocations"]],
    }


def run_locally_known_revocation_case(root: str | Path) -> dict[str, Any]:
    env = DeskEnvironment(root)
    _mandate, task_pass = setup_mandate_and_pass(env)
    env.library.set_station_link(False)
    revocation = env.author_desk.revoke(
        LINE_ID,
        "mandate_line",
        "撤回件已通过本地可信收发柜直接进入书库抽屉",
        library_connected=True,
    )
    env.deliver_to_library()
    pkg = env.terminal.submit_portable(
        mandate=_mandate,
        task_pass=task_pass,
        book_id=BOOK_ID,
        chapter_id="ch1",
        base_rev_id="rev-0000",
        replacements=[{"old": "人声顶沸", "new": "人声鼎沸"}],
        summary="书库本地已知撤回后仍尝试送件",
    )
    results = env.deliver_to_library()
    result = next(r for r in results if r.get("receipt", {}).get("package_id") == pkg["id"])
    return {
        "state_dir": str(env.root),
        "status": result["status"],
        "code": result["outcome"]["code"],
        "revocation_id": revocation["id"],
        "text": env.library.chapter_text("ch1"),
    }


def run_hosted_case(root: str | Path) -> dict[str, Any]:
    env = DeskEnvironment(root)
    env.author_desk.choose_delivery(
        "station_hosted",
        True,
        "作者选择编辑站托管身份：校录员逐笔申请，不持有可直接送书库的代表权。",
    )
    mandate = env.author_desk.issue_mandate(
        env.station.person,
        station_id=env.station.station_id,
        book_id=BOOK_ID,
        chapter_ids=["ch1", "ch2"],
        mandate_id="mandate-zhou-v1",
        line_id=LINE_ID,
        version=1,
        edit_self=True,
        redelegate=True,
        library_connected=True,
    )
    env.sync_station()
    env.deliver_to_library()

    request = env.terminal.request_hosted(
        book_id=BOOK_ID,
        chapter_id="ch1",
        base_rev_id="rev-0000",
        replacements=[{"old": "青灯亮起时", "new": "青灯初上时"}],
        summary="校录员向编辑站申请第一笔托管改稿",
        library_connected=True,
    )
    station_results = env.sync_station()
    library_results = env.deliver_to_library()
    landed = next((r for r in library_results if r.get("landed")), None)

    revocation = env.author_desk.revoke(
        LINE_ID,
        "mandate_line",
        "作者撤回编辑站托管委托",
        library_connected=True,
    )
    env.sync_station()
    env.deliver_to_library()

    second_request = env.terminal.request_hosted(
        book_id=BOOK_ID,
        chapter_id="ch1",
        base_rev_id=landed["rev_id"] if landed else "rev-0000",
        replacements=[{"old": "穿过灯市", "new": "走过灯市"}],
        summary="撤回后再申请新单",
        library_connected=True,
    )
    after_revoke_station = env.sync_station()

    env.library.set_station_link(False)
    claim_pkg = env.terminal.submit_hosted_claim_only(
        book_id=BOOK_ID,
        chapter_id="ch1",
        base_rev_id=landed["rev_id"] if landed else "rev-0000",
        replacements=[{"old": "灯市如昼", "new": "灯市如书"}],
        summary="断联后校录员自称代理直接送件",
    )
    claim_results = env.deliver_to_library()
    claim_result = next(
        r for r in claim_results
        if r.get("receipt", {}).get("package_id") == claim_pkg["id"]
    )

    restarted = env.restart()
    return {
        "state_dir": str(env.root),
        "first_request_id": request["id"],
        "station_results": station_results,
        "landed_rev_id": landed.get("rev_id") if landed else None,
        "landed_mode": landed.get("revision", {}).get("mode") if landed else None,
        "actual_operator_log": env.station.actual_operator_log,
        "revocation_id": revocation["id"],
        "after_revoke_station": after_revoke_station,
        "claim_status": claim_result["status"],
        "claim_code": claim_result["outcome"]["code"],
        "claim_reason": claim_result["outcome"]["reason"],
        "report_after_restart": restarted.library.author_report_text(),
    }


def run_all_scenarios(base_dir: str | Path) -> dict[str, Any]:
    base = Path(base_dir)
    main = run_main_story(base / "main")
    edit_first = run_portable_order_case(base / "offline-edit-first", "edit_first")
    revoke_first = run_portable_order_case(base / "offline-revoke-first", "revoke_first")
    local_revoke = run_locally_known_revocation_case(base / "local-revocation-first")
    hosted = run_hosted_case(base / "hosted")
    return {
        "base_dir": str(base),
        "main": main,
        "edit_first": edit_first,
        "revoke_first": revoke_first,
        "local_revoke": local_revoke,
        "hosted": hosted,
    }
