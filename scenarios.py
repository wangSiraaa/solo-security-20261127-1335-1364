"""Fictional accounts and tabletop scenarios for the recovery counter."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path
from typing import Any, Callable

from recovery_desk import ROOT, DeskError, jdumps, jloads, now_iso

DATA_DIR = ROOT / "data"
DEADLINE = "2026-11-27T08:00:00"
T0 = "2026-11-26T20:00:00"


def expect_error(marker: str, fn: Callable[[], Any]) -> str:
    try:
        fn()
    except DeskError as exc:
        if marker not in str(exc):
            raise AssertionError(f"期望错误包含 {marker!r}，实际：{exc}") from exc
        return str(exc)
    raise AssertionError(f"期望失败 {marker}，但调用成功了")


def seed_alice(d: Any) -> str:
    """Two independent sources complete a real takeover and setting change."""
    d.add_account("acct-alice", "Alice Example",
                  note="第一条黄金路径：纸册 + 见证席", at=T0)
    d.add_device("dev-alice-old", "acct-alice", "Alice 旧手机", role="old", at=T0)
    d.add_device("dev-alice-new", "acct-alice", "Alice 新手机", role="new", at=T0)

    d.add_source("src-alice-paper", "acct-alice", "paper_booklet",
                 "A-7 纸质恢复册", risk_score=10, at=T0)
    d.add_source("src-alice-witness", "acct-alice", "witness_desk",
                 "社区见证席 W-1", risk_score=20, at=T0)
    d.add_credential("cred-alice-paper", "acct-alice", "src-alice-paper",
                     "纸册骑缝码", at=T0)
    d.add_credential("cred-alice-witness", "acct-alice", "src-alice-witness",
                     "见证席晚班凭据", at=T0)

    d.submit_application("app-alice-1", "acct-alice",
                         old_device_id="dev-alice-old",
                         new_device_id="dev-alice-new",
                         deadline=DEADLINE, at=T0)
    d.add_confirmation("cnf-alice-paper", "acct-alice", "src-alice-paper",
                      application_id="app-alice-1",
                      credential_id="cred-alice-paper",
                      signer="Alice + 纸册保管人", at="2026-11-26T20:05:00")
    d.add_confirmation("cnf-alice-witness", "acct-alice", "src-alice-witness",
                      application_id="app-alice-1",
                      credential_id="cred-alice-witness",
                      signer="晚班见证人", at="2026-11-26T20:06:00")

    oid = d.issue_order("app-alice-1", "ord-alice-1",
                        at="2026-11-26T20:10:00")
    d.claim_order(oid, "dev-alice-new", at="2026-11-26T20:14:00")
    d.sign_order_receipt(oid, "dev-alice-new", at="2026-11-26T20:18:00")
    d.change_setting("acct-alice", "dev-alice-new", "5",
                     key="auto_lock_minutes", order_id=oid,
                     at="2026-11-26T20:22:00")
    return oid


def seed_bob_sync_copy(d: Any) -> tuple[str, str]:
    """Independent pair succeeds; after one becomes a sync copy, next takeover fails."""
    d.add_account("acct-bob", "Bob Example",
                  note="先办成，再把一个来源改成另一个的同步副本", at=T0)
    d.add_device("dev-bob-old", "acct-bob", "Bob 旧笔电", role="old", at=T0)
    d.add_device("dev-bob-new", "acct-bob", "Bob 新笔电", role="new", at=T0)
    d.add_device("dev-bob-newer", "acct-bob", "Bob 第二台新机", role="new", at=T0)

    d.add_source("src-bob-paper", "acct-bob", "paper_booklet",
                 "B-2 纸质恢复册", risk_score=10, at=T0)
    d.add_source("src-bob-witness", "acct-bob", "witness_desk",
                 "夜间见证席 W-2", risk_score=20, at=T0)
    d.add_credential("cred-bob-paper", "acct-bob", "src-bob-paper",
                     "纸册码", at=T0)
    d.add_credential("cred-bob-witness", "acct-bob", "src-bob-witness",
                     "见证凭据", at=T0)

    d.submit_application("app-bob-1", "acct-bob",
                         old_device_id="dev-bob-old",
                         new_device_id="dev-bob-new",
                         deadline=DEADLINE, at=T0)
    d.add_confirmation("cnf-bob-paper-1", "acct-bob", "src-bob-paper",
                      application_id="app-bob-1",
                      credential_id="cred-bob-paper", at="20:05".join(("2026-11-26T", ":00")))
    d.add_confirmation("cnf-bob-witness-1", "acct-bob", "src-bob-witness",
                      application_id="app-bob-1",
                      credential_id="cred-bob-witness",
                      at="2026-11-26T20:06:00")
    oid1 = d.issue_order("app-bob-1", "ord-bob-1",
                         at="2026-11-26T20:10:00")
    d.claim_order(oid1, "dev-bob-new", at="2026-11-26T20:14:00")
    d.sign_order_receipt(oid1, "dev-bob-new", at="2026-11-26T20:18:00")
    d.change_setting("acct-bob", "dev-bob-new", "5", order_id=oid1,
                     at="2026-11-26T20:22:00")

    # A later intake round uses fresh signatures from the same two sources.
    d.submit_application("app-bob-2", "acct-bob",
                         old_device_id="dev-bob-old",
                         new_device_id="dev-bob-newer",
                         deadline=DEADLINE, at="2026-11-26T21:00:00")
    d.add_confirmation("cnf-bob-paper-2", "acct-bob", "src-bob-paper",
                      application_id="app-bob-2",
                      credential_id="cred-bob-paper",
                      at="2026-11-26T21:05:00")
    d.add_confirmation("cnf-bob-witness-2", "acct-bob", "src-bob-witness",
                      application_id="app-bob-2",
                      credential_id="cred-bob-witness",
                      at="2026-11-26T21:06:00")

    # Mutation requested by the tabletop exercise: witness record is now a
    # synced copy of the paper source, so roots are no longer disjoint.
    d.link_source("src-bob-witness", "src-bob-paper", "synced_copy_of",
                  note="演练变更：改成同步副本",
                  at="2026-11-26T21:10:00")
    err = expect_error("NO_INDEPENDENT_PAIR",
                       lambda: d.issue_order("app-bob-2", "ord-bob-2",
                                             at="2026-11-26T21:12:00"))
    d.audit("program", "order.issue", "rejected", account_id="acct-bob",
            error_code="NO_INDEPENDENT_PAIR",
            detail={"application_id": "app-bob-2", "reason": err},
            at="2026-11-26T21:12:00")
    return oid1, err


def seed_carol_unsigned(d: Any) -> dict[str, str]:
    """Order is claimed but not receipt-signed; old/new devices both try."""
    d.add_account("acct-carol", "Carol Example",
                  note="只有领单、尚未签收；旧机草稿不得搭车", at=T0)
    d.add_device("dev-carol-old", "acct-carol", "Carol 旧平板", role="old", at=T0)
    d.add_device("dev-carol-new", "acct-carol", "Carol 新平板", role="new", at=T0)

    d.add_source("src-carol-paper", "acct-carol", "paper_booklet",
                 "C-9 纸册", risk_score=10, at=T0)
    d.add_source("src-carol-witness", "acct-carol", "witness_desk",
                 "见证席 W-9", risk_score=20, at=T0)
    d.add_credential("cred-carol-paper", "acct-carol", "src-carol-paper",
                     "纸册码", at=T0)
    d.add_credential("cred-carol-witness", "acct-carol", "src-carol-witness",
                     "见证码", at=T0)

    d.submit_application("app-carol", "acct-carol",
                         old_device_id="dev-carol-old",
                         new_device_id="dev-carol-new",
                         deadline=DEADLINE, at=T0)
    d.add_confirmation("cnf-carol-paper", "acct-carol", "src-carol-paper",
                      application_id="app-carol",
                      credential_id="cred-carol-paper", at="2026-11-26T20:05:00")
    d.add_confirmation("cnf-carol-witness", "acct-carol", "src-carol-witness",
                      application_id="app-carol",
                      credential_id="cred-carol-witness", at="2026-11-26T20:06:00")
    oid = d.issue_order("app-carol", "ord-carol",
                        at="2026-11-26T20:10:00")
    d.claim_order(oid, "dev-carol-new", at="2026-11-26T20:14:00")
    # Deliberately no sign_order_receipt(...).

    d.start_pending_change("chg-carol-draft", "acct-carol", "dev-carol-old",
                           "1", at="2026-11-26T19:40:00")

    out: dict[str, str] = {"order_id": oid}
    out["old_via_takeover"] = expect_error(
        "DRAFT_NOT_CARRIED",
        lambda: d.apply_pending_change(
            "chg-carol-draft", device_id="dev-carol-old",
            via_order_id=oid, at="2026-11-26T20:30:00"))
    out["old_direct"] = expect_error(
        "CHANGE_UNCONFIRMED",
        lambda: d.apply_pending_change(
            "chg-carol-draft", device_id="dev-carol-old",
            at="2026-11-26T20:31:00"))
    out["old_sign_order"] = expect_error(
        "WRONG_DEVICE",
        lambda: d.sign_order_receipt(
            oid, "dev-carol-old", at="2026-11-26T20:32:00"))
    out["new_before_sign"] = expect_error(
        "ORDER_NOT_EFFECTIVE",
        lambda: d.change_setting(
            "acct-carol", "dev-carol-new", "7", order_id=oid,
            at="2026-11-26T20:33:00"))

    # Correct continuation: new device signs, then may change the setting.
    d.sign_order_receipt(oid, "dev-carol-new", at="2026-11-26T20:36:00")
    rev = d.change_setting("acct-carol", "dev-carol-new", "7",
                           order_id=oid, at="2026-11-26T20:40:00")
    out["new_after_sign"] = f"committed revision {rev}: auto_lock_minutes=7"

    # The old draft is still pending and still cannot land without its own
    # confirmation; it was not overwritten or carried by takeover.
    out["old_after_takeover"] = expect_error(
        "CHANGE_UNCONFIRMED",
        lambda: d.apply_pending_change(
            "chg-carol-draft", device_id="dev-carol-old",
            at="2026-11-26T20:41:00"))
    return out


def seed_eric_sync_family(d: Any) -> str:
    """Tablet and synced phone sign on different devices but one source root."""
    d.add_account("acct-eric", "Eric Example",
                  note="平板/同步手机各签一次，只算一个源头", at=T0)
    d.add_device("dev-eric-tablet", "acct-eric", "Eric 平板",
                 role="tablet", sync_root="root-eric-sync-family", at=T0)
    d.add_device("dev-eric-phone", "acct-eric", "Eric 由平板同步的手机",
                 role="phone", sync_root="root-eric-sync-family", at=T0)

    d.add_source("src-eric-tablet", "acct-eric", "device_key",
                 "平板本地钥匙", root_id="root-eric-sync-family", at=T0)
    d.add_source("src-eric-phone", "acct-eric", "synced_device",
                 "从平板同步出的手机钥匙", root_id="root-eric-sync-family", at=T0)
    d.link_source("src-eric-phone", "src-eric-tablet", "synced_copy_of",
                  note="手机由平板同步", at=T0)

    # Also show a credential reissue that traces to an old master key.
    d.add_source("src-eric-oldkey", "acct-eric", "old_key",
                 "2025 旧主钥匙", at=T0)
    d.add_source("src-eric-laptop", "acct-eric", "device_key",
                 "补发后的笔电钥匙", at=T0)
    d.add_credential("cred-eric-oldkey", "acct-eric", "src-eric-oldkey",
                     "旧主钥匙凭据", at=T0)
    d.reissue_credential("cred-eric-laptop", "acct-eric", "src-eric-laptop",
                         ["cred-eric-oldkey"], "笔电新凭据",
                         note="补发时登记依赖旧主钥匙", at=T0)

    d.submit_application("app-eric-duplicate", "acct-eric",
                         new_device_id="dev-eric-phone",
                         deadline=DEADLINE, at=T0)
    d.add_confirmation("cnf-eric-tablet", "acct-eric", "src-eric-tablet",
                      application_id="app-eric-duplicate",
                      device_id="dev-eric-tablet", signer="Eric 平板", at="2026-11-26T20:05:00")
    d.add_confirmation("cnf-eric-phone", "acct-eric", "src-eric-phone",
                      application_id="app-eric-duplicate",
                      device_id="dev-eric-phone", signer="Eric 同步手机", at="2026-11-26T20:06:00")
    err = expect_error("NO_INDEPENDENT_PAIR",
                       lambda: d.issue_order("app-eric-duplicate",
                                             "ord-eric-duplicate",
                                             at="2026-11-26T20:10:00"))
    d.audit("program", "order.issue", "rejected", account_id="acct-eric",
            error_code="NO_INDEPENDENT_PAIR",
            detail={"application_id": "app-eric-duplicate", "reason": err},
            at="2026-11-26T20:10:00")
    return err


def build_dana_base(d: Any) -> str:
    """Three qualified proofs; program freezes paper + device key."""
    d.add_account("acct-dana", "Dana Example",
                  note="三条合格证明与同一笔已办成接管的两种后续", at=T0)
    d.add_device("dev-dana-old", "acct-dana", "Dana 旧设备", role="old", at=T0)
    d.add_device("dev-dana-new", "acct-dana", "Dana 新设备", role="new", at=T0)

    d.add_source("src-dana-paper", "acct-dana", "paper_booklet",
                 "随人出差的 D-7 纸册", risk_score=20,
                 note="实物随持有人出差，存在报失风险", at=T0)
    d.add_source("src-dana-witness", "acct-dana", "witness_desk",
                 "晚间换班 W-3 见证席", risk_score=45,
                 note="当班记录可能在明早前被更正", at=T0)
    d.add_source("src-dana-key", "acct-dana", "device_key",
                 "可留到次晨的 K-17 设备钥匙", risk_score=0,
                 valid_until="2026-11-27T09:00:00", at=T0)
    d.add_source("src-dana-oldkey", "acct-dana", "old_key",
                 "K-17 补发所依赖的旧钥匙根", risk_score=0, at=T0)

    d.add_credential("cred-dana-paper", "acct-dana", "src-dana-paper",
                     "纸册骑缝码", at=T0)
    d.add_credential("cred-dana-witness", "acct-dana", "src-dana-witness",
                     "晚班见证记录凭据", at=T0)
    d.add_credential("cred-dana-oldkey", "acct-dana", "src-dana-oldkey",
                     "旧钥匙凭据", at=T0)
    d.reissue_credential("cred-dana-key", "acct-dana", "src-dana-key",
                         ["cred-dana-oldkey"], "次晨有效设备钥匙凭据",
                         valid_until="2026-11-27T09:00:00", at=T0)

    d.submit_application("app-dana", "acct-dana",
                         old_device_id="dev-dana-old",
                         new_device_id="dev-dana-new",
                         deadline=DEADLINE, at=T0)
    d.add_confirmation("cnf-dana-paper", "acct-dana", "src-dana-paper",
                      application_id="app-dana",
                      credential_id="cred-dana-paper",
                      signer="出差持有人 + 纸册", at="2026-11-26T20:05:00")
    d.add_confirmation("cnf-dana-witness", "acct-dana", "src-dana-witness",
                      application_id="app-dana",
                      credential_id="cred-dana-witness",
                      signer="晚间换班见证席", at="2026-11-26T20:06:00")
    d.add_confirmation("cnf-dana-key", "acct-dana", "src-dana-key",
                      application_id="app-dana",
                      credential_id="cred-dana-key",
                      device_id="dev-dana-new",
                      signer="K-17 设备钥匙", at="2026-11-26T20:07:00")

    oid = d.issue_order("app-dana", "ord-dana",
                        at="2026-11-26T20:10:00")
    d.claim_order(oid, "dev-dana-new", at="2026-11-26T20:15:00")
    d.sign_order_receipt(oid, "dev-dana-new", at="2026-11-26T20:20:00")
    d.change_setting("acct-dana", "dev-dana-new", "10",
                     order_id=oid, at="2026-11-26T20:25:00")
    return oid


def _snapshot_base(main_d: Any) -> Path:
    base_dir = Path(main_d.path).resolve().parent
    snap_dir = base_dir / "snapshots"
    replay_dir = base_dir / "replay"
    snap_dir.mkdir(parents=True, exist_ok=True)
    replay_dir.mkdir(parents=True, exist_ok=True)
    snap = snap_dir / "dana-base.db"
    if snap.exists():
        snap.unlink()
    main_d.conn.commit()
    target = sqlite3.connect(str(snap))
    try:
        main_d.conn.backup(target)
    finally:
        target.close()
    return snap


def run_dana_replays(main_d: Any) -> list[dict[str, Any]]:
    """Replay both alternative overnight reports from the same completed base."""
    from recovery_desk import Desk

    snap = _snapshot_base(main_d)
    specs = [
        {
            "id": "paper-loss",
            "file": "dana-paper-loss.db",
            "event": "2026-11-26T23:05:00",
            "source": "src-dana-paper",
            "report": "23:05 收到纸册报失，纸册来源撤销",
            "revoke_reason": "traveler reported booklet lost",
        },
        {
            "id": "witness-correction",
            "file": "dana-witness-correction.db",
            "event": "2026-11-27T01:10:00",
            "source": "src-dana-witness",
            "report": "01:10 收到当班见证记录更正，见证来源撤销",
            "revoke_reason": "shift record correction withdraws attestation",
        },
    ]

    results: list[dict[str, Any]] = []
    for spec in specs:
        replay_dir = Path(main_d.path).resolve().parent / "replay"
        replay_dir.mkdir(parents=True, exist_ok=True)
        branch_path = replay_dir / spec["file"]
        if branch_path.exists():
            branch_path.unlink()
        shutil.copyfile(snap, branch_path)
        bd = Desk(branch_path)
        result: dict[str, Any] = {
            "id": spec["id"],
            "branch_db": str(branch_path.relative_to(ROOT))
            if str(branch_path).startswith(str(ROOT)) else str(branch_path),
            "future_event": spec["report"],
            "base_order_id": "ord-dana",
        }
        try:
            bd.revoke_source(spec["source"], spec["revoke_reason"],
                             actor="overnight-intake", at=spec["event"])

            # The new device tries to do real work under yesterday's order.
            try:
                bd.change_setting("acct-dana", "dev-dana-new", "5",
                                  order_id="ord-dana",
                                  at=spec["event"].replace(":00", ":10")
                                  if spec["id"] == "paper-loss"
                                  else "2026-11-27T01:15:00")
                result["frozen_order_setting_allowed"] = True
                result["frozen_order_error"] = ""
            except DeskError as exc:
                result["frozen_order_setting_allowed"] = False
                result["frozen_order_error"] = str(exc)

            order_state = bd.conn.execute(
                "SELECT state FROM orders WHERE id='ord-dana'"
            ).fetchone()["state"]
            result["frozen_order_state"] = order_state

            # Always attempt to repair yesterday's order with the discarded
            # third proof.  It must fail in both branches.
            backfill_err = expect_error(
                "THIRD_PROOF_DISCARDED",
                lambda: bd.backfill_evidence(
                    "ord-dana", "cnf-dana-witness",
                    at="2026-11-27T07:05:00"))
            result["backfill_allowed"] = False
            result["backfill_error"] = backfill_err

            result["remedy_order_id"] = None
            result["remedy_setting"] = None

            if spec["id"] == "paper-loss":
                # Selected paper was revoked.  A fresh in-person ceremony is
                # still possible before the 08:00 deadline.
                decision_at = "2026-11-27T07:15:00"
                remedy_at = "2026-11-27T07:20:00"
                complete_at = "2026-11-27T07:40:00"
                decision = bd.decide_in_person_remedy(
                    "ord-dana", at=decision_at, person_present=True)
                if not decision["new_ceremony_allowed"]:
                    raise AssertionError("截止前本人到场时应当允许新当面换证")
                result["remedy_decision"] = decision
                bd.add_source(
                    "src-dana-inperson", "acct-dana", "in_person",
                    "07:20 本人到场临时信任根 IP-27",
                    root_id="root-inperson-20261127-0720", risk_score=0,
                    note="新的当面核验根，不继承报失纸册的信任", at=remedy_at)
                # Administrative replacement is recorded, but excluded from
                # trust lineage because fresh presence is the trust basis.
                bd.reissue_credential(
                    "cred-dana-inperson", "acct-dana",
                    "src-dana-inperson", ["cred-dana-paper"],
                    "当面换发凭据", dependency_kind="replaces",
                    note="本人重新到场；仅行政替换旧纸册凭据", at=remedy_at)
                bd.submit_application(
                    "app-dana-remedy", "acct-dana",
                    old_device_id="dev-dana-old",
                    new_device_id="dev-dana-new", deadline=DEADLINE,
                    replacement_for_order_id="ord-dana",
                    person_present=True,
                    note="纸册报失后本人到场新申请", at=remedy_at)
                bd.add_confirmation(
                    "cnf-dana-inperson", "acct-dana",
                    "src-dana-inperson",
                    application_id="app-dana-remedy",
                    credential_id="cred-dana-inperson",
                    device_id="dev-dana-new",
                    signer="Dana 本人到场", at="2026-11-27T07:25:00")
                bd.add_confirmation(
                    "cnf-dana-key-remedy", "acct-dana",
                    "src-dana-key",
                    application_id="app-dana-remedy",
                    credential_id="cred-dana-key",
                    device_id="dev-dana-new",
                    signer="次晨仍有效的 K-17 钥匙",
                    at="2026-11-27T07:26:00")
                remedy_oid = bd.issue_order(
                    "app-dana-remedy", "ord-dana-remedy", at="2026-11-27T07:30:00")
                bd.claim_order(remedy_oid, "dev-dana-new", at="2026-11-27T07:33:00")
                bd.sign_order_receipt(remedy_oid, "dev-dana-new", at=complete_at)
                rev = bd.change_setting(
                    "acct-dana", "dev-dana-new", "5",
                    order_id=remedy_oid, at="2026-11-27T07:45:00")
                result["remedy_order_id"] = remedy_oid
                result["remedy_setting"] = f"revision {rev}: auto_lock_minutes=5"
                result["summary"] = (
                    "纸册报失命中接管单固化证据：原单暂停，不能用已丢弃的见证记录补签；"
                    "07:20 本人重新到场并立新单后，新机在 08:00 截止前恢复办事。"
                )
            else:
                no_remedy = bd.decide_in_person_remedy(
                    "ord-dana", at="2026-11-27T01:20:00", person_present=False)
                if no_remedy["needed"]:
                    raise AssertionError("未选中的见证来源被撤，不应要求换证")
                result["remedy_decision"] = no_remedy
                result["summary"] = (
                    "见证更正只撤销未选用且已销毁材料的第三来源；"
                    "接管单固化的纸册+设备钥匙仍有效且独立，新机可继续办事，无需换证。"
                )

            result["branch_revisions"] = [
                dict(r) for r in bd.history_rows()
                if r["account_id"] == "acct-dana"
            ]
        finally:
            bd.close()

        selected = main_d.conn.execute(
            "SELECT decision_json FROM orders WHERE id='ord-dana'"
        ).fetchone()["decision_json"]
        chosen = jloads(selected)["chosen_source_ids"]
        main_d.conn.execute(
            """INSERT OR REPLACE INTO replay_runs(
                   id, base_order_id, branch_db, future_event, selected_pair,
                   setting_allowed, backfill_allowed, remedy_order_id,
                   summary, at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (spec["id"], "ord-dana", result["branch_db"],
             result["future_event"], ",".join(chosen),
             1 if result["frozen_order_setting_allowed"] else 0,
             0, result.get("remedy_order_id"), result["summary"], now_iso()),
        )
        main_d.conn.commit()
        results.append(result)
    return results


def run_demo(db_path: str | Path) -> int:
    from recovery_desk import Desk

    print("== 本地账号恢复柜台：桌面演练 ==")
    with Desk(db_path).reset() as d:
        seed_alice(d)
        print("[1/5] alice：两条独立来源已办成，auto_lock_minutes 30 -> 5")

        _, bob_err = seed_bob_sync_copy(d)
        print("[2/5] bob：首次接管成功；改成同步副本后拒绝新接管：")
        print("      " + bob_err.split(":")[0])

        carol = seed_carol_unsigned(d)
        print("[3/5] carol：仅领单未签收；旧机搭车/直接办理、旧机签收、新机改设置均被拒")
        print("      新机签收后成功；旧草稿仍保持 pending")

        eric_err = seed_eric_sync_family(d)
        print("[4/5] eric：平板与同步手机各签一次，但共享源头，不能组成两份证明")
        print("      " + eric_err.split(":")[0])

        build_dana_base(d)
        print("[5/5] dana：三条合格证明中，程序选择纸册 + 设备钥匙，并销毁未选见证材料")
        replays = run_dana_replays(d)
        for r in replays:
            allowed = "允许" if r["frozen_order_setting_allowed"] else "暂停"
            print(f"      replay/{r['id']}: 原单改设置={allowed}; {r['summary']}")

        problems = d.verify()
        if problems:
            print("校验失败：")
            for p in problems:
                print(" -", p)
            return 1

        print("\n== 已实际提交的设置修订（重启 SQLite 后仍可复查） ==")
        for r in d.history_rows():
            print(f"  #{r['seq']:>2} {r['at']} {r['account_id']}: "
                  f"{r['key']} {r['old_value']} -> {r['new_value']} "
                  f"via {r['device_id']} ({r['reason']})")

        print("\n== 命令 ==")
        print("  ./desk status")
        print("  ./desk history")
        print("  ./desk graph --format mermaid")
        print("  ./desk verify")
        print("  ./desk replays")
    return 0
