# -*- coding: utf-8 -*-
"""离线器材互助库端到端剧情（零第三方依赖，python3 demo.py 直接运行）。

剧情时间线
----------
0. 四家寄存行各自生成合成身份，对 4 镜身 + 4 转接环全部盖足额章。
1. 两个客户提交需要成对器材的普通单；实际使用日此时未知。
2. 托管席按最不利同时兑付核定：同一日两套保函必须用互不相同的物理实体。
   ——先展示一个“章都足额但 distinct 实物不足”的反例，再回到健康库存通过核定。
3. 把某机构第二张收据改成同一实物别名 -> distinct 数下降 -> 覆盖组改判 under。
4. 恢复收据 -> 重新 covered；兑付日客户 B 先取走一只转接环。
5. 客户 A 在柜台离线时，只能凭预留交接范围取件；范围外/双花一律拒办。
6. 客户用独立配套图亲自装接并查回出库件；公告板只有锚点与 nullifier，无名册。
"""
from __future__ import annotations

import json

from crypto import Identity, self_test
from model import BODY, RING, BankReceipt, PairedOrder, SyntheticItem
from escrow import COVERED, UNDER_COVERED, EscrowSeat, OfflineCounter
from client import ClientView

BANKS = ["bank-north", "bank-south", "bank-east", "bank-west"]
EXP = "2026-12-31"


def hr(title):
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def current_group(escrow):
    """始终读托管席当前组指针，避免在 groups 字典里取到历史陈旧组。"""
    return escrow.groups[escrow.current_group_id]


def build_items():
    """4 镜身 + 4 转接环；镜座/公差设计成需要替代镜座才能凑齐两套。"""
    bodies = [
        SyntheticItem("sid-B1", BODY, "M1", 1.5, EXP, "bank-north"),
        SyntheticItem("sid-B2", BODY, "M2", 2.0, EXP, "bank-south"),
        SyntheticItem("sid-B3", BODY, "M1", 2.5, EXP, "bank-east"),
        SyntheticItem("sid-B4", BODY, "M3", 1.0, EXP, "bank-west"),
    ]
    rings = [
        SyntheticItem("sid-R1", RING, "M1", 1.8, EXP, "bank-north"),
        # R2 公差 2.5：超过 B 的 2.0 上限，但在 A 的 2.5 上限内
        SyntheticItem("sid-R2", RING, "M2", 2.5, EXP, "bank-south"),
        SyntheticItem("sid-R3", RING, "M1", 2.0, EXP, "bank-east"),
        # R4 是 B（上限 2.0）唯一公差达标的 M2 转接环
        SyntheticItem("sid-R4", RING, "M2", 1.2, EXP, "bank-west"),
    ]
    return bodies, rings


def make_receipt(item: SyntheticItem, bank: Identity) -> BankReceipt:
    return BankReceipt(
        sid=item.sid, bank=bank.label, kind=item.kind, mount=item.mount,
        tolerance_um=item.tolerance_um, expires_on=item.expires_on,
        bank_pub=bank.ed_pub.hex()).sign(bank)


def main():
    self_test()
    hr("0. 密码学原语自检通过（Ed25519/X25519/ChaCha20/HKDF 官方向量）")

    seat = Identity.generate("escrow-seat")
    banks = {name: Identity.generate(name) for name in BANKS}
    escrow = EscrowSeat(seat)
    for b in banks.values():
        escrow.register_bank(b)

    bodies, rings = build_items()
    all_items = bodies + rings
    # 物理实体标识：健康状态下 sid 与物理实体一一对应
    physical = {it.sid: f"phys-{it.sid}" for it in all_items}

    hr("1. 四家寄存行对全部 8 个部件盖足额章（每家至少一件，故四家都‘足额’）")
    for it in all_items:
        rcpt = make_receipt(it, banks[it.bank])
        escrow.deposit_receipt(rcpt, physical[it.sid])
    print("已登记收据数:", len(escrow.receipts),
          "| distinct 镜身:", escrow.groups and
          current_group(escrow)["distinct_bodies"],
          "| distinct 转接环:",
          current_group(escrow)["distinct_rings"])

    # ------------------------------------------------------------------
    # 反例：章都足额，但别名让 distinct 实物不足以同时覆盖两套
    # ------------------------------------------------------------------
    hr("2. 反例——‘四家都盖足额章’不等于两套保函能同时兑现")
    client_a = Identity.generate("client-A")
    client_b = Identity.generate("client-B")
    order_a = PairedOrder(
        "order-A", client_a.x_pub.hex(), client_a.ed_pub.hex(),
        preferred_mount="M1", allowed_mounts=["M1", "M2"],
        max_tolerance_um=2.5, need_before=EXP)
    order_b = PairedOrder(
        "order-B", client_b.x_pub.hex(), client_b.ed_pub.hex(),
        preferred_mount="M2",
        # B 只接受 M2：R4 是其唯一公差达标的 M2 环，别名 R4->R2 即击穿
        allowed_mounts=["M2"],
        max_tolerance_um=2.0, need_before=EXP)
    escrow.submit_order(order_a)
    escrow.submit_order(order_b)
    healthy_group = current_group(escrow)
    print("健康库存核定状态:", healthy_group["status"])
    print("最不利同时兑付分配:")
    print(json.dumps(healthy_group["allocation"], ensure_ascii=False, indent=2))

    # 人为制造别名：把 west 行第二张收据（sid-R4，B 唯一公差达标的 M2 环）
    # 指向 R2 的同一物理实体，B 便找不到合格 M2 转接环
    print("\n-- 别名攻击：sid-R4 的物理对应被改成 phys-sid-R2 --")
    escrow.alias_receipt_to("sid-R4", physical["sid-R2"])
    aliased_group = current_group(escrow)
    print("别名后核定状态:", aliased_group["status"],
          "| distinct 转接环:", aliased_group["distinct_rings"])
    assert aliased_group["status"] == UNDER_COVERED, "别名后必须改判 under"
    print("=> 原先有足额证明的一组保函被改判：收据仍各自有效，但 distinct 实物"
          "不足以在同一日同时履约（B 已无公差达标的 M2 转接环）。")

    hr("3. 恢复收据 -> 重新核定为 covered，并签发密封保函")
    escrow.restore_receipt("sid-R4", physical["sid-R4"])
    restored = current_group(escrow)
    assert restored["status"] == COVERED
    print("恢复后核定状态:", restored["status"])
    labels = list(escrow.guarantees.keys())
    label_a, label_b = labels[0], labels[1]
    sealed_a = escrow.issue_sealed_guarantee(label_a, client_a.x_pub.hex())
    sealed_b = escrow.issue_sealed_guarantee(label_b, client_b.x_pub.hex())

    bank_pubs = {name: b.ed_pub for name, b in banks.items()}
    view_a = ClientView(client_a, seat.ed_pub, bank_pubs)
    view_b = ClientView(client_b, seat.ed_pub, bank_pubs)
    doc_a = view_a.open_guarantee(sealed_a)
    doc_b = view_b.open_guarantee(sealed_b)
    print("客户 A 私有视图:", json.dumps(view_a.my_private_view(),
                                        ensure_ascii=False, indent=2))
    print("客户 B 只能看到自己的标签:", doc_b["label"],
          "；看不到 A 的订单/分配")

    hr("4. 公告板公开数据检查：只有锚点、状态、nullifier、组根，无名册/sid")
    public = escrow.bulletin.public_view()
    serialized = json.dumps(public, ensure_ascii=False)
    for leak in ["client-A", "client-B", "sid-", "phys-", "order-A", "order-B"]:
        assert leak not in serialized, f"公开数据暗附名册: {leak}"
    print(json.dumps(public, ensure_ascii=False, indent=2))
    print("名册/sid/物理实体泄漏检查: 全部通过")

    # ------------------------------------------------------------------
    # 兑付日：使用日无法提前知道；客户 B 先取走一只转接环
    # ------------------------------------------------------------------
    hr("5. 兑付日（使用日当天才确定）：B 先取走一只 M2 转接环")
    use_day = "2026-11-30"
    redeem_b = escrow.redeem(label_b, use_day, chosen_alt_mount="M2",
                             client_identity=client_b)
    manifest_b = redeem_b["manifest"]
    print("B 出库: 镜身", manifest_b["body_sid"],
          "+ 转接环", manifest_b["ring_sid"])
    report_b = view_b.verify_manifest(
        json.loads(json.dumps(manifest_b)), order_b)
    print("B 独立核验报告:", json.dumps(report_b, ensure_ascii=False))
    assert report_b["ok"]
    taken_ring = manifest_b["ring_sid"]
    print("当日已交走物理实体:", sorted(escrow.spent_today[use_day]))

    hr("6. 仍有效保函如何换件兑现：A 在扣除已交走部件后重新匹配替代镜座")
    # A 首选 M1；当日 R1 若被 B 占用，则用替代镜座/其余部件重组
    redeem_a = escrow.redeem(label_a, use_day, chosen_alt_mount="M1",
                             client_identity=client_a)
    manifest_a = redeem_a["manifest"]
    print("A 出库: 镜身", manifest_a["body_sid"],
          "+ 转接环", manifest_a["ring_sid"])
    assert manifest_a["ring_sid"] != taken_ring, "同一实物不得同日重复履约"
    report_a = view_a.verify_manifest(
        json.loads(json.dumps(manifest_a)), order_a)
    print("A 独立核验报告:", json.dumps(report_a, ensure_ascii=False))
    assert report_a["ok"]
    print("两套器材使用的物理实体互不相同:")
    print("  B:", manifest_b["body_sid"], manifest_b["ring_sid"])
    print("  A:", manifest_a["body_sid"], manifest_a["ring_sid"])

    # 双花拒绝
    try:
        escrow.redeem(label_a, use_day, "M1", client_a)
        raise AssertionError("重复兑付必须被拒绝")
    except ValueError as e:
        print("双花拒绝:", e)

    # ------------------------------------------------------------------
    # 离线柜台：只能按已预留交接范围办
    # ------------------------------------------------------------------
    hr("7. 柜台离线：只认托管席预签的交接范围，超范围/篡改/双花一律拒办")
    # A/B 已各取走一套；剩余 B3(M1)+R3(M1) 等部件。第三单的离线范围据此核定。
    client_c = Identity.generate("client-C")
    order_c = PairedOrder(
        "order-C", client_c.x_pub.hex(), client_c.ed_pub.hex(),
        preferred_mount="M3", allowed_mounts=["M3", "M1", "M2"],
        max_tolerance_um=2.5, need_before=EXP)
    label_c = escrow.submit_order(order_c)
    assert escrow.guarantees[label_c]["status"] == COVERED, \
        "剩余 B3+R3 应能覆盖第三单"
    scope = escrow.issue_offline_scope(label_c, use_day)
    view_c = ClientView(client_c, seat.ed_pub, bank_pubs)
    assert view_c.verify_offline_scope(scope), "客户可独立验 scope 签名"
    print("预留交接范围（描述符仅含允许交付部件）:")
    print(json.dumps({k: v for k, v in scope.items()
                      if k != "seat_signature"}, ensure_ascii=False, indent=2))

    # 离线柜台只持托管席公钥 + scope 副本 + 本地花费集，全程不连托管席
    counter = OfflineCounter(seat.ed_pub)
    body_sid = scope["allowed_body_sids"][0]
    ring_sid = scope["allowed_ring_sids"][0]

    # 7.1 正常离线兑付：凭 scope 内描述符本地过配套图
    receipt = counter.redeem(scope, use_day, body_sid, ring_sid)
    print("\n[7.1] 离线兑付成功:", receipt)

    # 7.2 同一份 scope 再用一次：本地 nullifier 双花拒绝
    try:
        counter.redeem(scope, use_day, body_sid, ring_sid)
        raise AssertionError("离线双花必须拒绝")
    except PermissionError as e:
        print("[7.2] 离线双花拒办:", e)

    # 7.3 范围外部件（B4 不在 scope 允许镜身内）
    try:
        counter.redeem(scope, use_day, "sid-B4", ring_sid)
        raise AssertionError("范围外镜身必须拒绝")
    except PermissionError as e:
        print("[7.3] 范围外拒办:", e)

    # 7.4 篡改 scope：把允许环换成范围外 sid 并破坏签名
    tampered = json.loads(json.dumps(scope))
    tampered["allowed_ring_sids"] = ["sid-R2"]
    tampered["descriptors"] = [d for d in tampered["descriptors"]
                               if d["sid"] != "sid-R3"]
    try:
        counter.redeem(tampered, use_day, body_sid, "sid-R2")
        raise AssertionError("篡改 scope 必须在验签处拒绝")
    except PermissionError as e:
        print("[7.4] 篡改签名拒办:", e)

    # 7.5 回连同步：离线花费的 nullifier 进入托管席公告板
    synced = counter.sync_back(escrow)
    assert all(n in escrow.bulletin.spent_nullifiers for n in synced)
    print("[7.5] 回连同步 nullifier:", synced,
          "| 公告板 spent_count =", escrow.bulletin.public_view()["spent_count"])

    hr("8. 结论")
    print("四家寄存行的足额章只对各自收据负责；真正决定两套借来器材能否同时")
    print("换回实物的，是托管席按‘同一履约日 + distinct 物理实体’做的最不利")
    print("同时兑付核定。别名会降低 distinct 实物数并触发改判；恢复后柜台依据")
    print("当日已交走部件与装配公差重新匹配替代镜座，离线时只按预留范围交付。")


if __name__ == "__main__":
    main()
