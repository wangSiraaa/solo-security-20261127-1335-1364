"""端到端本地演练：合格组合读稿、换件重放、失联重放和接班人查询。"""

from __future__ import annotations

import copy
from typing import Any

from .model import (
    DeliveryProofRequired,
    DeskUnreachable,
    FreshAttestationRequired,
    OpenDenied,
)
from .world import DrillWorld


WIDTH = 92


def rule(char: str = "─") -> None:
    print(char * WIDTH)


def heading(text: str) -> None:
    print("\n" + "=" * WIDTH)
    print(text)
    print("=" * WIDTH)


def subheading(text: str) -> None:
    print("\n" + text)
    print("-" * len(text))


def good(text: str) -> None:
    print(f"  [放行] {text}")


def denied(text: str) -> None:
    print(f"  [拒绝] {text}")


def info(text: str) -> None:
    print(f"  [信息] {text}")


def print_decision(prefix: str, decision: Any) -> None:
    print(f"  [{decision.code}] {prefix}{decision.summary}")
    for reason in decision.reasons:
        print(f"         - {reason}")


def print_successor_lookup(world: DrillWorld, heading_text: str = "接班人按稿号查询") -> None:
    subheading(heading_text)
    manuscript_no = world.order.manuscript_no
    records = world.desk.ledger.for_manuscript(manuscript_no)
    print(f"  稿号：{manuscript_no}    交付记录数：{len(records)}")
    if not records:
        denied("没有任何已验证的实际显示记录")
        return
    for record in records:
        print(
            "  "
            f"{record.delivery_id}｜订单 {record.order_id}｜显示版本 {record.displayed_version}\n"
            f"      正文 SHA256：{record.content_sha256}\n"
            f"      核验代次：{record.state_generation}\n"
            f"      核验单：{record.verification_id}｜组合：{record.combination_id}\n"
            f"      终端：{record.terminal_id}｜显示会话：{record.session_id}"
        )
    latest = records[-1]
    good(
        f"接班人查到最近一次真正显示的是 {latest.displayed_version}，"
        f"钉住核验代次 {latest.state_generation}"
    )


def print_boot_and_policy(world: DrillWorld) -> None:
    heading("0. 本地演练组件：虚拟终端 / 状态核验器 / 借稿台")
    info("证明类别 FICTIONAL_LOCAL_DEVICE_DOUBLE：本地设备替身只能签发虚构证明。")
    assert world.boot_result is not None
    assert world.boot_receipt is not None
    print_decision("开机核验：", world.boot_result)
    boot = world.boot_receipt.payload
    print(
        f"  开机回执：{boot['receipt_id']}｜启动代次 {boot['boot_generation']}｜"
        f"开机模块摘要 {boot['module_digest'][:20]}…"
    )
    print("  开机时加载模块：")
    for module in boot["modules"]:
        print(f"    - {module['coordinate']} ({module['kind']}) {module['measurement'][:24]}…")

    subheading("管理员策略要点")
    for combo in world.policy.combinations:
        core = ", ".join(sorted(combo.core_coordinates))
        print(f"  * {combo.combination_id}: {core}")
        print(f"      交互要求：{combo.interaction_requirements}")
    info("FONT-KAI@9.0.0 与 RENDERER-XIN@6.1.0 均单独合格，但不存在二者的批准组合。")


def run_golden_path(world: DrillWorld):
    heading("1. 已获准组合：借稿询问绑定本次 nonce，合格终端真正读到稿")

    grant = world.complete_loan()
    assert world.initial_loan_result is not None
    proof = grant.proof.payload
    good(
        f"借稿询问 {proof['inquiry_id']} / nonce {proof['nonce']} 已由设备替身证明 "
        f"{proof['proof_id']} 绑定；旧询问回条不能顶替。"
    )
    print_decision("借稿核验：", world.initial_loan_result)
    good(f"借稿凭据 {grant.token.payload['grant_id']} 已办成；其范围={grant.token.payload['scope']}。")
    info("该凭据只用于请求一次新的开稿取证，本身不含底稿正文。")

    outcome = world.open_and_display(grant)
    good(
        f"开稿新鲜证明 {outcome.proof.payload['proof_id']} 绑定 {outcome.inquiry.inquiry_id}；"
        f"核验单 {outcome.verification.verification_id} 通过。"
    )
    good(
        f"借稿台释放 {outcome.release.payload['release_id']}，终端显示会话 "
        f"{outcome.session.session_id}，并写入交付账 {outcome.delivery.delivery_id}。"
    )

    subheading("终端屏幕上实际显示的内容")
    print("┌" + "─" * (WIDTH - 1))
    for line in outcome.display_text.splitlines():
        print(f"│ {line}")
    print("└" + "─" * (WIDTH - 1))
    assert "夜航排字房密样第十四号" in outcome.display_text
    assert outcome.delivery.displayed_version == world.draft.version
    assert outcome.delivery.content_sha256 == world.draft.content_sha256

    print_successor_lookup(world, "2. 接班人凭稿号查询首次真实显示")
    return outcome


def run_swap_replay(base: DrillWorld) -> None:
    heading("3. 同一初始订单重放 A：班中换入两个单独合格、从未批准搭配的组件")
    world = copy.deepcopy(base)
    order = world.order
    grant = world.initial_grant
    assert grant is not None

    first_record = world.desk.ledger.for_manuscript(order.manuscript_no)[-1]
    pinned = world.terminal.continue_reading(order.order_id)
    good(
        f"换件前已打开的会话 {pinned.session_id} 继续钉住 {pinned.state_generation}；"
        "可显示同一已释放版本，不把正文交给新活跃代次重新渲染。"
    )

    state = world.swap_to_unapproved_pair()
    denied(
        f"已换入 FONT-KAI@9.0.0 + RENDERER-XIN@6.1.0（附件仍在）；"
        f"新活跃代次 {state.state_generation()}。"
    )
    info("开机回执不变，仍记录 BOOT-20261127-0001 的旧模块；不能拿开机通过代替当前核验。")

    subheading("3.1 旧询问回条顶替新询问：必须失败")
    new_inquiry = world.verifier.issue_inquiry(order, purpose="open-after-swap")
    old_proof = grant.proof
    old_bound = old_proof.payload
    replay_result = world.verifier.verify_for_order(old_proof, new_inquiry, order)
    assert not replay_result.ok
    assert replay_result.code == "INQUIRY_BINDING_MISMATCH"
    denied(
        f"旧证明 {old_bound['proof_id']} 绑定 {old_bound['inquiry_id']}/{old_bound['nonce']}，"
        f"不能用于新询问 {new_inquiry.inquiry_id}/{new_inquiry.nonce}：{replay_result.summary}"
    )

    subheading("3.2 原借据直接取正文：凭据为真也不是开稿能力")
    try:
        world.desk.present_credential_directly_for_body(grant)
        raise AssertionError("原借据竟可直接开稿")
    except FreshAttestationRequired as exc:
        denied(str(exc))

    subheading("3.3 用换件后的新鲜证明开稿：组合策略拒绝")
    before_count = len(world.desk.ledger.records)
    try:
        world.open_and_display(grant)
        raise AssertionError("未批准的组件组合竟可开稿")
    except OpenDenied as exc:
        assert exc.decision.code == "NO_APPROVED_COMBINATION"
        print_decision("", exc.decision)
    assert len(world.desk.ledger.records) == before_count
    denied("未产生正文释放，也未产生交付记录。")

    subheading("3.4 恢复批准核心组合，但撤走订单要求的隔屏阅读附件")
    restored = world.restore_approved_combo(with_attachment=False)
    info(f"当前代次 {restored.state_generation()}；FONT-SONG + RENDERER-PRESS 已恢复，附件缺失。")
    try:
        world.open_and_display(grant)
        raise AssertionError("缺少隔屏附件竟可开稿")
    except OpenDenied as exc:
        assert exc.decision.code == "MISSING_REQUIRED_ATTACHMENT"
        print_decision("", exc.decision)
    assert len(world.desk.ledger.records) == before_count

    subheading("3.5 环境证明即使证明核心组合合格，也不能记成本订单已交付")
    env_inquiry = world.verifier.issue_inquiry(order, purpose="environment-check")
    env_proof = world.terminal.answer_inquiry(env_inquiry)
    env_result = world.verifier.verify_environment_only(env_proof, env_inquiry)
    assert env_result.ok
    assert env_result.code == "ENVIRONMENT_APPROVED"
    print_decision("仅环境核验：", env_result)
    info("这张环境证明不检查/不替代本订单的显示交付回执。")
    try:
        world.desk.ledger.append_from_environment_certificate(
            manuscript_no=order.manuscript_no,
            environment_verification_id=(
                env_result.verification_id or first_record.verification_id
            ),
        )
        raise AssertionError("环境证明竟被写成交付记录")
    except DeliveryProofRequired as exc:
        denied(str(exc))
    assert len(world.desk.ledger.records) == before_count

    subheading("3.6 装回隔屏附件后才能重新取证并开稿")
    approved_state = world.restore_approved_combo(with_attachment=True)
    outcome = world.open_and_display(grant)
    assert outcome.verification.ok
    assert outcome.delivery.displayed_version == world.draft.version
    good(
        f"附件装回，当前代次 {approved_state.state_generation()}；"
        f"新交付 {outcome.delivery.delivery_id} 固定到 {outcome.delivery.state_generation}。"
    )
    assert len(world.desk.ledger.records) == before_count + 1
    print_successor_lookup(world, "换件分支结束后，接班人按稿号查到的版本/代次")


def run_disconnect_replay(base: DrillWorld) -> None:
    heading("4. 同一初始订单重放 B：短暂失联")
    world = copy.deepcopy(base)
    order = world.order
    grant = world.initial_grant
    assert grant is not None

    pinned = world.terminal.continue_reading(order.order_id)
    world.disconnect()
    subheading("4.1 断线期间：已打开的可继续，刚到的开稿请求不能新开")
    good(
        f"已打开会话 {pinned.session_id} 本地继续显示，仍钉住 {pinned.state_generation}，"
        "不需要联系借稿台。"
    )
    info("所选为“借稿台在真正开稿时交正文”的在线方案；终端没有可离线兑现的正文包。")

    before_count = len(world.desk.ledger.records)
    try:
        world.open_and_display(grant)
        raise AssertionError("断线期间竟联系上借稿台")
    except DeskUnreachable as exc:
        denied(str(exc))
    assert len(world.desk.ledger.records) == before_count
    denied("断线期间的新开请求没有生成核验单、正文释放或交付记录。")

    subheading("4.2 短暂失联结束：恢复连接后可重新取证并新开")
    world.reconnect()
    outcome = world.open_and_display(grant)
    assert outcome.verification.ok
    good(
        f"恢复连接后，新询问 {outcome.inquiry.inquiry_id} 与新鲜证明 "
        f"{outcome.proof.payload['proof_id']} 通过，新增交付 {outcome.delivery.delivery_id}。"
    )
    assert len(world.desk.ledger.records) == before_count + 1
    print_successor_lookup(world, "失联分支结束后，接班人按稿号查询")


def run_demo() -> int:
    print("本地虚拟阅稿演练：新鲜证明 / 组合策略 / 开稿能力归属 / 交付账")
    print("本演练全部使用 Python 标准库；所有“设备证明”均为明确标记的虚构本地证明。")

    world = DrillWorld.standard()
    print_boot_and_policy(world)
    run_golden_path(world)

    # 以“已获准组合、已办成借稿、已真实读到第一份稿”的同一世界快照重放两个后续。
    snapshot = copy.deepcopy(world)
    run_swap_replay(snapshot)
    run_disconnect_replay(snapshot)

    heading("结论")
    print("  1. 开稿能力归借稿台/状态核验路径：原借据只标识订单，不能直接取正文。")
    print("  2. 已打开阅读固定在当时核验过的组件代次，可继续该钉住会话；新开会话必须重新取证。")
    print("  3. 换入两个单独合格但未批准搭配的组件时，新鲜证明也过不了组合/交互策略。")
    print("  4. 恢复批准组合但缺少订单指定附件时仍拒绝；环境证明永远不能替代显示交付回执。")
    print("  5. 在线方案遇到短暂断线时，已打开内容可继续，新到请求无法新开；恢复后可补办。")
    good("全部端到端断言通过。")
    return 0
