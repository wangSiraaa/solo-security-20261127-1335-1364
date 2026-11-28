# -*- coding: utf-8 -*-
"""离线器材互助库的确定性单元测试：python3 test_suite.py。"""
from __future__ import annotations

import json

from crypto import Identity, sealed_box_open, self_test
from model import (BODY, RING, BankReceipt, PairedOrder, SyntheticItem)
from escrow import COVERED, UNDER_COVERED, EscrowSeat, OfflineCounter
from client import ClientView

EXP = "2026-12-31"
DAY = "2026-11-30"


def _seat_with_inventory(rings_override=None):
    seat = Identity.generate("seat")
    banks = {n: Identity.generate(n)
             for n in ("bn", "bs", "be", "bw")}
    e = EscrowSeat(seat)
    for b in banks.values():
        e.register_bank(b)
    bodies = [
        SyntheticItem("B1", BODY, "M1", 1.5, EXP, "bn"),
        SyntheticItem("B2", BODY, "M2", 2.0, EXP, "bs"),
        SyntheticItem("B3", BODY, "M1", 2.5, EXP, "be"),
        SyntheticItem("B4", BODY, "M3", 1.0, EXP, "bw"),
    ]
    rings = rings_override or [
        SyntheticItem("R1", RING, "M1", 1.8, EXP, "bn"),
        SyntheticItem("R2", RING, "M2", 2.5, EXP, "bs"),
        SyntheticItem("R3", RING, "M1", 2.0, EXP, "be"),
        SyntheticItem("R4", RING, "M2", 1.2, EXP, "bw"),
    ]
    items = bodies + rings
    phys = {i.sid: "phys-" + i.sid for i in items}
    for i in items:
        rcpt = BankReceipt(
            i.sid, i.bank, i.kind, i.mount, i.tolerance_um, i.expires_on,
            banks[i.bank].ed_pub.hex()).sign(banks[i.bank])
        e.deposit_receipt(rcpt, phys[i.sid])
    return e, seat, banks, phys


def _order(cid, mounts, tol, oid):
    c = Identity.generate(cid)
    return c, PairedOrder(oid, c.x_pub.hex(), c.ed_pub.hex(),
                          mounts[0], mounts, tol, EXP)


def test_crypto_kat():
    self_test()  # 内部已含 Ed25519/X25519/ChaCha20 官方向量与篡改检测


def test_healthy_two_orders_covered_with_distinct_physical():
    e, *_ = _seat_with_inventory()
    ca, oa = _order("A", ["M1", "M2"], 2.5, "OA")
    cb, ob = _order("B", ["M2"], 2.0, "OB")
    e.submit_order(oa)
    e.submit_order(ob)
    g = e.groups[e.current_group_id]
    assert g["status"] == COVERED
    used = set()
    for plan in g["allocation"].values():
        for b, r in plan["pairs"]:
            assert b not in used and r not in used
            used.add(b)
            used.add(r)
    # 两套单共消耗 4 个互不相同的 sid
    assert len(used) == 4


def test_insufficient_distinct_physical_is_under():
    # 只有 1 镜身 + 1 环的库存，却开两张单 -> under
    e, seat, banks, phys = _seat_with_inventory()
    # 把 B2/B3/B4 全部别名到 B1 同一物理实体
    for sid in ("B2", "B3", "B4"):
        e.alias_receipt_to(sid, phys["B1"])
    ca, oa = _order("A", ["M1", "M2"], 2.5, "OA")
    cb, ob = _order("B", ["M1", "M2"], 2.5, "OB")
    e.submit_order(oa)
    e.submit_order(ob)
    assert e.groups[e.current_group_id]["status"] == UNDER_COVERED


def test_alias_then_restore_cycle():
    e, *_, phys = _seat_with_inventory()
    ca, oa = _order("A", ["M1", "M2"], 2.5, "OA")
    cb, ob = _order("B", ["M2"], 2.0, "OB")
    la, lb = e.submit_order(oa), e.submit_order(ob)
    assert e.guarantees[la]["status"] == COVERED
    e.alias_receipt_to("R4", phys["R2"])   # B 唯一合格 M2 环被别名
    assert e.guarantees[la]["status"] == UNDER_COVERED
    e.restore_receipt("R4", phys["R4"])
    assert e.guarantees[la]["status"] == COVERED


def test_same_physical_cannot_redeem_twice_same_day():
    e, seat, banks, phys = _seat_with_inventory()
    ca, oa = _order("A", ["M1", "M2"], 2.5, "OA")
    cb, ob = _order("B", ["M2"], 2.0, "OB")
    la, lb = e.submit_order(oa), e.submit_order(ob)
    e.redeem(lb, DAY, "M2", cb)
    e.redeem(la, DAY, "M1", ca)
    # 当日已花费物理实体数 = 4（两套各两件，互不重叠）
    assert len(e.spent_today[DAY]) == 4
    # nullifier 双花
    try:
        e.redeem(la, DAY, "M1", ca)
        raise AssertionError("应拒绝双花")
    except ValueError:
        pass


def test_sealed_guarantee_is_client_scoped_and_bulletin_has_no_roster():
    e, seat, banks, _ = _seat_with_inventory()
    ca, oa = _order("A", ["M1"], 2.5, "OA")
    cb, ob = _order("B", ["M2"], 2.0, "OB")
    la, lb = e.submit_order(oa), e.submit_order(ob)
    sealed_a = e.issue_sealed_guarantee(la, ca.x_pub.hex())
    # B 无法打开 A 的密封信封
    try:
        sealed_box_open(cb, sealed_a["box"])
        raise AssertionError("B 不应能打开 A 的保函")
    except ValueError:
        pass
    plain = sealed_box_open(ca, sealed_a["box"])
    doc = json.loads(plain)
    assert doc["order_id"] == "OA" and "OB" not in plain.decode()
    # 公告板无名册/sid/物理实体
    pub = json.dumps(e.bulletin.public_view(), ensure_ascii=False)
    for leak in ("sid-", "phys-", "OA", "OB"):
        assert leak not in pub


def test_offline_counter_scope_tamper_and_double_spend():
    e, seat, banks, _ = _seat_with_inventory()
    cc, oc = _order("C", ["M3", "M1", "M2"], 2.5, "OC")
    lc = e.submit_order(oc)
    scope = e.issue_offline_scope(lc, DAY)
    counter = OfflineCounter(seat.ed_pub)
    b, r = scope["allowed_body_sids"][0], scope["allowed_ring_sids"][0]
    ok = counter.redeem(scope, DAY, b, r)
    assert ok["offline"] is True
    # 双花
    try:
        counter.redeem(scope, DAY, b, r)
        raise AssertionError
    except PermissionError:
        pass
    # 篡改签名
    bad = json.loads(json.dumps(scope))
    bad["max_tolerance_um"] = 99.0
    try:
        counter.redeem(bad, DAY, b, r)
        raise AssertionError
    except PermissionError:
        pass
    # 回连同步
    synced = counter.sync_back(e)
    assert all(n in e.bulletin.spent_nullifiers for n in synced)
    assert lc in e.spent_labels and e.guarantees[lc]["status"] == "spent"


def test_client_independent_manifest_verification():
    e, seat, banks, _ = _seat_with_inventory()
    ca, oa = _order("A", ["M1", "M2"], 2.5, "OA")
    la = e.submit_order(oa)
    sealed = e.issue_sealed_guarantee(la, ca.x_pub.hex())
    view = ClientView(ca, seat.ed_pub, {n: b.ed_pub for n, b in banks.items()})
    view.open_guarantee(sealed)
    out = e.redeem(la, DAY, "M1", ca)
    report = view.verify_manifest(json.loads(json.dumps(out["manifest"])), oa)
    assert report["ok"]
    # 篡改出库单一个 sid 后必须验不过
    tampered = json.loads(json.dumps(out["manifest"]))
    tampered["ring_sid"] = "R9"
    report2 = view.verify_manifest(tampered, oa)
    assert not report2["ok"]


def _run():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"\n全部 {len(tests)} 项测试通过")


if __name__ == "__main__":
    _run()
