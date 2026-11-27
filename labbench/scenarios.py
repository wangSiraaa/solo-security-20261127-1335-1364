from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from .errors import AuthorizationDenied, BindingMismatch
from .identity import HolderDevice
from .lab import COMMON_NAME, Lab
from .store import new_id

Logger = Callable[[str], None]


def _no_log(_: str) -> None:
    pass


def issue_correct_bundle(
    lab: Lab, cast: dict[str, Any], card_ids: list[str], bench_id: str, nonce: str
) -> dict[str, Any]:
    if lab.mode == "holder":
        return cast["protagonist"].issue_bundle(
            card_ids, bench_id=bench_id, nonce=nonce, oracle=lab.oracle
        )
    return lab.registry.issue_bundle(
        cast["link_id"], card_ids, bench_id=bench_id, nonce=nonce
    )


def issue_presence_only(
    lab: Lab, cast: dict[str, Any], *, bench_id: str, nonce: str, presentation_id: str
) -> dict[str, Any]:
    if lab.mode == "holder":
        return cast["protagonist"].issue_presence_proof(
            bench_id=bench_id,
            nonce=nonce,
            oracle=lab.oracle,
            presentation_id=presentation_id,
        )
    return lab.registry.issue_presence_proof(
        cast["link_id"],
        bench_id=bench_id,
        nonce=nonce,
        presentation_id=presentation_id,
    )


def expect_denied(action: Callable[[], Any], label: str, log: Logger) -> str:
    try:
        action()
    except AuthorizationDenied as exc:
        log(f"    拒绝符合预期：{label} —— {exc}")
        return str(exc)
    raise AssertionError(f"{label} 必须被实验台拒绝")


def privacy_audit(lab: Lab, result: dict[str, Any]) -> list[str]:
    """检查学校导出和公开回单中是否含跨校/跨台关联字段。"""
    root = lab.root
    school_a_text = (root / "exports" / "school_a_export.json").read_text(encoding="utf-8")
    school_b_text = (root / "exports" / "school_b_export.json").read_text(encoding="utf-8")
    public_text = "\n".join(
        json.dumps(r, ensure_ascii=False, sort_keys=True) for r in lab.receipt_book.all()
    )

    a_card = result["a_card"]["card_id"]
    b_card = result["b_card"]["card_id"]
    classmate_card = result["classmate_b_card"]["card_id"]
    forbidden_in_a = [
        b_card,
        classmate_card,
        "B-2046",
        "B-2099",
        "school_b",
        "bench_a",
        "bench_b",
        result.get("token_a", ""),
        result.get("token_b", ""),
    ]
    forbidden_in_b = [
        a_card,
        "A-1001",
        "school_a",
        "bench_a",
        "bench_b",
        result.get("token_a", ""),
        result.get("token_b", ""),
    ]
    for marker in forbidden_in_a:
        if marker and marker in school_a_text:
            raise AssertionError(f"学校 A 导出包含其无权看到的标记：{marker}")
    for marker in forbidden_in_b:
        if marker and marker in school_b_text:
            raise AssertionError(f"学校 B 导出包含其无权看到的 A 校标记：{marker}")

    sensitive_public_markers = [
        COMMON_NAME,
        "张伟",
        "A-1001",
        "B-2046",
        "B-2099",
        a_card,
        b_card,
        classmate_card,
        "bench_a",
        "bench_b",
        result.get("seat_a_id", ""),
        result.get("seat_b_id", ""),
        result.get("link_id", ""),
        result.get("holder_id", ""),
        result.get("token_a", ""),
        result.get("token_b", ""),
        result.get("handoff_code", ""),
    ]
    for marker in sensitive_public_markers:
        if marker and marker in public_text:
            raise AssertionError(f"公开回单包含可追踪身份/行程的标记：{marker}")

    # 学校 B 当然能看到本校两张真卡；这里确认攻击素材确实存在，而不是靠作废假卡取胜。
    b_export = json.loads(school_b_text)
    exported_cards = b_export["student_records"]
    if b_card not in exported_cards or classmate_card not in exported_cards:
        raise AssertionError("学校 B 导出中缺少两张均真实有效的 B 校卡")

    return ["school_a_export_minimal", "school_b_export_minimal", "public_receipts_unlinkable"]


def run_core_scenario(
    root: str | Path,
    *,
    mode: str = "holder",
    reset: bool = True,
    log: Logger = _no_log,
) -> dict[str, Any]:
    log(f"\n=== 核心场景 / 合作模式：{mode} ===")
    lab = Lab.create(root, mode=mode, reset=reset)
    cast = lab.seed_named_cast()
    a_card = cast["a_card"]
    b_card = cast["b_card"]
    classmate_b_card = cast["classmate_b_card"]
    correct_ids = [a_card["card_id"], b_card["card_id"]]
    mixed_ids = [a_card["card_id"], classmate_b_card["card_id"]]

    holder_id = cast["protagonist"].holder_id if cast["protagonist"] else None

    # 1. 本台确认两项齐备、同一主体，并开席。
    nonce_a = lab.bench_a.create_challenge("open")
    bundle_a = issue_correct_bundle(lab, cast, correct_ids, "bench_a", nonce_a)
    seat_a, token_a = lab.bench_a.open_seat(
        bundle_a,
        resolve_card=lab.resolve_card,
        verify_identity=lab.verify_identity,
        receipt_book=lab.receipt_book,
    )
    log(f"[1] A 台开席：{seat_a['seat_id']}，模式={mode}，两张卡和本次到场均已确认")

    # 2. 操作本地样件，完成前两步后真实中断。
    lab.bench_a.start_operation(token_a)
    event1, seat_a = lab.bench_a.operate_next(token_a)
    event2, seat_a = lab.bench_a.operate_next(token_a)
    log(f"[2] A 台完成工序：{event1['code']} -> {event2['code']}")
    seat_a = lab.bench_a.interrupt_operation(token_a, "演示紧急/主动中断")
    if seat_a["status"] != "interrupted":
        raise AssertionError("中断后席位状态应为 interrupted")
    handoff_code, handoff_view = lab.bench_a.create_handoff_receipt(token_a, "bench_b")
    if handoff_view["remaining_steps"] != 2:
        raise AssertionError("检查点应保留两道剩余工序")
    log(f"[3] A 台已中断并开出未结操作回单：剩余 {handoff_view['remaining_steps']} 步")

    # 3. 攻击：第二项换成同名同学的真 B 校卡。
    for cid in mixed_ids:
        card = lab.resolve_card(cid)
        if not card or card["status"] != "active":
            raise AssertionError("攻击用的两张卡都必须真实有效")
    binding_refused = False
    try:
        issue_correct_bundle(lab, cast, mixed_ids, "bench_b", lab.bench_b.create_challenge("open"))
    except BindingMismatch:
        binding_refused = True
    if not binding_refused:
        raise AssertionError("异主卡不应能形成同一人证明")

    nonce_swap = lab.bench_b.create_challenge("open")
    attack_pid = new_id("attack-presentation")
    attack_presence = issue_presence_only(
        lab,
        cast,
        bench_id="bench_b",
        nonce=nonce_swap,
        presentation_id=attack_pid,
    )
    attack_bundle = {
        "mode": mode,
        "nonce": nonce_swap,
        "card_ids": sorted(mixed_ids),
        "same_proof": {"proof_id": new_id("forged-proof"), "presentation_id": attack_pid},
        "presence_proof": attack_presence,
    }
    seats_before = len(lab.bench_b.read_state()["seats"])
    expect_denied(
        lambda: lab.bench_b.open_seat(
            attack_bundle,
            resolve_card=lab.resolve_card,
            verify_identity=lab.verify_identity,
            receipt_book=lab.receipt_book,
        ),
        "同名同学真卡拼入第二资格",
        log,
    )
    seats_after = len(lab.bench_b.read_state()["seats"])
    if seats_before != seats_after:
        raise AssertionError("攻击被拒后不得产生操作席")
    log("[4] 两张单独有效但异主的卡 + 相同姓名，不能开席；实验台不使用姓名判同")

    # 4. 旧台到场证明不能挪到新台。
    cross_nonce_a = lab.bench_a.create_challenge("presence-only")
    cross_pid = new_id("cross-presentation")
    cross_proof_a = issue_presence_only(
        lab,
        cast,
        bench_id="bench_a",
        nonce=cross_nonce_a,
        presentation_id=cross_pid,
    )
    fresh_b_nonce = lab.bench_b.create_challenge("presence-audit")
    cross_claims = {"presentation_id": cross_pid}
    moved_to_b = lab.verifier_oracle.verify_one(
        cross_proof_a,
        proof_type="presence",
        bench_id="bench_b",
        nonce=fresh_b_nonce,
        claims=cross_claims,
    )
    if moved_to_b:
        raise AssertionError("A 台到场证明不得在 B 台成立")
    still_valid_at_a = lab.verifier_oracle.verify_one(
        cross_proof_a,
        proof_type="presence",
        bench_id="bench_a",
        nonce=cross_nonce_a,
        claims=cross_claims,
    )
    if not still_valid_at_a:
        raise AssertionError("A 台到场证明本应在指定 A 台成立")
    log("[5] A 台到场证明改投 B 台仅得到 False；票据本身在 A 台有效，证明受众绑定正确")

    # 5. 恢复原卡：攻击请求没有产生席位或消费原证明；随后走旧台未结回单接手。
    log("[5b] 已恢复主角原卡 B-2046；混合卡请求只留下拒绝记录，未改变学籍和席位")
    nonce_handoff = lab.bench_b.create_challenge("handoff")
    handoff_bundle = issue_correct_bundle(
        lab, cast, correct_ids, "bench_b", nonce_handoff
    )
    missing_presence_bundle = deepcopy(handoff_bundle)
    missing_presence_bundle["presence_proof"] = {
        "proof_id": new_id("missing-presence"),
        "presentation_id": handoff_bundle["same_proof"]["presentation_id"],
    }
    expect_denied(
        lambda: lab.bench_b.accept_handoff(
            handoff_code,
            missing_presence_bundle,
            old_bench=lab.bench_a,
            resolve_card=lab.resolve_card,
            verify_identity=lab.verify_identity,
            receipt_book=lab.receipt_book,
        ),
        "凭旧回单接手但缺少 B 台本次到场依据",
        log,
    )
    old_receipt_status = lab.bench_a.read_state()["handoff_receipts"][handoff_view["receipt_id"]]["status"]
    if old_receipt_status != "unsettled":
        raise AssertionError("缺少到场依据时不应消费旧台回单")

    seat_b, token_b = lab.bench_b.accept_handoff(
        handoff_code,
        handoff_bundle,
        old_bench=lab.bench_a,
        resolve_card=lab.resolve_card,
        verify_identity=lab.verify_identity,
        receipt_book=lab.receipt_book,
    )
    if seat_b["source"]["type"] != "handoff" or seat_b["operation"]["sample"]["next_step"] != 2:
        raise AssertionError("B 台未正确继承 A 台检查点")
    log("[6] 补齐 B 台本次同持/到场依据后接手成功，检查点和未结工序已迁移")

    # 旧回单重放也必须失败。
    replay_nonce = lab.bench_b.create_challenge("handoff")
    replay_bundle = issue_correct_bundle(lab, cast, correct_ids, "bench_b", replay_nonce)
    expect_denied(
        lambda: lab.bench_b.accept_handoff(
            handoff_code,
            replay_bundle,
            old_bench=lab.bench_a,
            resolve_card=lab.resolve_card,
            verify_identity=lab.verify_identity,
            receipt_book=lab.receipt_book,
        ),
        "同一旧台未结回单重放",
        log,
    )
    seat_a_exit, exit_a_receipt = lab.bench_a.exit_seat(token_a, lab.receipt_book)
    _, exit_a_receipt_again = lab.bench_a.exit_seat(token_a, lab.receipt_book)
    if (
        seat_a_exit["status"] != "closed"
        or exit_a_receipt["receipt_id"] != exit_a_receipt_again["receipt_id"]
    ):
        raise AssertionError("旧台交席后退席应成功且可幂等重办")
    log("    旧台在新台接手后退席成功，重复退席返回同一回单")

    # 6. 在 B 台继续操作同一个本地样件并完成。
    lab.bench_b.resume_operation(token_b)
    completed = False
    last_event: dict[str, Any] | None = None
    for _ in range(4):
        last_event, seat_b = lab.bench_b.operate_next(token_b)
        if seat_b["operation"]["status"] == "completed":
            completed = True
            break
    if not completed:
        raise AssertionError("接手后应能完成剩余工序")
    readings = seat_b["operation"]["sample"]["readings"]
    if len(readings) != 4 or [r["step_index"] for r in readings] != [0, 1, 2, 3]:
        raise AssertionError("样件工序没有从检查点连续执行")
    log(f"[7] B 台完成剩余工序：{last_event['code'] if last_event else '?'}；样件累计值="
        f"{seat_b['operation']['sample']['accumulator']}")

    # 7. 退席可重办，重复退席返回同一回单。
    seat_b, exit_receipt = lab.bench_b.exit_seat(token_b, lab.receipt_book)
    seat_b_again, exit_receipt_again = lab.bench_b.exit_seat(token_b, lab.receipt_book)
    if seat_b["status"] != "closed" or exit_receipt["receipt_id"] != exit_receipt_again["receipt_id"]:
        raise AssertionError("重复退席应幂等返回原回单")
    log(f"[8] 退席成功且可安全重办：{exit_receipt['receipt_id']}（重试返回同一回单）")

    # 8. 借台可重办：重新取号、重新示证、再开一席。
    nonce_reborrow = lab.bench_b.create_challenge("open")
    reborrow_bundle = issue_correct_bundle(lab, cast, correct_ids, "bench_b", nonce_reborrow)
    seat_c, token_c = lab.bench_b.open_seat(
        reborrow_bundle,
        resolve_card=lab.resolve_card,
        verify_identity=lab.verify_identity,
        receipt_book=lab.receipt_book,
    )
    lab.bench_b.exit_seat(token_c, lab.receipt_book)
    lab.bench_b.exit_seat(token_c, lab.receipt_book)
    log(f"[9] 借台可重新办理：新席 {seat_c['seat_id']} 已开、已退，重复退席幂等")

    # 9. 普通结业发证可按同一业务键安全重办。
    repeated_a = lab.graduate(
        "school_a",
        "A-1001",
        COMMON_NAME,
        holder=cast["protagonist"],
        course="高压实操甲班",
        idempotency_key="A-1001:practical-2026",
    )
    if repeated_a["card_id"] != a_card["card_id"]:
        raise AssertionError("相同结业业务重办应返回同一有效卡")
    log("[10] 普通结业按幂等键重办返回原卡，不制造重复结业关系")

    # 10. 换卡：学校保留换卡记录；登记处模式更正关联，持卡器模式由同一持卡器重绑。
    new_a_card = lab.reissue_card(
        a_card["card_id"], holder=cast["protagonist"], link_id=cast["link_id"]
    )
    old_a_card = lab.resolve_card(a_card["card_id"])
    if old_a_card["status"] != "replaced":
        raise AssertionError("旧卡应标记为 replaced")
    if lab.mode == "registry":
        link = lab.registry.find_by_account("school_a", "A-1001")
        if link["school_accounts"]["school_a"]["card_id"] != new_a_card["card_id"]:
            raise AssertionError("登记处关联未更正到新卡")
        log("[11] 换卡后登记处保留更正历史，并把当前关联指向新卡")
    else:
        if new_a_card["card_id"] not in cast["protagonist"].store.read()["bindings"]:
            raise AssertionError("新卡未绑定到原持卡器")
        log("[11] 换卡后新卡由当事人在同一持卡器重绑；学校只看到本校句柄")

    # 11. 分别导出两校有权看到的记录，以及登记处自己的关联记录。
    lab.export_schools(lab.root / "exports")
    lab.export_registry(lab.root / "exports" / "registry_only_export.json")

    result = {
        "lab": lab,
        "mode": mode,
        "a_card": a_card,
        "b_card": b_card,
        "classmate_b_card": classmate_b_card,
        "new_a_card": new_a_card,
        "link_id": cast["link_id"],
        "holder_id": holder_id,
        "seat_a_id": seat_a["seat_id"],
        "seat_b_id": seat_b["seat_id"],
        "seat_c_id": seat_c["seat_id"],
        "token_a": token_a,
        "token_b": token_b,
        "token_c": token_c,
        "handoff_code": handoff_code,
        "exit_receipt_id": exit_receipt["receipt_id"],
    }
    checks = privacy_audit(lab, result)
    audit_report = {
        "passed": True,
        "checks": checks,
        "public_receipt_count": len(lab.receipt_book.all()),
        "school_exports": [
            str(lab.root / "exports" / "school_a_export.json"),
            str(lab.root / "exports" / "school_b_export.json"),
        ],
    }
    (lab.root / "exports" / "privacy_audit.json").write_text(
        json.dumps(audit_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    log("[12] 导出与隐私审计通过：" + "、".join(checks))
    return result


def run_holder_loss_demo(root: str | Path, *, log: Logger = _no_log) -> dict[str, Any]:
    log("\n=== 持卡器彻底遗失且无备份场景 ===")
    lab = Lab.create(root, mode="holder", reset=True)
    holder = HolderDevice.create(lab.root, label="lost-only-device")
    a_card = lab.graduate(
        "school_a", "A-7788", "李雷", holder=holder, idempotency_key="A-7788:course"
    )
    b_card = lab.graduate(
        "school_b", "B-3321", "李雷", holder=holder, idempotency_key="B-3321:course"
    )
    ids = [a_card["card_id"], b_card["card_id"]]

    nonce = lab.bench_a.create_challenge("open")
    bundle = holder.issue_bundle(ids, bench_id="bench_a", nonce=nonce, oracle=lab.oracle)
    if not lab.verify_identity(bundle, ids, "bench_a", nonce):
        raise AssertionError("遗失前同持证明应成立")

    holder_id = holder.holder_id
    holder_secret = holder.secret
    holder.destroy_without_backup()
    lost = False
    try:
        HolderDevice.unlock(lab.root, holder_id, holder_secret)
    except AuthorizationDenied:
        lost = True
    if not lost:
        raise AssertionError("设备文件删除后应无法解锁")
    if lab.registry.recover_cross_school_link("school_a", "A-7788") is not None:
        raise AssertionError("持卡器模式下登记处不应能恢复跨校关联")
    if lab.registry.recover_cross_school_link("school_b", "B-3321") is not None:
        raise AssertionError("持卡器模式下登记处不应能恢复跨校关联")
    log("[L1] 持卡器彻底遗失且无备份后，旧关联无法补开；登记处查询两校账号均无连接线")

    replacement_holder = HolderDevice.create(lab.root, label="replacement-after-identity-check")
    new_a = lab.reissue_card(a_card["card_id"], holder=replacement_holder)
    new_b = lab.reissue_card(b_card["card_id"], holder=replacement_holder)
    new_ids = [new_a["card_id"], new_b["card_id"]]
    nonce2 = lab.bench_a.create_challenge("open")
    bundle2 = replacement_holder.issue_bundle(
        new_ids, bench_id="bench_a", nonce=nonce2, oracle=lab.oracle
    )
    seat, token = lab.bench_a.open_seat(
        bundle2,
        resolve_card=lab.resolve_card,
        verify_identity=lab.verify_identity,
        receipt_book=lab.receipt_book,
    )
    lab.bench_a.exit_seat(token, lab.receipt_book)
    log("[L2] 两校核验本人后分别补卡，学员用新持卡器重办绑定；新席可开可退")
    lab.export_schools(lab.root / "exports")
    lab.export_registry(lab.root / "exports" / "registry_only_export.json")
    return {"lab": lab, "seat_id": seat["seat_id"], "new_ids": new_ids}
